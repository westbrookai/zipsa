"""Tests for the skill's pyproject.toml parser.

The Hybrid Phases runtime replaces `manifest.yaml` with the PEP 621
`[project]` table plus a `[tool.zipsa]` section. Per-phase overrides
live under `[tool.zipsa.phases."<id>"]`.

See `docs/zipsa-runtime-spec-2026-06-11.md` §1.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zipsa.core.pyproject import (
    Limits,
    PhaseOverride,
    ProjectInfo,
    PyprojectError,
    PyprojectMeta,
    ZipsaConfig,
    load_pyproject,
)


def _make_skill(root: Path, pyproject_content: str) -> Path:
    (root / "zipsa-dist").mkdir(parents=True)
    (root / "zipsa-dist" / "pyproject.toml").write_text(pyproject_content)
    return root


class TestMinimalPyproject:
    """A valid skill needs only [project] basics + [tool.zipsa].description."""

    def test_loads_minimal(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "hello-world"
version = "0.1.0"

[tool.zipsa]
description = "Smoke test"
""")

        meta = load_pyproject(skill)

        assert isinstance(meta, PyprojectMeta)
        assert meta.project.name == "hello-world"
        assert meta.project.version == "0.1.0"
        assert meta.zipsa.description == "Smoke test"

    def test_credentials_default_empty(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
description = "y"
""")

        meta = load_pyproject(skill)

        assert meta.zipsa.credentials == []

    def test_schedule_default_none(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
description = "y"
""")

        meta = load_pyproject(skill)

        assert meta.zipsa.schedule is None

    def test_max_run_depth_default_3(self, tmp_path):
        """Default cap on recursive run_staging_skill depth (spec §8.2)."""
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
description = "y"
""")

        meta = load_pyproject(skill)

        assert meta.zipsa.max_run_depth == 3


class TestFullPyproject:
    """All recognized fields populated end to end."""

    def test_loads_full_config(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "morning-notion-log"
version = "0.2.0"
description = "Summarize yesterday's work into Notion"
requires-python = ">=3.12"
dependencies = ["notion-client>=2.0", "anthropic>=0.30"]

[tool.zipsa]
description = "Summarize yesterday's work into Notion"
credentials = ["notion"]
schedule = "0 8 * * *"
allows_staging_run = true
max_run_depth = 5

[tool.zipsa.limits]
max_cost_usd = 1.0
timeout_seconds = 600
""")

        meta = load_pyproject(skill)

        assert meta.project.dependencies == ["notion-client>=2.0", "anthropic>=0.30"]
        assert meta.zipsa.credentials == ["notion"]
        assert meta.zipsa.schedule == "0 8 * * *"
        assert meta.zipsa.allows_staging_run is True
        assert meta.zipsa.max_run_depth == 5
        assert meta.zipsa.limits.max_cost_usd == 1.0
        assert meta.zipsa.limits.timeout_seconds == 600


class TestPhaseOverrides:
    """Per-phase config lives under [tool.zipsa.phases."<id>"]."""

    def test_phase_overrides_parsed(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "skill-builder"
version = "0.1.0"
[tool.zipsa]
description = "Author a skill"

[tool.zipsa.phases."2.gather"]
max_turns = 20
allowed_tools = ["mcp__zipsa__ask", "mcp__zipsa__confirm"]
cost_warn_threshold_usd = 0.5

[tool.zipsa.phases."3.author"]
max_turns = 30
allowed_tools = ["Read", "Write", "Glob"]
""")

        meta = load_pyproject(skill)

        assert "2.gather" in meta.zipsa.phases
        assert "3.author" in meta.zipsa.phases
        gather = meta.zipsa.phases["2.gather"]
        assert isinstance(gather, PhaseOverride)
        assert gather.max_turns == 20
        assert gather.allowed_tools == ["mcp__zipsa__ask", "mcp__zipsa__confirm"]
        assert gather.cost_warn_threshold_usd == 0.5
        assert meta.zipsa.phases["3.author"].max_turns == 30

    def test_no_phases_section_defaults_empty(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
description = "y"
""")

        meta = load_pyproject(skill)

        assert meta.zipsa.phases == {}

    def test_phase_override_model_spec(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
description = "y"

[tool.zipsa.phases."1.do"]
model = {name = "claude-sonnet-4-6"}
""")

        meta = load_pyproject(skill)

        do = meta.zipsa.phases["1.do"]
        assert do.model == {"name": "claude-sonnet-4-6"}


class TestErrors:
    """Cases that prevent the skill from loading."""

    def test_missing_pyproject_raises(self, tmp_path):
        skill = tmp_path / "no-pyproject"
        (skill / "zipsa-dist").mkdir(parents=True)

        with pytest.raises(PyprojectError, match="pyproject.toml"):
            load_pyproject(skill)

    def test_missing_zipsa_dist_raises(self, tmp_path):
        skill = tmp_path / "no-dist"
        skill.mkdir()

        with pytest.raises(PyprojectError, match="zipsa-dist"):
            load_pyproject(skill)

    def test_invalid_toml_raises(self, tmp_path):
        skill = _make_skill(tmp_path, "this is = not valid = toml\n")

        with pytest.raises(PyprojectError, match="parse"):
            load_pyproject(skill)

    def test_missing_project_name_raises(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
version = "0.1.0"
[tool.zipsa]
description = "y"
""")

        with pytest.raises(PyprojectError):
            load_pyproject(skill)

    def test_missing_zipsa_description_raises(self, tmp_path):
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
[tool.zipsa]
""")

        with pytest.raises(PyprojectError):
            load_pyproject(skill)

    def test_missing_tool_zipsa_raises(self, tmp_path):
        """[tool.zipsa] is required — a skill without it isn't a zipsa skill."""
        skill = _make_skill(tmp_path, """\
[project]
name = "x"
version = "0.1.0"
""")

        with pytest.raises(PyprojectError, match="tool.zipsa"):
            load_pyproject(skill)


class TestIgnoredLegacyKeys:
    """Old manifest fields silently ignored — runtime spec §1.1."""

    def test_legacy_keys_ignored(self, tmp_path):
        """apiVersion/kind/metadata/spec at top level mean nothing."""
        skill = _make_skill(tmp_path, """\
apiVersion = "zipsa.dev/v1alpha1"
kind = "Skill"

[project]
name = "x"
version = "0.1.0"

[tool.zipsa]
description = "y"

[metadata]
name = "ignored"

[spec]
purpose = "ignored"
""")

        meta = load_pyproject(skill)

        assert meta.project.name == "x"
        assert meta.zipsa.description == "y"
