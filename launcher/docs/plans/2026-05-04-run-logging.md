# SKILL Run Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Save execution outputs to timestamped folders for skill memory and analytics

**Architecture:** Modify DockerExecutor to save Docker stdout to output.jsonl in real-time while streaming to CLI, then post-process into summary.jsonl (filtered events) and metadata.json (extracted metrics)

**Tech Stack:** Python stdlib (json, datetime, pathlib), existing executor infrastructure

---

## File Structure

**Modified:**
- `zipsa/core/executor.py` - Add run_dir creation, real-time logging, summary/metadata generation
- `zipsa-skills/*/.gitignore` - Ignore runs directory

**Created:**
- `tests/test_run_logger.py` - Unit tests for logging functionality

**Verified:**
- `zipsa/core/skill.py` - Already has `skill_dir` property (no changes needed)

---

### Task 1: Add datetime import and create run directory

**Files:**
- Modify: `zipsa/core/executor.py:1-10`
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write failing test for run_dir creation**

Create `tests/test_run_logger.py`:

```python
"""Tests for run logging functionality."""

from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill


class TestRunLogging:
    """Test run logging functionality."""

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("zipsa.core.executor.datetime")
    def test_run_dir_created(self, mock_datetime, mock_popen):
        """Run should create timestamped directory in skill/.zipsa/runs/."""
        # Mock datetime
        mock_datetime.now.return_value.strftime.return_value = "2026-05-04_143022_123456"

        # Mock process
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Load skill
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Execute
        list(executor.run(skill, "Test input", env={}))

        # Verify run directory created
        run_dir = skill_dir / ".zipsa" / "runs" / "2026-05-04_143022_123456"
        assert run_dir.exists()
        assert run_dir.is_dir()

        # Cleanup
        import shutil
        shutil.rmtree(skill_dir / ".zipsa" / "runs")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-launcher
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_run_dir_created -v
```

Expected: FAIL with "AttributeError: module 'zipsa.core.executor' has no attribute 'datetime'"

- [ ] **Step 3: Add datetime import to executor.py**

In `zipsa/core/executor.py`, add after line 4:

```python
from datetime import datetime
```

- [ ] **Step 4: Create run_dir in DockerExecutor.run()**

In `zipsa/core/executor.py`, modify the `run()` method around line 33-54:

```python
def run(
    self,
    skill: Skill,
    user_input: str,
    env: Optional[dict[str, str]] = None,
    dry_run: bool = False,
) -> Optional[Iterator[dict]]:
    """Execute skill in Docker container.

    Args:
        skill: Skill to execute
        user_input: User's input/query
        env: Environment variables
        dry_run: If True, print command without executing

    Returns:
        Iterator of parsed output events (None for dry_run)

    Raises:
        RuntimeError: If Docker execution fails
    """
    env = env or {}

    # Create temp directory in workspace for MCP config
    temp_dir = self.workspace / ".zipsa"
    temp_dir.mkdir(exist_ok=True)

    # Create run directory for logging (skip for dry-run)
    run_dir = None
    if not dry_run:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
        run_dir = skill.skill_dir / ".zipsa" / "runs" / timestamp
        run_dir.mkdir(parents=True, exist_ok=True)

    # Create temp MCP config file
    mcp_config_path = temp_dir / f"mcp-config-{id(self)}.json"
    mcp_config = skill.build_mcp_config()
    mcp_config_path.write_text(json.dumps(mcp_config))

    try:
        # Build Docker command
        docker_cmd = self._build_docker_command(
            skill, user_input, mcp_config_path, env
        )

        if dry_run:
            self._print_dry_run(skill, docker_cmd, mcp_config)
            return None

        # Execute and return generator
        return self._execute_skill(docker_cmd, mcp_config_path, skill, run_dir)

    except Exception:
        # Cleanup on error
        mcp_config_path.unlink(missing_ok=True)
        raise
    finally:
        # Cleanup temp file for dry_run (non-dry_run cleanup is in _execute_skill)
        if dry_run:
            mcp_config_path.unlink(missing_ok=True)
```

- [ ] **Step 5: Update _execute_skill signature**

In `zipsa/core/executor.py`, update `_execute_skill` method signature (around line 87):

