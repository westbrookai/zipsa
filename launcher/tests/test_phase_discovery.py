"""Tests for filename-based phase discovery.

A skill's `zipsa-dist/` directory holds one file per phase. The filename
encodes the phase: `<dotted-int>.<slug>.{py,md}`. Phases sort by the
tuple of int parts (so `10` comes after `2`, and `3.1` comes between
`3` and `3.2`).

Files that don't match the pattern are ignored — that's how skills
ship helper modules alongside phases.

See `docs/zipsa-runtime-spec-2026-06-11.md` §1.2 for the discovery rule.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from zipsa.core.phase_discovery import (
    PHASE_EXTENSIONS,
    Phase,
    PhaseDiscoveryError,
    discover_phases,
)


def _make_skill(root: Path, files: dict[str, str]) -> Path:
    """Create a skill dir at `root` with `zipsa-dist/` populated by `files`.

    Returns the skill root.
    """
    (root / "zipsa-dist").mkdir(parents=True)
    for name, content in files.items():
        (root / "zipsa-dist" / name).write_text(content)
    (root / "SKILL.md").write_text("# test skill\n")
    return root


class TestPhaseDiscoveryHappyPaths:
    """Discovery returns the right phases in the right order."""

    def test_single_python_phase(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.preflight.py": "def run(c, p): return {}\n"})

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0] == Phase(
            id_tuple=(1,),
            id_str="1",
            slug="preflight",
            kind="py",
            path=skill / "zipsa-dist" / "1.preflight.py",
        )

    def test_single_llm_phase(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.gather.md": "# gather\n"})

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].kind == "md"
        assert phases[0].slug == "gather"

    def test_multiple_phases_in_order(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "1.preflight.py": "def run(c, p): return {}\n",
            "2.fetch.md": "# fetch\n",
            "3.write.py": "def run(c, p): return {}\n",
        })

        phases = discover_phases(skill)

        assert [p.id_str for p in phases] == ["1", "2", "3"]
        assert [p.kind for p in phases] == ["py", "md", "py"]

    def test_sub_phase_branching(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "1.preflight.py": "def run(c, p): return {}\n",
            "2.decide.md": "# decide\n",
            "3.1.fetch-from-db.py": "def run(c, p): return {}\n",
            "3.2.fetch-from-web.py": "def run(c, p): return {}\n",
            "4.done.py": "def run(c, p): return {}\n",
        })

        phases = discover_phases(skill)

        # 3.1 sits between 3 (none) and 4, and before 3.2.
        assert [p.id_str for p in phases] == ["1", "2", "3.1", "3.2", "4"]
        assert phases[2].slug == "fetch-from-db"
        assert phases[3].slug == "fetch-from-web"

    def test_numeric_ordering_not_lexicographic(self, tmp_path):
        """`10` must sort after `2`, not between `1` and `2`."""
        skill = _make_skill(tmp_path, {
            "1.a.py": "def run(c, p): return {}\n",
            "2.b.py": "def run(c, p): return {}\n",
            "10.c.py": "def run(c, p): return {}\n",
        })

        phases = discover_phases(skill)

        assert [p.id_str for p in phases] == ["1", "2", "10"]


class TestPhaseDiscoveryLanguages:
    """Phase 0 supports multiple languages via file extension."""

    def test_shell_phase_discovered(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.do.sh": "#!/bin/bash\necho hi\n"})

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].kind == "sh"
        assert phases[0].slug == "do"

    def test_typescript_phase_discovered(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.do.ts": "console.log('hi');\n"})

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].kind == "ts"

    def test_javascript_phase_discovered(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.do.js": "console.log('hi');\n"})

        phases = discover_phases(skill)

        assert phases[0].kind == "js"

    def test_go_phase_discovered(self, tmp_path):
        skill = _make_skill(tmp_path, {"1.do.go": "package main\nfunc main(){}\n"})

        phases = discover_phases(skill)

        assert phases[0].kind == "go"

    def test_extensions_constant_is_published(self):
        """exec_runner uses this set; keep it in one place."""
        assert "py" in PHASE_EXTENSIONS
        assert "md" in PHASE_EXTENSIONS
        assert "sh" in PHASE_EXTENSIONS
        assert "ts" in PHASE_EXTENSIONS
        assert "js" in PHASE_EXTENSIONS
        assert "go" in PHASE_EXTENSIONS


class TestPhaseDiscoveryIgnoredFiles:
    """Files that don't match the pattern are silently skipped."""

    def test_helper_module_ignored(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "1.do.py": "def run(c, p): return {}\n",
            "helper.py": "def util(): return 42\n",
            "_private.py": "INTERNAL = True\n",
        })

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].slug == "do"

    def test_readme_ignored(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "1.do.py": "def run(c, p): return {}\n",
            "README.md": "# readme\n",
            "notes.md": "stray notes\n",
        })

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].slug == "do"

    def test_invalid_slug_ignored(self, tmp_path):
        """Slug must be lower-case kebab; uppercase or punctuation rejects it."""
        skill = _make_skill(tmp_path, {
            "1.real.py": "def run(c, p): return {}\n",
            "2.UpperCase.py": "def run(c, p): return {}\n",
            "3.bad_underscore.py": "def run(c, p): return {}\n",
            "4.bad..py": "def run(c, p): return {}\n",
        })

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].slug == "real"

    def test_wrong_extension_ignored(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "1.real.py": "def run(c, p): return {}\n",
            "2.config.toml": "key = 'value'\n",
            "3.data.json": "{}",
        })

        phases = discover_phases(skill)

        assert len(phases) == 1
        assert phases[0].slug == "real"


