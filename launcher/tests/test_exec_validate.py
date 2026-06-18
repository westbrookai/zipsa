"""Tests for static validation of exec-format skills (#159).

`validate_exec_skill` is the static pre-flight for exec skills: it checks
the metadata schema (via the loader), required frontmatter, phase
structure (via discover_phases), and PEP 723 block validity — collecting
ALL problems in one pass rather than failing fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zipsa.core.exec_validate import ExecValidation, validate_exec_skill


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _make_skill(
    root: Path,
    *,
    name: str | None = "demo",
    description: str | None = "Does a demo. Use when demoing.",
    version: str | None = "0.1.0",
    phases: dict[str, str] | None = None,
) -> Path:
    """Materialize an exec skill under root/<name or 'demo'> and return it."""
    skill_dir = root / (name or "demo")
    fm_lines = ["---"]
    if name is not None:
        fm_lines.append(f"name: {name}")
    if description is not None:
        fm_lines.append(f"description: {description}")
    fm_lines.append("---")
    _write(skill_dir / "SKILL.md", "\n".join(fm_lines) + "\n\n# skill\n")

    pkg_lines = []
    if version is not None:
        pkg_lines.append(f"version: {version}")
    pkg_lines.append("author: tester")
    _write(skill_dir / "zipsa" / "package.yaml", "\n".join(pkg_lines) + "\n")

    if phases is None:
        phases = {
            "1.fetch.py": "print('{}')\n",
            "2.report.md": "# report\n",
        }
    for fname, body in phases.items():
        _write(skill_dir / "scripts" / fname, body)
    return skill_dir


class TestValidExecSkill:
    def test_valid_skill_passes(self, tmp_path):
        skill_dir = _make_skill(tmp_path)
        report = validate_exec_skill(skill_dir)
        assert isinstance(report, ExecValidation)
        assert report.ok
        assert report.errors == []
        assert report.skill is not None
        assert report.skill.name == "demo"
        assert [p.id_str for p in report.phases] == ["1", "2"]

    def test_valid_pep723_block_passes(self, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            phases={
                "1.fetch.py": (
                    "# /// script\n"
                    '# dependencies = ["requests"]\n'
                    "# [tool.zipsa]\n"
                    "# timeout-seconds = 120\n"
                    "# ///\n"
                    "print('{}')\n"
                ),
            },
        )
        report = validate_exec_skill(skill_dir)
        assert report.ok, report.errors


class TestMetadataErrors:
    def test_missing_version_errors(self, tmp_path):
        skill_dir = _make_skill(tmp_path, version=None)
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("version" in e for e in report.errors)

    def test_missing_name_errors(self, tmp_path):
        skill_dir = _make_skill(tmp_path, name="demo")
        # Strip the name from frontmatter while keeping the dir name.
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: no name here\n---\n\n# skill\n"
        )
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("name" in e for e in report.errors)

    def test_missing_description_errors(self, tmp_path):
        skill_dir = _make_skill(tmp_path, description=None)
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("description" in e for e in report.errors)


class TestPhaseStructureErrors:
    def test_no_scripts_dir_errors(self, tmp_path):
        skill_dir = _make_skill(tmp_path)
        for f in (skill_dir / "scripts").iterdir():
            f.unlink()
        (skill_dir / "scripts").rmdir()
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("scripts" in e for e in report.errors)

    def test_no_phases_errors(self, tmp_path):
        skill_dir = _make_skill(tmp_path, phases={"helper.py": "x = 1\n"})
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("phase" in e.lower() for e in report.errors)

    def test_duplicate_phase_id_errors(self, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            phases={"1.fetch.py": "print('{}')\n", "1.other.py": "print('{}')\n"},
        )
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("duplicate" in e.lower() for e in report.errors)


class TestPep723Errors:
    def test_malformed_pep723_block_errors(self, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            phases={
                "1.fetch.py": (
                    "# /// script\n"
                    "# dependencies = [\n"  # unterminated array → invalid TOML
                    "# ///\n"
                    "print('{}')\n"
                ),
            },
        )
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert any("pep" in e.lower() or "toml" in e.lower() or "1.fetch.py" in e for e in report.errors)


class TestWarnings:
    def test_first_phase_md_warns_but_ok(self, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            phases={"1.greet.md": "# greet\n", "2.finish.py": "print('{}')\n"},
        )
        report = validate_exec_skill(skill_dir)
        assert report.ok
        assert any(".py" in w or "preflight" in w.lower() for w in report.warnings)


class TestCollectsAllErrors:
    def test_multiple_problems_all_reported(self, tmp_path):
        # Missing version AND missing description AND no phases — one pass.
        skill_dir = _make_skill(
            tmp_path, version=None, description=None, phases={"helper.py": "x=1\n"}
        )
        report = validate_exec_skill(skill_dir)
        assert not report.ok
        assert len(report.errors) >= 2


class TestValidateCliExecPath:
    """`zipsa validate <path>` dispatches to exec validation for exec skills."""

    def _run(self, args):
        from typer.testing import CliRunner

        from zipsa.cli import app

        return CliRunner().invoke(app, args)

    def test_valid_exec_skill_path_exits_zero(self, tmp_path):
        skill_dir = _make_skill(tmp_path)
        result = self._run(["validate", str(skill_dir)])
        assert result.exit_code == 0, result.output
        assert "is valid" in result.output
        assert "Phases: 2" in result.output

    def test_broken_exec_skill_path_exits_one(self, tmp_path):
        skill_dir = _make_skill(tmp_path, version=None, description=None)
        result = self._run(["validate", str(skill_dir)])
        assert result.exit_code == 1
        assert "Validation failed" in result.output