```python
def _execute_skill(
    self, docker_cmd: list[str], mcp_config_path: Path, skill: Skill, run_dir: Optional[Path]
) -> Iterator[dict]:
    """Execute Docker command and stream output.

    Args:
        docker_cmd: Docker command array
        mcp_config_path: Path to temp MCP config file
        skill: Skill being executed
        run_dir: Directory to save run logs (None to skip logging)

    Yields:
        Parsed output events

    Raises:
        RuntimeError: If Docker execution fails
    """
```

- [ ] **Step 6: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_run_dir_created -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add zipsa/core/executor.py tests/test_run_logger.py
git commit -m "feat: create timestamped run directory for logging

- Add datetime import
- Create .zipsa/runs/<timestamp> directory per execution
- Skip directory creation for dry-run mode
- Pass run_dir to _execute_skill

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 2: Save output.jsonl in real-time

**Files:**
- Modify: `zipsa/core/executor.py:87-127`
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write failing test for output.jsonl saving**

Add to `tests/test_run_logger.py`:

```python
@patch("zipsa.core.executor.subprocess.Popen")
@patch("zipsa.core.executor.datetime")
def test_output_jsonl_saved(self, mock_datetime, mock_popen):
    """Output should be saved to output.jsonl in real-time."""
    mock_datetime.now.return_value.strftime.return_value = "2026-05-04_143022_123456"

    # Mock process with JSON output
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = [
        '{"type":"system","subtype":"init"}\n',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}\n',
        '{"type":"result","num_turns":1}\n',
        ""
    ]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    # Execute
    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    list(executor.run(skill, "Test input", env={}))

    # Verify output.jsonl exists and contains events
    run_dir = skill_dir / ".zipsa" / "runs" / "2026-05-04_143022_123456"
    output_file = run_dir / "output.jsonl"

    assert output_file.exists()

    # Read and verify content
    lines = output_file.read_text().strip().split('\n')
    assert len(lines) == 3
    assert '"type":"system"' in lines[0]
    assert '"type":"assistant"' in lines[1]
    assert '"type":"result"' in lines[2]

    # Cleanup
    import shutil
    shutil.rmtree(skill_dir / ".zipsa" / "runs")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_output_jsonl_saved -v
```

