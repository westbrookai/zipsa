"""End-to-end resume verification.

These tests actually spawn the executor against the two-phase-fail
fixture. Marked @pytest.mark.integration so they can be excluded from
fast unit-test runs."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

_REAL_ZIPSA_HOME = Path.home() / ".zipsa"

# pytest's tmp_path lands in /var/folders on macOS, which colima (the
# Docker VM) does not mount. We need a ZIPSA_HOME under $HOME so Docker
# can bind-mount skill data dirs into the container.
_ZIPSA_TEST_ROOT = Path.home() / ".zipsa-e2e-tests"


def _fixture_path() -> Path:
    return Path(__file__).parent / "fixtures/skills/two-phase-fail"


def _seed_credentials(home: Path) -> None:
    """Copy the global ~/.zipsa/.env into home so Docker gets credentials."""
    real_env = _REAL_ZIPSA_HOME / ".env"
    if real_env.exists():
        shutil.copy2(real_env, home / ".env")


@pytest.fixture
def e2e_home(request):
    """Provide an isolated ZIPSA_HOME under $HOME/.zipsa-e2e-tests/<test-name>.

    Colima (macOS Docker VM) only mounts $HOME by default, so this path
    is reachable from inside containers. The directory is removed before
    and after the test to guarantee isolation across repeated runs.
    """
    test_name = request.node.name.replace("/", "_")[:60]
    home = _ZIPSA_TEST_ROOT / test_name
    # Pre-clean in case a previous run left stale data (e.g. after a crash
    # or keyboard interrupt that skipped the teardown).
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def installed_fixture(e2e_home, monkeypatch):
    """Install the two-phase-fail fixture into an isolated ZIPSA_HOME.

    Uses a path under $HOME so Docker (via colima) can bind-mount skill
    data directories into the container. Copies the real ~/.zipsa/.env
    (which carries CLAUDE_CODE_OAUTH_TOKEN) so the container can auth.
    """
    monkeypatch.setenv("ZIPSA_HOME", str(e2e_home))
    _seed_credentials(e2e_home)
    env = {**os.environ, "ZIPSA_HOME": str(e2e_home)}
    subprocess.run(
        ["uv", "run", "zipsa", "install", "--link", str(_fixture_path())],
        check=True, env=env,
    )
    yield e2e_home


_SKILL_QUERY = "go"  # matches default_query in manifest; pass explicitly so
# user_input is stored as "go" in summary.json AND compared as "go" in the
# resume check (the CLI uses user_input or "" before default_query expansion).


def _run_skill(home: Path, extra_args: list[str] | None = None,
               **kwargs) -> subprocess.CompletedProcess:
    """Invoke 'zipsa run two-phase-fail <_SKILL_QUERY> [extra_args]'."""
    cmd = ["uv", "run", "zipsa", "run", "two-phase-fail", _SKILL_QUERY]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd, env={**os.environ, "ZIPSA_HOME": str(home)}, **kwargs
    )


def test_state_json_written_for_phase1(installed_fixture):
    """First run: phase 1 should succeed (state.json present), phase 2
    fails (no state.json)."""
    home = installed_fixture
    r = _run_skill(home, ["--no-resume"], capture_output=True)
    assert r.returncode != 0  # phase 2 fails

    # Locate the run dir
    runs_dir = home / "two-phase-fail@0.1.0" / "runs"
    runs = sorted(runs_dir.iterdir())
    assert runs
    run_dir = runs[-1]
    # Phase 1 wrote state.json
    p1 = run_dir / "phases" / "0-succeed" / "state.json"
    assert p1.exists()
    envelope = json.loads(p1.read_text())
    assert envelope["next_phase_input"] == {"phase1_done": True, "marker": "abc"}
    # Phase 2 wrote no state.json
    p2 = run_dir / "phases" / "1-fail" / "state.json"
    assert not p2.exists()


def test_second_run_with_no_resume_starts_fresh(installed_fixture):
    """Two consecutive --no-resume invocations should not see each
    other — both run phase 1 + 2."""
    home = installed_fixture
    for _ in range(2):
        _run_skill(home, ["--no-resume"])
    runs = sorted((home / "two-phase-fail@0.1.0" / "runs").iterdir())
    assert len(runs) == 2
    for r in runs:
        # Each run executed phase 1 (state.json present)
        assert (r / "phases" / "0-succeed" / "state.json").exists()


def test_non_interactive_without_no_resume_exits_2(installed_fixture):
    """After a failed run, a follow-up without --no-resume and without
    a TTY should exit 2 (refusing to silently resume or start fresh)."""
    home = installed_fixture
    # First run — fails (produces a resumable candidate)
    _run_skill(home, ["--no-resume"])
    # Second run — no --no-resume, no TTY (subprocess gets no tty)
    r = _run_skill(home, capture_output=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 2
    assert b"previous failed run found" in r.stderr
