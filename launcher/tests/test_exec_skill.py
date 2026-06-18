"""Tests for the exec-skill metadata loader (#156).

An exec-format skill carries two metadata layers:
  - SKILL.md YAML frontmatter (standard Agent Skills fields)
  - zipsa/package.yaml (zipsa-only sidecar)

`load_exec_skill` parses both into an `ExecSkill` model. Identity is
frontmatter `name` + package.yaml `version`.

See `docs/superpowers/specs/2026-06-18-exec-skill-metadata.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zipsa.core.exec_skill import (
    ExecSkill,
    ExecSkillError,
    Requirement,
    load_exec_skill,
)


def _make_skill(
    root: Path,
    *,
    frontmatter: str,
    package_yaml: str | None,
    scripts: dict[str, str] | None = None,
) -> Path:
    """Create a new-layout exec skill at `root`.

    `frontmatter` is the YAML body between the SKILL.md `---` fences.
    `package_yaml` (if not None) is written to `zipsa/package.yaml`.
    """
    root.mkdir(parents=True, exist_ok=True)
    skill_md = f"---\n{frontmatter}\n---\n\n# Skill body\n"
    (root / "SKILL.md").write_text(skill_md)
    scripts = scripts or {"1.fetch.py": "# fetch\n"}
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name, content in scripts.items():
        (root / "scripts" / name).write_text(content)
    if package_yaml is not None:
        (root / "zipsa").mkdir(parents=True, exist_ok=True)
        (root / "zipsa" / "package.yaml").write_text(package_yaml)
    return root


class TestLoadExecSkill:
    """Parsing a well-formed new-layout skill."""

    def test_parses_frontmatter_and_package(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter=(
                "name: weather\n"
                "description: Report the weather and WHEN to use it\n"
            ),
            package_yaml="version: 0.2.0\nauthor: westbrookai\n",
        )
        skill = load_exec_skill(root)
        assert isinstance(skill, ExecSkill)
        assert skill.name == "weather"
        assert skill.description == "Report the weather and WHEN to use it"
        assert skill.version == "0.2.0"
        assert skill.author == "westbrookai"

    def test_identity_is_name_and_version(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: x\n",
            package_yaml="version: 1.4.2\n",
        )
        skill = load_exec_skill(root)
        assert skill.name == "weather"
        assert skill.version == "1.4.2"

    def test_tags_and_limits(self, tmp_path):
        root = _make_skill(
            tmp_path / "transit",
            frontmatter="name: transit\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "tags: [transit, telegram]\n"
                "limits:\n"
                "  max_turns: 6\n"
                "  max_cost_usd: 0.05\n"
                "  timeout_seconds: 60\n"
            ),
        )
        skill = load_exec_skill(root)
        assert skill.tags == ["transit", "telegram"]
        assert skill.limits is not None
        assert skill.limits.max_turns == 6
        assert skill.limits.max_cost_usd == 0.05
        assert skill.limits.timeout_seconds == 60

    def test_description_optional(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\n",
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.description is None


class TestMissingFields:
    """Required-field errors are clear and name the missing field + file."""

    def test_missing_version_errors(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: x\n",
            package_yaml="author: westbrookai\n",
        )
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        msg = str(exc.value)
        assert "version" in msg
        assert "package.yaml" in msg

    def test_missing_name_errors(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="description: x\n",
            package_yaml="version: 0.1.0\n",
        )
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        msg = str(exc.value)
        assert "name" in msg
        assert "SKILL.md" in msg

    def test_missing_skill_md_errors(self, tmp_path):
        root = tmp_path / "weather"
        (root / "zipsa").mkdir(parents=True)
        (root / "zipsa" / "package.yaml").write_text("version: 0.1.0\n")
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        assert "SKILL.md" in str(exc.value)

    def test_missing_package_yaml_errors(self, tmp_path):
        root = tmp_path / "weather"
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text("---\nname: weather\n---\n")
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        assert "package.yaml" in str(exc.value)


class TestMalformedYaml:
    """Malformed YAML in either layer raises a clear ExecSkillError.

    The guarantee is a clear error naming the layer, NOT a raw
    `yaml.YAMLError` / traceback escaping to the caller.
    """

    def test_malformed_frontmatter_yaml_errors(self, tmp_path):
        # Unbalanced bracket in a flow sequence is not valid YAML.
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ntags: [unclosed, list\n",
            package_yaml="version: 0.1.0\n",
        )
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        msg = str(exc.value)
        assert "SKILL.md" in msg
        assert "YAML" in msg

    def test_malformed_package_yaml_errors(self, tmp_path):
        # Unbalanced bracket in a flow mapping is not valid YAML.
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: x\n",
            package_yaml="version: 0.1.0\ntags: [a, b\n",
        )
        with pytest.raises(ExecSkillError) as exc:
            load_exec_skill(root)
        msg = str(exc.value)
        assert "package.yaml" in msg
        assert "YAML" in msg


class TestFrontmatterTools:
    """allowed-tools/disallowed-tools accept both string and list forms."""

    def test_allowed_tools_string_form(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter=(
                "name: weather\n"
                "description: x\n"
                "allowed-tools: Read Grep\n"
            ),
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.allowed_tools == ["Read", "Grep"]

    def test_allowed_tools_comma_string_form(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter=(
                "name: weather\n"
                "description: x\n"
                "allowed-tools: Bash(python3:*), Write\n"
            ),
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.allowed_tools == ["Bash(python3:*)", "Write"]

    def test_allowed_tools_list_form(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter=(
                "name: weather\n"
                "description: x\n"
                "allowed-tools:\n"
                "  - Read\n"
                "  - Grep\n"
            ),
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.allowed_tools == ["Read", "Grep"]

    def test_disallowed_tools_and_model(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter=(
                "name: weather\n"
                "description: x\n"
                "disallowed-tools: WebFetch\n"
                "model: claude-haiku-4-5-20251001\n"
            ),
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.disallowed_tools == ["WebFetch"]
        assert skill.model == "claude-haiku-4-5-20251001"

    def test_tools_default_none(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: x\n",
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.allowed_tools is None
        assert skill.disallowed_tools is None
        assert skill.model is None


class TestRequires:
    """`requires` folds the mount mapping into each requirement."""

    def test_list_directory_with_container_prefix(self, tmp_path):
        root = _make_skill(
            tmp_path / "agenthud",
            frontmatter="name: agenthud\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "requires:\n"
                "  project_roots:\n"
                "    type: list[directory]\n"
                "    prompt: Which dirs contain your git projects?\n"
                "    container_prefix: /projects/\n"
                "    mode: ro\n"
            ),
        )
        skill = load_exec_skill(root)
        req = skill.requires["project_roots"]
        assert isinstance(req, Requirement)
        assert req.type == "list[directory]"
        assert req.container_prefix == "/projects/"
        assert req.container is None
        assert req.mode == "ro"

    def test_directory_with_container(self, tmp_path):
        root = _make_skill(
            tmp_path / "vault",
            frontmatter="name: vault\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "requires:\n"
                "  vault_dir:\n"
                "    type: directory\n"
                "    prompt: Where is your vault?\n"
                "    container: /vault\n"
                "    mode: rw\n"
            ),
        )
        skill = load_exec_skill(root)
        req = skill.requires["vault_dir"]
        assert req.type == "directory"
        assert req.container == "/vault"
        assert req.container_prefix is None
        assert req.mode == "rw"

    def test_directory_preserve_host_path(self, tmp_path):
        root = _make_skill(
            tmp_path / "sessions",
            frontmatter="name: sessions\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "requires:\n"
                "  session_dir:\n"
                "    type: directory\n"
                "    prompt: Where are sessions?\n"
                "    preserve_host_path: true\n"
            ),
        )
        skill = load_exec_skill(root)
        req = skill.requires["session_dir"]
        assert req.preserve_host_path is True

    def test_list_directory_rejects_container(self, tmp_path):
        root = _make_skill(
            tmp_path / "bad",
            frontmatter="name: bad\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "requires:\n"
                "  roots:\n"
                "    type: list[directory]\n"
                "    prompt: dirs?\n"
                "    container: /projects\n"
            ),
        )
        with pytest.raises(ExecSkillError):
            load_exec_skill(root)

    def test_directory_rejects_container_prefix(self, tmp_path):
        root = _make_skill(
            tmp_path / "bad",
            frontmatter="name: bad\ndescription: x\n",
            package_yaml=(
                "version: 0.1.0\n"
                "requires:\n"
                "  vault:\n"
                "    type: directory\n"
                "    prompt: dir?\n"
                "    container_prefix: /vaults/\n"
            ),
        )
        with pytest.raises(ExecSkillError):
            load_exec_skill(root)

    def test_no_requires_defaults_empty(self, tmp_path):
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: x\n",
            package_yaml="version: 0.1.0\n",
        )
        skill = load_exec_skill(root)
        assert skill.requires == {}


class TestLitmus:
    """Structural litmus: `rm -rf zipsa/` leaves a valid Agent Skill."""

    def test_skill_without_zipsa_is_valid_agent_skill(self, tmp_path):
        # A new-layout skill...
        root = _make_skill(
            tmp_path / "weather",
            frontmatter="name: weather\ndescription: Report weather\n",
            package_yaml="version: 0.1.0\n",
            scripts={"1.fetch.py": "# fetch\n", "2.report.md": "# report\n"},
        )
        # ...with zipsa/ removed.
        import shutil

        shutil.rmtree(root / "zipsa")

        # SKILL.md + scripts/ remain.
        assert (root / "SKILL.md").is_file()
        assert (root / "scripts").is_dir()
        assert list((root / "scripts").iterdir())

        # Frontmatter still parses with the standard required fields.
        from zipsa.core.exec_skill import parse_frontmatter

        fm = parse_frontmatter((root / "SKILL.md").read_text())
        assert fm["name"] == "weather"
        assert fm["description"] == "Report weather"
