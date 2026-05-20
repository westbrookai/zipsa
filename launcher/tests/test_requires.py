"""Tests for the requires-config core module."""

from pathlib import Path
import io
import pytest
import yaml


class TestValidateValue:
    def test_string_non_empty_returns_value(self):
        from zipsa.core.requires import validate_value
        assert validate_value("string", "hi") == "hi"

    def test_string_empty_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="must be non-empty"):
            validate_value("string", "")

    def test_string_whitespace_only_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="must be non-empty"):
            validate_value("string", "   ")

    def test_string_non_string_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="expected string"):
            validate_value("string", 123)

    def test_directory_existing_returns_absolute(self, tmp_path):
        from zipsa.core.requires import validate_value
        d = tmp_path / "code"
        d.mkdir()
        result = validate_value("directory", str(d))
        assert result == str(d.resolve())

    def test_directory_with_tilde_expanded(self, tmp_path, monkeypatch):
        from zipsa.core.requires import validate_value
        monkeypatch.setenv("HOME", str(tmp_path))
        d = tmp_path / "code"
        d.mkdir()
        result = validate_value("directory", "~/code")
        assert result == str(d.resolve())

    def test_directory_missing_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="does not exist"):
            validate_value("directory", str(tmp_path / "nope"))

    def test_directory_file_not_dir_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        f = tmp_path / "f"
        f.write_text("hi")
        with pytest.raises(ValueError, match="not a directory"):
            validate_value("directory", str(f))

    def test_list_directory_all_valid(self, tmp_path):
        from zipsa.core.requires import validate_value
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        result = validate_value("list[directory]", [str(a), str(b)])
        assert result == [str(a.resolve()), str(b.resolve())]

    def test_list_directory_one_invalid_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        a = tmp_path / "a"
        a.mkdir()
        with pytest.raises(ValueError, match="does not exist"):
            validate_value("list[directory]", [str(a), str(tmp_path / "missing")])

    def test_list_directory_non_list_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="expected list"):
            validate_value("list[directory]", "not a list")

    def test_unsupported_type_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="unsupported type"):
            validate_value("int", 5)


class TestLoadSave:
    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        from zipsa.core.requires import load_requires
        assert load_requires(tmp_path / "missing.yaml") == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        from zipsa.core.requires import load_requires, save_requires
        f = tmp_path / "requires.yaml"
        save_requires(f, {"project_roots": ["/a", "/b"]})
        assert load_requires(f) == {"project_roots": ["/a", "/b"]}

    def test_save_is_atomic_tmp_then_rename(self, tmp_path, monkeypatch):
        """Inject a write failure between tmp write and rename; original file unchanged."""
        from zipsa.core.requires import save_requires
        f = tmp_path / "requires.yaml"
        save_requires(f, {"x": "original"})

        # Make Path.replace blow up to simulate atomic rename failure
        from pathlib import Path as PathClass
        orig_replace = PathClass.replace

        def failing_replace(self, target):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(PathClass, "replace", failing_replace)

        with pytest.raises(OSError):
            save_requires(f, {"x": "new"})

        # Restore for read
        monkeypatch.setattr(PathClass, "replace", orig_replace)

        from zipsa.core.requires import load_requires
        assert load_requires(f) == {"x": "original"}

    def test_save_creates_parent_directory(self, tmp_path):
        from zipsa.core.requires import save_requires, load_requires
        f = tmp_path / "subdir" / "requires.yaml"
        save_requires(f, {"k": "v"})
        assert load_requires(f) == {"k": "v"}

    def test_load_invalid_yaml_raises(self, tmp_path):
        from zipsa.core.requires import load_requires
        f = tmp_path / "bad.yaml"
        f.write_text(":\n:: invalid yaml")
        with pytest.raises(yaml.YAMLError):
            load_requires(f)

    def test_load_non_dict_raises(self, tmp_path):
        from zipsa.core.requires import load_requires
        f = tmp_path / "list.yaml"
        f.write_text("- 1\n- 2\n")
        with pytest.raises(ValueError, match="must be a dict"):
            load_requires(f)


class TestClassifyState:
    def test_all_ok(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"
        d.mkdir()
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(d)}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {"path": str(d)}
        assert needs_prompt == []
        assert needs_revalidation == []

    def test_missing_value_marked_needs_prompt(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, {})
        assert ok == {}
        assert needs_prompt == ["path"]
        assert needs_revalidation == []

    def test_type_mismatch_marked_needs_prompt(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, {"path": ["not a string"]})
        assert needs_prompt == ["path"]

    def test_stale_path_marked_needs_revalidation(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(tmp_path / "nope")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {}
        assert needs_revalidation == ["path"]

    def test_extra_saved_keys_ignored(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"
        d.mkdir()
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(d), "stale_key": "leftover"}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {"path": str(d)}
        assert needs_prompt == []


class TestCarryOver:
    def test_carry_from_one_previous_version(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir

        prev = skill_data_dir("daily-progress", "0.4.0")
        prev.mkdir(parents=True)
        save_requires(prev / "requires.yaml", {"project_roots": ["/a"]})

        from zipsa.core.models import RequiresEntry
        current_spec = {"project_roots": RequiresEntry(type="list[directory]", prompt="?")}
        result = carry_over_from_previous("daily-progress", "0.5.0", current_spec)
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.4.0"
        assert values == {"project_roots": ["/a"]}

    def test_no_previous_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous
        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "new-skill", "0.1.0",
            {"x": RequiresEntry(type="string", prompt="?")},
        )
        assert result is None

    def test_picks_most_recent_when_multiple_previous(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir

        for v, val in [("0.4.0", "v4"), ("0.4.9", "v4_9"), ("0.5.0", "v5")]:
            d = skill_data_dir("s", v)
            d.mkdir(parents=True)
            save_requires(d / "requires.yaml", {"x": val})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous("s", "0.6.0", {"x": RequiresEntry(type="string", prompt="?")})
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.5.0"
        assert values == {"x": "v5"}

    def test_excludes_keys_with_type_mismatch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.4.0")
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"x": "old_string_value"})

        # Current schema changed type: x is now list[directory]
        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "s", "0.5.0",
            {"x": RequiresEntry(type="list[directory]", prompt="?")},
        )
        # x excluded (type mismatch), and no other keys carry over → None
        assert result is None

    def test_partial_carry_over(self, tmp_path, monkeypatch):
        """Old version had 2 keys; new schema renamed one → only the matching key carries over."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.4.0")
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"a": "hi", "old_name": "x"})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "s", "0.5.0",
            {"a": RequiresEntry(type="string", prompt="?"),
             "new_name": RequiresEntry(type="string", prompt="?")},
        )
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.4.0"
        assert values == {"a": "hi"}  # old_name excluded; new_name not present

    def test_self_excluded_from_previous_search(self, tmp_path, monkeypatch):
        """Should not consider the current version as 'previous'."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.5.0")  # SAME version
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"x": "self"})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous("s", "0.5.0", {"x": RequiresEntry(type="string", prompt="?")})
        assert result is None
