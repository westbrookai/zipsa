# Centralized Skill Run Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all skill runtime data from `skill_dir/.zipsa/` to `~/.zipsa/<skill-name>@<version>/` so logs and configs are centralized and easy to find.

**Architecture:** Add an `output_dir` parameter to `Skill.build_claude_json()`. In `DockerExecutor.run()`, compute the centralized `skill_data_dir = ~/.zipsa/<name>@<version>/` and pass it to all methods that currently write into `skill_dir/.zipsa/`. No new files — only path changes.

**Tech Stack:** Python 3.12, pytest, pathlib, uv

---

## File Map

| File | Change |
|------|--------|
| `zipsa/core/skill.py` | Add `output_dir` param to `build_claude_json()` |
| `zipsa/core/executor.py` | Compute `skill_data_dir`, pass to `build_claude_json()` and `_write_env_file()` |
| `tests/test_skill.py` | Update `TestClaudeJson` tests to pass `output_dir` |
| `tests/test_executor.py` | Update path assertions to `~/.zipsa/<name>@<version>/` |

---

### Task 1: Update `Skill.build_claude_json()` to accept `output_dir`

**Files:**
- Modify: `zipsa/core/skill.py:134-190`
- Test: `tests/test_skill.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_skill.py` inside `TestClaudeJson`:

```python
def test_build_claude_json_uses_default_home_dir(self, tmp_path):
    """build_claude_json with no args should write to ~/.zipsa/<name>@<version>/."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    manifest = {
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": "my-skill", "version": "2.0.0"},
        "spec": {
            "purpose": "Test",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": [], "mcp": []},
        },
    }
    import yaml
    (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
    (skill_dir / "SKILL.md").write_text("Test instructions")

    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        claude_json_path = skill.build_claude_json()

    expected_dir = tmp_path / ".zipsa" / "my-skill@2.0.0"
    assert claude_json_path == expected_dir / ".claude.json"
    assert claude_json_path.exists()
    assert (expected_dir / ".claude.json.org").exists()

def test_build_claude_json_uses_custom_output_dir(self, tmp_path):
    """build_claude_json with output_dir should write there."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    manifest = {
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": "my-skill", "version": "1.0.0"},
        "spec": {
            "purpose": "Test",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": [], "mcp": []},
        },
    }
    import yaml
    (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
    (skill_dir / "SKILL.md").write_text("Test instructions")

    output_dir = tmp_path / "custom-output"
    skill = Skill.load(skill_dir)
    claude_json_path = skill.build_claude_json(output_dir=output_dir)

    assert claude_json_path == output_dir / ".claude.json"
    assert claude_json_path.exists()
    assert (output_dir / ".claude.json.org").exists()
```