Expected: FAIL with "AssertionError: assert False" (file doesn't exist)

- [ ] **Step 3: Implement output.jsonl real-time saving**

In `zipsa/core/executor.py`, modify `_execute_skill` method (around line 87-127):

```python
def _execute_skill(
    self, docker_cmd: list[str], mcp_config_path: Path, skill: Skill, run_dir: Optional[Path]
) -> Iterator[dict]:
    """Execute Docker command and stream output.

    Args:
        docker_cmd: Docker command array
        mcp_config_path: Path to temp MCP config file
        skill: Skill being executed
        run_dir: Directory to save run logs (None to skip logging)

    Yields:
        Parsed output events

    Raises:
        RuntimeError: If Docker execution fails
    """
    output_file = None
    if run_dir:
        output_file = run_dir / "output.jsonl"

    try:
        # Execute Docker
        process = subprocess.Popen(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Stream output through runtime parser
        raw_stream = iter(process.stdout.readline, "")

        # Save to file while parsing
        if output_file:
            with open(output_file, 'w', buffering=1) as f:  # Line buffering
                for line in raw_stream:
                    if line:
                        # Write to file
                        f.write(line)

                        # Parse and yield
                        parsed_events = self.runtime.parse_output([line])
                        yield from parsed_events
        else:
            # No logging - just parse and yield
            parsed_stream = self.runtime.parse_output(raw_stream)
            yield from parsed_stream

        process.wait()

        if process.returncode != 0:
            raise RuntimeError(
                f"Docker execution failed with code {process.returncode}"
            )

    finally:
        # Cleanup temp MCP config file
        mcp_config_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_output_jsonl_saved -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add zipsa/core/executor.py tests/test_run_logger.py
git commit -m "feat: save Docker stdout to output.jsonl in real-time

- Write each line to output.jsonl while streaming
- Use line buffering for immediate writes
- Skip file creation if run_dir is None

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Implement summary.jsonl filtering

**Files:**
- Modify: `zipsa/core/executor.py` (add new method)
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write failing test for summary.jsonl**

Add to `tests/test_run_logger.py`:

```python
def test_summary_filtering(self):
    """Summary should contain only important events."""
    # Create test run directory
    run_dir = Path(__file__).parent / "test_runs" / "test-summary"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create test output.jsonl
    output_file = run_dir / "output.jsonl"
    output_file.write_text(
        '{"type":"system","subtype":"init","tools":["WebFetch"]}\n'
        '{"type":"rate_limit_event","status":"ok"}\n'
        '{"type":"assistant","message":{"content":[{"type":"thinking"}]}}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result"}]}}\n'
        '{"type":"result","num_turns":1,"is_error":false}\n'
    )

    # Call _save_summary
    from zipsa.core.executor import DockerExecutor
    executor = DockerExecutor()
    executor._save_summary(run_dir)

    # Verify summary.jsonl
    summary_file = run_dir / "summary.jsonl"
    assert summary_file.exists()

    lines = summary_file.read_text().strip().split('\n')
    assert len(lines) == 4  # system init, assistant, user, result (no rate_limit)

    # Verify content
    assert '"type":"system"' in lines[0]
    assert '"type":"assistant"' in lines[1]
    assert '"type":"user"' in lines[2]
    assert '"type":"result"' in lines[3]

    # Cleanup
    import shutil
    shutil.rmtree(Path(__file__).parent / "test_runs")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_summary_filtering -v
```

Expected: FAIL with "AttributeError: 'DockerExecutor' object has no attribute '_save_summary'"

- [ ] **Step 3: Implement _save_summary method**

Add to `zipsa/core/executor.py` after `_execute_skill` method:

```python
def _save_summary(self, run_dir: Path) -> None:
    """Generate summary.jsonl from output.jsonl.

    Filters for important events only:
    - system (init only)
    - assistant (all)
    - user (all)
    - result (all)
    - any event with "error" in type

    Args:
        run_dir: Run directory containing output.jsonl
    """
    output_file = run_dir / "output.jsonl"
    summary_file = run_dir / "summary.jsonl"

    if not output_file.exists():
        return

    important_types = {"system", "assistant", "user", "result"}

    with open(output_file, 'r') as inf, open(summary_file, 'w') as outf:
        for line in inf:
            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
                event_type = event.get("type", "")

                # Include important events
                if event_type in important_types or "error" in event_type.lower():
                    # For system events, only keep init
                    if event_type == "system":
                        if event.get("subtype") == "init":
                            outf.write(line + '\n')
                    else:
                        outf.write(line + '\n')
            except json.JSONDecodeError:
                # Skip malformed lines
                continue
```

- [ ] **Step 4: Call _save_summary in _execute_skill finally block**

In `zipsa/core/executor.py`, modify `_execute_skill` finally block:

```python
    finally:
        # Generate summary if logging enabled
        if run_dir:
            try:
                self._save_summary(run_dir)
            except Exception as e:
                # Don't fail execution due to logging errors
                print(f"Warning: Failed to save summary: {e}", file=sys.stderr)

        # Cleanup temp MCP config file
        mcp_config_path.unlink(missing_ok=True)
```

Add import at top of file:

```python
import sys
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_summary_filtering -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add zipsa/core/executor.py tests/test_run_logger.py
git commit -m "feat: generate summary.jsonl with filtered events

- Add _save_summary() method to filter important events
- Call in _execute_skill finally block
- Handle errors gracefully with stderr warning

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 4: Implement metadata.json extraction

**Files:**
- Modify: `zipsa/core/executor.py` (add new method)
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write failing test for metadata.json**

Add to `tests/test_run_logger.py`:

```python
def test_metadata_extraction(self):
    """Metadata should be extracted from result event."""
    # Create test run directory
    run_dir = Path(__file__).parent / "test_runs" / "test-metadata"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create test output.jsonl with result event
    output_file = run_dir / "output.jsonl"
    output_file.write_text(
        '{"type":"system","subtype":"init"}\n'
        '{"type":"result","duration_ms":15562,"duration_api_ms":14586,'
        '"num_turns":3,"total_cost_usd":0.099,"is_error":false,'
        '"stop_reason":"end_turn","terminal_reason":"completed",'
        '"usage":{"input_tokens":7,"output_tokens":302},'
        '"modelUsage":{"claude-sonnet-4-6":{"costUSD":0.08}}}\n'
    )

    # Create mock skill
    from zipsa.core.skill import Skill
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    # Call _save_metadata
    from zipsa.core.executor import DockerExecutor
    executor = DockerExecutor()
    executor._save_metadata(run_dir, skill)

    # Verify metadata.json
    metadata_file = run_dir / "metadata.json"
    assert metadata_file.exists()

    import json
    metadata = json.loads(metadata_file.read_text())

    assert metadata["skill_name"] == "test-skill"
    assert metadata["num_turns"] == 3
    assert metadata["total_cost_usd"] == 0.099
    assert metadata["is_error"] is False
    assert metadata["duration_ms"] == 15562

    # Cleanup
    import shutil
    shutil.rmtree(Path(__file__).parent / "test_runs")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_metadata_extraction -v
```

Expected: FAIL with "AttributeError: 'DockerExecutor' object has no attribute '_save_metadata'"

- [ ] **Step 3: Implement _save_metadata method**

Add to `zipsa/core/executor.py` after `_save_summary` method:

```python
def _save_metadata(self, run_dir: Path, skill: Skill) -> None:
    """Extract metrics from output.jsonl and save to metadata.json.

    Extracts execution metrics from the result event.

    Args:
        run_dir: Run directory containing output.jsonl
        skill: Skill that was executed
    """
    output_file = run_dir / "output.jsonl"
    metadata_file = run_dir / "metadata.json"

    if not output_file.exists():
        return

    # Find result event
    result_event = None
    with open(output_file, 'r') as f:
        for line in f:
            try:
                event = json.loads(line.strip())
                if event.get("type") == "result":
                    result_event = event
                    break
            except json.JSONDecodeError:
                continue

    if not result_event:
        # Execution failed before result
        metadata = {
            "run_id": run_dir.name,
            "skill_name": skill.name,
            "skill_version": skill.manifest.metadata.version,
            "timestamp": datetime.now().isoformat(),
            "is_error": True,
            "error": "No result event found - execution may have failed"
        }
    else:
        # Extract from result event
        metadata = {
            "run_id": run_dir.name,
            "skill_name": skill.name,
            "skill_version": skill.manifest.metadata.version,
            "timestamp": datetime.now().isoformat(),
            "duration_ms": result_event.get("duration_ms"),
            "duration_api_ms": result_event.get("duration_api_ms"),
            "num_turns": result_event.get("num_turns"),
            "total_cost_usd": result_event.get("total_cost_usd"),
            "is_error": result_event.get("is_error", False),
            "stop_reason": result_event.get("stop_reason"),
            "terminal_reason": result_event.get("terminal_reason"),
            "usage": result_event.get("usage", {}),
            "model_usage": result_event.get("modelUsage", {})
        }

    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
```

- [ ] **Step 4: Call _save_metadata in _execute_skill finally block**

In `zipsa/core/executor.py`, modify `_execute_skill` finally block:

```python
    finally:
        # Generate summary and metadata if logging enabled
        if run_dir:
            try:
                self._save_summary(run_dir)
                self._save_metadata(run_dir, skill)
            except Exception as e:
                # Don't fail execution due to logging errors
                print(f"Warning: Failed to save run logs: {e}", file=sys.stderr)

        # Cleanup temp MCP config file
        mcp_config_path.unlink(missing_ok=True)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_metadata_extraction -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add zipsa/core/executor.py tests/test_run_logger.py
git commit -m "feat: extract metrics to metadata.json

- Add _save_metadata() to extract metrics from result event
- Handle missing result event (failed executions)
- Save skill name, version, metrics, usage data
- Call in _execute_skill finally block

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 5: Test dry-run doesn't create logs

**Files:**
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write test for dry-run no logging**

Add to `tests/test_run_logger.py`:

```python
@patch("zipsa.core.executor.subprocess.Popen")
@patch("builtins.print")
def test_dry_run_no_logging(self, mock_print, mock_popen):
    """Dry-run should not create run directory or logs."""
    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    # Execute in dry-run mode
    result = executor.run(skill, "Test", env={}, dry_run=True)

    # Should return None
    assert result is None

    # Should not call Popen
    mock_popen.assert_not_called()

    # Verify no run directory created
    runs_dir = skill_dir / ".zipsa" / "runs"
    if runs_dir.exists():
        # Should be empty
        assert len(list(runs_dir.iterdir())) == 0
```

- [ ] **Step 2: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_dry_run_no_logging -v
```

Expected: PASS (already implemented in Task 1)

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_logger.py
git commit -m "test: verify dry-run mode doesn't create logs

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 6: Test error handling preserves partial logs

**Files:**
- Test: `tests/test_run_logger.py`

- [ ] **Step 1: Write test for error with partial log**

Add to `tests/test_run_logger.py`:

```python
@patch("zipsa.core.executor.subprocess.Popen")
@patch("zipsa.core.executor.datetime")
def test_error_partial_log(self, mock_datetime, mock_popen):
    """Failed execution should preserve partial logs."""
    mock_datetime.now.return_value.strftime.return_value = "2026-05-04_150000_123456"

    # Mock process that outputs then fails
    mock_stdout = MagicMock()
    mock_stdout.readline.side_effect = [
        '{"type":"system","subtype":"init"}\n',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Starting"}]}}\n',
        ""  # End of output
    ]
    mock_process = Mock()
    mock_process.stdout = mock_stdout
    mock_process.wait.return_value = 1  # Non-zero exit
    mock_process.returncode = 1
    mock_popen.return_value = mock_process

    # Execute (should raise)
    executor = DockerExecutor()
    skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
    skill = Skill.load(skill_dir)

    try:
        list(executor.run(skill, "Test", env={}))
    except RuntimeError:
        pass  # Expected

    # Verify partial log saved
    run_dir = skill_dir / ".zipsa" / "runs" / "2026-05-04_150000_123456"
    output_file = run_dir / "output.jsonl"

    assert output_file.exists()
    lines = output_file.read_text().strip().split('\n')
    assert len(lines) == 2  # Both events saved before failure

    # Verify metadata marks error
    metadata_file = run_dir / "metadata.json"
    assert metadata_file.exists()

    import json
    metadata = json.loads(metadata_file.read_text())
    assert metadata["is_error"] is True
    assert "No result event" in metadata["error"]

    # Cleanup
    import shutil
    shutil.rmtree(skill_dir / ".zipsa" / "runs")
```

- [ ] **Step 2: Run test to verify it passes**

```bash
.venv/bin/pytest tests/test_run_logger.py::TestRunLogging::test_error_partial_log -v
```

Expected: PASS (error handling already implemented)

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_logger.py
git commit -m "test: verify partial logs preserved on error

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 7: Update .gitignore files

**Files:**
- Modify: `zipsa-skills/*/.gitignore`

- [ ] **Step 1: Add .zipsa/runs/ to weather skill .gitignore**

Check if `.gitignore` exists in weather skill:

```bash
ls -la /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.gitignore
```

If it doesn't exist, create it:

```bash
cat > /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.gitignore << 'EOF'
# Run logs - generated during execution
.zipsa/runs/
EOF
```

If it exists, append:

```bash
echo "" >> /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.gitignore
echo "# Run logs - generated during execution" >> /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.gitignore
echo ".zipsa/runs/" >> /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.gitignore
```

- [ ] **Step 2: Verify .gitignore works**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather
mkdir -p .zipsa/runs/test
touch .zipsa/runs/test/dummy.txt
git status --short
```

Expected: No output (runs directory ignored)

- [ ] **Step 3: Clean up test**

```bash
rm -rf .zipsa/runs/test
```

- [ ] **Step 4: Commit**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc
git add zipsa-skills/weather/.gitignore
git commit -m "chore: ignore run logs in weather skill

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 8: Integration test with real weather skill

**Files:**
- Test: Manual verification

- [ ] **Step 1: Run weather skill with logging**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-launcher
.venv/bin/python -m zipsa.cli run ../zipsa-skills/weather "What's the weather in Seoul?" -e "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}"
```

Expected: Normal execution output

- [ ] **Step 2: Verify run directory created**

```bash
ls -la /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/
```

Expected: One timestamped directory

- [ ] **Step 3: Verify all three files exist**

```bash
LATEST_RUN=$(ls -t /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/ | head -1)
ls -lh /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/$LATEST_RUN/
```

Expected:
```
output.jsonl   (several KB)
summary.jsonl  (smaller)
metadata.json  (< 1KB)
```

- [ ] **Step 4: Verify output.jsonl has content**

```bash
wc -l /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/$LATEST_RUN/output.jsonl
```

Expected: 10+ lines

- [ ] **Step 5: Verify summary.jsonl is filtered**

```bash
wc -l /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/$LATEST_RUN/summary.jsonl
```

Expected: Fewer lines than output.jsonl

- [ ] **Step 6: Verify metadata.json has metrics**

```bash
cat /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-skills/weather/.zipsa/runs/$LATEST_RUN/metadata.json | jq .
```

Expected: JSON with skill_name, num_turns, total_cost_usd, etc.

- [ ] **Step 7: Run all unit tests**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-launcher
.venv/bin/pytest tests/test_run_logger.py -v
```

Expected: All tests PASS

- [ ] **Step 8: Run full test suite**

```bash
.venv/bin/pytest -v
```

Expected: All 48+ tests PASS

- [ ] **Step 9: Commit integration verification**

```bash
git add -A
git commit -m "test: verify run logging integration

Manual integration test with weather skill:
- Confirms run directory creation
- Validates all three files (output, summary, metadata)
- Checks content and filtering accuracy

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 9: Update documentation

**Files:**
- Create: `zipsa-launcher/docs/run-logging.md`

- [ ] **Step 1: Create run-logging.md documentation**

```bash
cat > /Users/neochoon/WestbrookAI/skill-runtime-poc/zipsa-launcher/docs/run-logging.md << 'EOF'
# Run Logging

Zipsa automatically saves execution logs for every skill run.

## Log Location

```
<skill_dir>/.zipsa/runs/<YYYY-MM-DD_HHMMSS_microseconds>/
  ├── output.jsonl      # Complete stdout (stream-json format)
  ├── summary.jsonl     # Important events only
  └── metadata.json     # Extracted metrics
```

## Files

### output.jsonl

Complete Docker container stdout in Claude Code's stream-json format.

Contains all events:
- System initialization
- Rate limits
- Assistant messages (thinking, tool use, text)
- User messages (tool results)
- Final result summary

**Use case:** Full execution replay, debugging

### summary.jsonl

Filtered subset of important events:
- `system` (init only)
- `assistant` (all messages)
- `user` (all messages)
- `result` (final summary)
- Any `error` events

**Use case:** Quick review without noise

### metadata.json

Extracted execution metrics:

```json
{
  "run_id": "2026-05-04_143022_123456",
  "skill_name": "weather",
  "skill_version": "1.0.0",
  "timestamp": "2026-05-04T14:30:22Z",
  "duration_ms": 15562,
  "num_turns": 3,
  "total_cost_usd": 0.099,
  "is_error": false,
  "usage": {
    "input_tokens": 7,
    "output_tokens": 302
  }
}
```

**Use case:** Analytics, cost tracking, monitoring

## Dry-Run Mode

Run logs are **not** created when using `--dry-run` flag.

## Error Handling

If execution fails:
- `output.jsonl` contains partial output (already saved)
- `summary.jsonl` and `metadata.json` generated from available data
- `metadata.json` marks `is_error: true`

## Future Features

- MCP server for querying past runs
- CLI commands: `zipsa runs`, `zipsa analyze`
- Automatic context injection from previous runs
- Retention policies
EOF
```

- [ ] **Step 2: Commit documentation**

```bash
cd /Users/neochoon/WestbrookAI/skill-runtime-poc
git add zipsa-launcher/docs/run-logging.md
git commit -m "docs: add run logging documentation

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Implementation Complete

All tasks completed:
- ✅ Run directory creation with timestamps
- ✅ Real-time output.jsonl saving
- ✅ summary.jsonl filtering
- ✅ metadata.json extraction
- ✅ Dry-run handling
- ✅ Error handling
- ✅ .gitignore updates
- ✅ Integration testing
- ✅ Documentation

**Testing:**
- 6 unit tests in `tests/test_run_logger.py`
- 1 integration test (manual verification)
- Full test suite passes

**Files modified:**
- `zipsa/core/executor.py` (+~100 lines)
- `zipsa-skills/weather/.gitignore` (new)
- `tests/test_run_logger.py` (new, ~200 lines)
- `zipsa-launcher/docs/run-logging.md` (new)