class TestPhaseDiscoveryErrors:
    """Cases that prevent the skill from running."""

    def test_missing_zipsa_dist_raises(self, tmp_path):
        skill = tmp_path / "no-dist-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("# no dist\n")

        with pytest.raises(PhaseDiscoveryError, match="zipsa-dist"):
            discover_phases(skill)

    def test_empty_zipsa_dist_raises(self, tmp_path):
        skill = _make_skill(tmp_path, {})

        with pytest.raises(PhaseDiscoveryError, match="no phases"):
            discover_phases(skill)

    def test_only_ignored_files_raises(self, tmp_path):
        skill = _make_skill(tmp_path, {
            "helper.py": "pass\n",
            "README.md": "# readme\n",
        })

        with pytest.raises(PhaseDiscoveryError, match="no phases"):
            discover_phases(skill)

    def test_duplicate_phase_id_raises(self, tmp_path):
        """Two files with the same dotted id (regardless of slug or kind)."""
        skill = _make_skill(tmp_path, {
            "1.foo.py": "def run(c, p): return {}\n",
            "1.bar.md": "# bar\n",
        })

        with pytest.raises(PhaseDiscoveryError, match="duplicate"):
            discover_phases(skill)


class TestPhaseDiscoveryFirstPhaseWarning:
    """First-phase `.md` is allowed (for authoring tools) but logs a warning."""

    def test_first_phase_py_no_warning(self, tmp_path, caplog):
        skill = _make_skill(tmp_path, {"1.preflight.py": "def run(c, p): return {}\n"})

        with caplog.at_level(logging.WARNING, logger="zipsa.core.phase_discovery"):
            discover_phases(skill)

        assert not [r for r in caplog.records if "first phase" in r.getMessage().lower()]

    def test_first_phase_md_logs_warning(self, tmp_path, caplog):
        skill = _make_skill(tmp_path, {"1.gather.md": "# gather\n"})

        with caplog.at_level(logging.WARNING, logger="zipsa.core.phase_discovery"):
            phases = discover_phases(skill)

        # Discovery still succeeds and returns the phase.
        assert len(phases) == 1
        assert phases[0].kind == "md"
        # And it warned.
        assert any(
            "first phase" in r.getMessage().lower() and "md" in r.getMessage().lower()
            for r in caplog.records
        )


class TestTransitionWindow:
    """Discovery accepts the new `scripts/` layout and legacy `zipsa-dist/`.

    See `docs/superpowers/specs/2026-06-18-exec-skill-metadata.md`.
    """

    def _make_scripts_skill(self, root: Path, files: dict[str, str]) -> Path:
        (root / "scripts").mkdir(parents=True)
        for name, content in files.items():
            (root / "scripts" / name).write_text(content)
        (root / "SKILL.md").write_text("# test skill\n")
        return root

    def test_discovers_phases_in_scripts(self, tmp_path):
        skill = self._make_scripts_skill(
            tmp_path, {"1.fetch.py": "# fetch\n", "2.report.md": "# report\n"}
        )
        phases = discover_phases(skill)
        assert [p.slug for p in phases] == ["fetch", "report"]
        assert phases[0].path == skill / "scripts" / "1.fetch.py"

    def test_still_discovers_phases_in_zipsa_dist(self, tmp_path):
        # Legacy layout keeps working.
        (tmp_path / "zipsa-dist").mkdir(parents=True)
        (tmp_path / "zipsa-dist" / "1.fetch.py").write_text("# fetch\n")
        (tmp_path / "SKILL.md").write_text("# test skill\n")
        phases = discover_phases(tmp_path)
        assert [p.slug for p in phases] == ["fetch"]
        assert phases[0].path == tmp_path / "zipsa-dist" / "1.fetch.py"

    def test_scripts_preferred_over_zipsa_dist(self, tmp_path):
        # When both exist, scripts/ wins (it is the new canonical location).
        self._make_scripts_skill(tmp_path, {"1.new.py": "# new\n"})
        (tmp_path / "zipsa-dist").mkdir(parents=True)
        (tmp_path / "zipsa-dist" / "1.old.py").write_text("# old\n")
        phases = discover_phases(tmp_path)
        assert [p.slug for p in phases] == ["new"]