Also add `from unittest.mock import patch` to `tests/test_skill.py` imports.

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd launcher
uv run pytest tests/test_skill.py::TestClaudeJson::test_build_claude_json_uses_default_home_dir tests/test_skill.py::TestClaudeJson::test_build_claude_json_uses_custom_output_dir -v
```

Expected: FAIL — `build_claude_json()` doesn't accept `output_dir` yet.

- [ ] **Step 3: Update `Skill.build_claude_json()` in `zipsa/core/skill.py`**

Replace the entire `build_claude_json` method:

```python
def build_claude_json(self, output_dir: Optional[Path] = None) -> Path:
    """Generate .claude.json file for skill.

    Args:
        output_dir: Directory to write files into.
                    Defaults to ~/.zipsa/<name>@<version>/.

    Returns:
        Path to created .claude.json file
    """
    if output_dir is None:
        output_dir = (
            Path.home() / ".zipsa" / f"{self.name}@{self.manifest.metadata.version}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    mcp_servers = {}
    for server in self.manifest.spec.mcp:
        if server.type == "stdio":
            mcp_servers[server.name] = {
                "command": server.command,
                "args": server.args,
            }
        elif server.type == "http":
            server_config: dict = {
                "type": "http",
                "url": server.url,
            }
            if server.connection:
                server_config["connection"] = server.connection
            if server.headersHelper:
                server_config["headersHelper"] = server.headersHelper
            mcp_servers[server.name] = server_config

    claude_config = {
        "hasCompletedOnboarding": True,
        "projects": {
            "/workspace": {
                "hasTrustDialogAccepted": True,
                "mcpServers": mcp_servers,
            }
        },
    }

    config_text = json.dumps(claude_config, indent=2)
    claude_json_path = output_dir / ".claude.json"
    claude_json_path.write_text(config_text)
    (output_dir / ".claude.json.org").write_text(config_text)

    return claude_json_path
```

- [ ] **Step 4: Update existing `TestClaudeJson` tests to pass `output_dir`**

The 5 existing tests in `TestClaudeJson` (`test_build_claude_json_creates_file`, `test_build_claude_json_structure`, `test_build_claude_json_with_stdio_mcp`, `test_build_claude_json_with_http_mcp`, `test_build_claude_json_with_headers_helper`) each call `skill.build_claude_json()` without args and assert the file is at `skill_dir / ".zipsa"`.

For each of them:
1. Change `claude_json_path = skill.build_claude_json()` → `claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")`
2. Change `zipsa_dir = skill_dir / ".zipsa"` → `zipsa_dir = tmp_path / "skill-data"`

Example — `test_build_claude_json_creates_file` after change:

```python
def test_build_claude_json_creates_file(self, tmp_path):
    """Should create .claude.json file in output_dir."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    output_dir = tmp_path / "skill-data"

    manifest = {
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": "test", "version": "1.0.0"},
        "spec": {
            "purpose": "Test",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": [], "mcp": []},
        },
    }

    import yaml
    (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
    (skill_dir / "SKILL.md").write_text("Test instructions")

    skill = Skill.load(skill_dir)
    claude_json_path = skill.build_claude_json(output_dir=output_dir)

    assert claude_json_path.exists()
    assert claude_json_path == output_dir / ".claude.json"
    assert (output_dir / ".claude.json.org").exists()
    assert claude_json_path.read_text() == (output_dir / ".claude.json.org").read_text()
```

Apply the same pattern to the other 4 tests — replace `zipsa_dir = skill_dir / ".zipsa"` with `output_dir = tmp_path / "skill-data"` and pass it to `build_claude_json()`.

- [ ] **Step 5: Run all skill tests**

```bash
cd launcher
uv run pytest tests/test_skill.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd launcher
git add zipsa/core/skill.py tests/test_skill.py
git commit -m "feat: add output_dir param to build_claude_json, default to ~/.zipsa/<name>@<version>/"
```

---

### Task 2: Update `DockerExecutor` to use centralized `skill_data_dir`

**Files:**
- Modify: `zipsa/core/executor.py`
- Test: `tests/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_executor.py` inside `TestDockerExecutor`:

```python
@patch("zipsa.core.executor.subprocess.Popen")
def test_run_creates_run_dir_in_home(self, mock_popen, tmp_path):
    """run() should create runs/<timestamp>/ under ~/.zipsa/<name>@<version>/."""
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test", env={}))

    skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
    assert skill_data_dir.exists()
    runs_dir = skill_data_dir / "runs"
    assert runs_dir.exists()
    assert len(list(runs_dir.iterdir())) == 1

@patch("zipsa.core.executor.subprocess.Popen")
def test_run_creates_claude_json_in_home(self, mock_popen, tmp_path):
    """run() should create .claude.json under ~/.zipsa/<name>@<version>/."""
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test", env={}))

    skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
    assert (skill_data_dir / ".claude.json").exists()

@patch("zipsa.core.executor.subprocess.Popen")
def test_run_cleans_up_env_file_in_home(self, mock_popen, tmp_path):
    """run() should delete .env from ~/.zipsa/<name>@<version>/ after execution."""
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test", env={"SECRET": "value"}))

    env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
    assert not env_file.exists()
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
cd launcher
uv run pytest tests/test_executor.py::TestDockerExecutor::test_run_creates_run_dir_in_home tests/test_executor.py::TestDockerExecutor::test_run_creates_claude_json_in_home tests/test_executor.py::TestDockerExecutor::test_run_cleans_up_env_file_in_home -v
```

Expected: FAIL — paths still use `skill_dir/.zipsa/`.

- [ ] **Step 3: Update `DockerExecutor.run()` to compute `skill_data_dir`**

In `zipsa/core/executor.py`, find the `run()` method. Replace:

```python
# Create run directory for logging (skip for dry-run and shell mode)
run_dir = None
if not dry_run and not shell:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
    run_dir = skill.skill_dir / ".zipsa" / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

# Generate .claude.json for skill (contains MCP config + onboarding settings)
claude_json_path = skill.build_claude_json()

env_file = skill.skill_dir / ".zipsa" / ".env"
```

With:

```python
# Centralized skill data directory: ~/.zipsa/<name>@<version>/
skill_data_dir = (
    Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}"
)
skill_data_dir.mkdir(parents=True, exist_ok=True)

# Create run directory for logging (skip for dry-run and shell mode)
run_dir = None
if not dry_run and not shell:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
    run_dir = skill_data_dir / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

# Generate .claude.json in centralized directory
claude_json_path = skill.build_claude_json(output_dir=skill_data_dir)

env_file = skill_data_dir / ".env"
```

- [ ] **Step 4: Update `_write_env_file()` to accept `output_dir`**

In `zipsa/core/executor.py`, replace the `_write_env_file` method:

```python
def _write_env_file(self, output_dir: Path, env: dict[str, str]) -> Path:
    """Write env vars to output_dir/.env and return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    env_file = output_dir / ".env"
    with open(env_file, "w") as f:
        for key, value in env.items():
            f.write(f"{key}={value}\n")
    return env_file
```

- [ ] **Step 5: Update `_build_docker_command()` to use `skill_data_dir`**

In `_build_docker_command()`, the env file is now written via `run()` before this is called. The env file path comes from the `skill_data_dir`. Find the env file writing block:

```python
env_file = self._write_env_file(skill, env)
cmd.extend(["--env-file", str(env_file)])
```

Replace with:

```python
skill_data_dir = (
    Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}"
)
env_file = self._write_env_file(skill_data_dir, env)
cmd.extend(["--env-file", str(env_file)])
```

- [ ] **Step 6: Run the three new executor tests**

```bash
cd launcher
uv run pytest tests/test_executor.py::TestDockerExecutor::test_run_creates_run_dir_in_home tests/test_executor.py::TestDockerExecutor::test_run_creates_claude_json_in_home tests/test_executor.py::TestDockerExecutor::test_run_cleans_up_env_file_in_home -v
```

Expected: PASS.

- [ ] **Step 7: Run full test suite and fix any breakage**

```bash
cd launcher
uv run pytest --tb=short
```

The following existing tests will likely fail because they check paths under `skill.skill_dir / ".zipsa"`:
- `test_run_creates_claude_config` — check `skill_dir/.zipsa/` (now wrong)
- `test_run_persists_claude_config` — same
- `test_run_cleans_up_env_file` — same
- `test_write_env_file_creates_file` / `test_write_env_file_empty_env` — `_write_env_file` signature changed
- `test_build_docker_command_uses_env_file` — env file path now under `~/.zipsa/`
- `TestRuntimeConfig` tests — env file path changed

Fix each failing test:

**`test_run_creates_claude_config` and `test_run_persists_claude_config`** — these no longer need to check a specific path; they can use `patch("pathlib.Path.home")` with `tmp_path`:

```python
@patch("zipsa.core.executor.subprocess.Popen")
def test_run_creates_claude_config(self, mock_popen, tmp_path):
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output line\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test input", env={}))

    skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
    assert skill_data_dir.exists()
    assert (skill_data_dir / ".claude.json").exists()

@patch("zipsa.core.executor.subprocess.Popen")
def test_run_persists_claude_config(self, mock_popen, tmp_path):
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test", env={}))

    claude_json = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".claude.json"
    assert claude_json.exists()
```

**`test_run_cleans_up_env_file`** — update path:

```python
@patch("zipsa.core.executor.subprocess.Popen")
def test_run_cleans_up_env_file(self, mock_popen, tmp_path):
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = ["output\n", ""]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        list(executor.run(skill, "Test", env={"SECRET": "value"}))

    env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
    assert not env_file.exists()
```

**`test_write_env_file_creates_file` and `test_write_env_file_empty_env`** — signature changed from `(skill, env)` to `(output_dir, env)`:

```python
def test_write_env_file_creates_file(self, tmp_path):
    executor = DockerExecutor()
    output_dir = tmp_path / "skill-data"
    env = {"FOO": "bar", "TOKEN": "secret"}

    env_file = executor._write_env_file(output_dir, env)

    assert env_file == output_dir / ".env"
    assert env_file.exists()
    content = env_file.read_text()
    assert "FOO=bar\n" in content
    assert "TOKEN=secret\n" in content

def test_write_env_file_empty_env(self, tmp_path):
    executor = DockerExecutor()
    output_dir = tmp_path / "skill-data"

    env_file = executor._write_env_file(output_dir, {})

    assert env_file.exists()
    assert env_file.read_text() == ""
```

**`test_build_docker_command_uses_env_file`** — env file now under `~/.zipsa/`:

```python
def test_build_docker_command_uses_env_file(self, tmp_path):
    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
    skill = Skill.load(skill_dir)
    claude_json_path = skill.build_claude_json(output_dir=tmp_path)
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}

    with patch("pathlib.Path.home", return_value=tmp_path):
        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test input",
            claude_json_path=claude_json_path,
            env=env,
        )

    assert "--env-file" in cmd
    assert "-e" not in cmd

    env_file = tmp_path / ".zipsa" / "minimal@1.0.0" / ".env"
    assert str(env_file) in cmd
    assert "CLAUDE_CODE_OAUTH_TOKEN=test-token\n" in env_file.read_text()

    assert cmd[0] == "docker"
    assert "--rm" in cmd
    assert "--name" in cmd
    assert "/home/agent/.claude.json" in " ".join(cmd)
    assert "ghcr.io/westbrookai/zipsa-runtime:latest" in cmd
    assert "claude" in cmd
```

**`test_dry_run_cleans_up_env_file`** — update path:

```python
@patch("zipsa.core.executor.subprocess.Popen")
@patch("builtins.print")
def test_dry_run_cleans_up_env_file(self, mock_print, mock_popen, tmp_path):
    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    with patch("pathlib.Path.home", return_value=tmp_path):
        executor.run(skill, "Test", env={"SECRET": "value"}, dry_run=True)

    env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
    assert not env_file.exists()
```

**`TestRuntimeConfig` tests** — each calls `_build_docker_command()` and checks `skill.skill_dir / ".zipsa" / ".env"`. Update to use `tmp_path` mock for home and the new path:

```python
# Pattern for all TestRuntimeConfig tests:
# 1. Add tmp_path parameter
# 2. Wrap _build_docker_command call with patch("pathlib.Path.home", return_value=tmp_path)
# 3. Change env_file path to: tmp_path / ".zipsa" / "minimal@1.0.0" / ".env"
```

For example, `test_auto_inject_env_from_config`:

```python
def test_auto_inject_env_from_config(self, tmp_path):
    config_path = tmp_path / "runtime-config.yaml"
    config_path.write_text("""
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
""")

    executor = DockerExecutor(runtime_config_path=config_path)
    skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
    skill = Skill.load(skill_dir)
    claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

    with patch("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
        with patch("pathlib.Path.home", return_value=tmp_path):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

    env_file = tmp_path / ".zipsa" / "minimal@1.0.0" / ".env"
    assert "CLAUDE_CODE_OAUTH_TOKEN=test-token\n" in env_file.read_text()
```

Apply the same pattern to the other 4 `TestRuntimeConfig` tests.

- [ ] **Step 8: Run full test suite to confirm all pass**

```bash
cd launcher
uv run pytest --tb=short
```

Expected: all PASS, coverage ≥ 70%.

- [ ] **Step 9: Commit**

```bash
cd /path/to/zipsa
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat: centralize skill runtime data under ~/.zipsa/<name>@<version>/"
```

---

### Task 3: Clean up `skill_dir/.zipsa/` from existing skills and update gitignore

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Remove `.zipsa/` from existing skills**

```bash
find /Users/neochoon/WestbrookAI/zipsa/skills -name ".zipsa" -type d -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 2: Verify `.gitignore` still has `.zipsa/` entry**

```bash
grep "\.zipsa" /Users/neochoon/WestbrookAI/zipsa/.gitignore
```

Expected output: `# zipsa runtime and execution files` and `.zipsa/`.

No change needed — the rule still applies to any accidental `.zipsa/` creation.

- [ ] **Step 3: Commit cleanup**

```bash
cd /path/to/zipsa
git add -u
git commit -m "chore: remove skill_dir/.zipsa/ directories (data moved to ~/.zipsa/)"
```

---

### Task 4: Tag and release

- [ ] **Step 1: Run full test suite one final time**

```bash
cd launcher
uv run pytest --cov=zipsa --cov-fail-under=70
```

Expected: all PASS, coverage ≥ 70%.

- [ ] **Step 2: Push and tag**

```bash
cd /path/to/zipsa
git push origin main
git tag launcher-v0.1.5
git push origin launcher-v0.1.5
```

- [ ] **Step 3: Confirm CI passes**

```bash
gh run watch $(gh run list --limit 1 --json databaseId -q '.[0].databaseId')
```

Expected: `✓ Publish Launcher to PyPI`
