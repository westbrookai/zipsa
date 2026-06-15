"""CLI for zipsa launcher."""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Annotated, Generator, Iterator, Optional

import typer
from importlib.metadata import version as pkg_version
from pydantic import ValidationError

from .auth.oauth import OAuthManager
from .core.executor import DockerExecutor
from .run_llm import run_skill_llm
from .core.install_health import check_install
from .core.renderer import OutputMode, render
from .core.requires import (
    RequiresError,
    RequiresStaleError,
    RequiresUnsetError,
    carry_over_from_previous,
    classify_state,
    load_requires,
    prompt_for_value,
    resolve_requires,
    save_requires,
)
from .core.skill import Skill
from .installer import install_from_github, install_local, _write_install_json
from .paths import skill_runs_dir, installed_skill_dir, resolve_skill, skills_dir as _skills_dir, zipsa_home, SkillNotInstalledError, skill_data_dir as _skill_data_dir, skill_requires_file
from .runtimes import list_runtimes
from .scheduling import get_scheduler


_LAUNCHER_VERSION = pkg_version("zipsa")
_RUNTIME_VERSION = (Path(__file__).parent.parent / "RUNTIME_VERSION").read_text().strip()
_DEFAULT_IMAGE = f"ghcr.io/westbrookai/zipsa-runtime:{_RUNTIME_VERSION}"


def _resolve_skill_path(name: str) -> Path:
    """Resolve an installed skill name to its directory path.

    When ZIPSA_STAGING_RUN_PATH is set in the environment, load the
    skill from that directory instead. This is the hook RunStagingSkillHandler
    uses to run an unsaved (staging) skill via the regular `zipsa run`
    pipeline — no need to duplicate Skill.load + DockerExecutor setup.
    Containment guard: the override path must resolve under ZIPSA_HOME.

    Thin wrapper around resolve_skill so tests can patch a single symbol.
    """
    staging = os.environ.get("ZIPSA_STAGING_RUN_PATH")
    if staging:
        from .paths import zipsa_home
        p = Path(staging).resolve()
        try:
            p.relative_to(zipsa_home().resolve())
        except ValueError:
            raise typer.BadParameter(
                f"ZIPSA_STAGING_RUN_PATH must be under ZIPSA_HOME: {staging}"
            )
        if not p.exists():
            raise SkillNotInstalledError(
                f"Staging path does not exist: {staging}"
            )
        return p
    return resolve_skill(name)


def _is_exec_format(skill_dir: Path) -> bool:
    """Return True for skills authored for the new LLM run-time.

    An exec-format skill has SKILL.md + zipsa-dist/ phase scripts but
    deliberately omits manifest.yaml (the legacy marker). Presence of all
    three signals an ambiguous hybrid — treat it as legacy so DockerExecutor
    can handle it with the full manifest-aware pipeline.
    """
    return (
        (skill_dir / "SKILL.md").is_file()
        and (skill_dir / "zipsa-dist").is_dir()
        and not (skill_dir / "manifest.yaml").exists()
    )


_MAX_CALL_DEPTH = 5


def _check_call_trace(skill_name: str) -> None:
    """Reject runs that would cycle or exceed depth cap.

    A parent skill's RunSkillHandler passes its own call chain as
    ZIPSA_CALL_TRACE (comma-separated skill names) and the current
    depth as ZIPSA_CALL_DEPTH. The child launcher checks these at
    startup so the rejection happens BEFORE any Docker resources are
    spent.
    """
    trace = [s for s in os.environ.get("ZIPSA_CALL_TRACE", "").split(",") if s]
    if skill_name in trace:
        chain_str = " -> ".join(trace + [skill_name])
        print(
            f"Error: skill_cycle_detected -- '{skill_name}' is already "
            f"in the call chain ({chain_str})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    depth = int(os.environ.get("ZIPSA_CALL_DEPTH", "0"))
    if depth >= _MAX_CALL_DEPTH:
        chain_str = " -> ".join(trace)
        print(
            f"Error: skill_depth_exceeded -- call depth {depth} >= cap "
            f"{_MAX_CALL_DEPTH} (chain: {chain_str})",
            file=sys.stderr,
        )
        raise SystemExit(2)


app = typer.Typer(
    name="zipsa",
    help="SKILL runtime launcher - Execute SKILLs with Claude Code, Codex, or Gemini",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"zipsa {_LAUNCHER_VERSION} (runtime {_RUNTIME_VERSION})")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-V", callback=_version_callback, is_eager=True, help="Show version and exit"),
    ] = None,
) -> None:
    pass


_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_\d{5}$")


def _validate_children(parent: Skill) -> None:
    """Validate the parent's declared spec.children.

    Missing/unloadable children are a HARD ERROR (typer.Exit(4)) — the
    orchestrator cannot complete its job without them, and discovering
    the gap mid-run wastes precheck budget on reasoning that's destined
    to fail at the first run_skill call (with child_not_installed from
    RunSkillHandler). Catch it at load-time so the user gets a clean
    install hint instead of a confused half-run.

    Budget mismatches (children's max_cost_usd / timeout_seconds sum
    exceeds parent's) stay as warnings — the run may still complete
    if the agent stays under budget, and these aren't always wrong
    (parent might intentionally over-budget for safety margin).
    """
    skills_dir = zipsa_home() / "skills"

    missing = []
    child_skills = []
    for child_name in parent.manifest.spec.children:
        path = skills_dir / child_name
        health = check_install(path)
        if not health.ok:
            missing.append((child_name, health.reason))
            continue
        try:
            child_skills.append(Skill.load(path))
        except Exception as e:
            missing.append((child_name, f"failed to load: {e}"))

    if missing:
        typer.echo(
            f"Error: {parent.name} declares children that can't be loaded:",
            err=True,
        )
        for child_name, reason in missing:
            typer.echo(f"  {child_name}: {reason}", err=True)
        typer.echo(
            f"  Run: zipsa install --link skills/<name>  (or zipsa install <source>)",
            err=True,
        )
        raise typer.Exit(4)

    parent_limits = parent.manifest.spec.limits
    if parent_limits and child_skills:
        # Budget check: cost
        sum_cost = sum(
            (c.manifest.spec.limits.max_cost_usd or 0.0)
            for c in child_skills if c.manifest.spec.limits
        )
        if parent_limits.max_cost_usd is not None and sum_cost > parent_limits.max_cost_usd:
            typer.echo(
                f"Warning: {parent.name} child cost limits don't add up.", err=True
            )
            typer.echo(
                f"  parent.max_cost_usd  = ${parent_limits.max_cost_usd:.4f}", err=True
            )
            typer.echo(f"  children sum         = ${sum_cost:.4f}", err=True)
            for c in child_skills:
                if c.manifest.spec.limits and c.manifest.spec.limits.max_cost_usd:
                    typer.echo(
                        f"    {c.name:20} = ${c.manifest.spec.limits.max_cost_usd:.4f}",
                        err=True,
                    )

        # Same for timeout
        sum_to = sum(
            (c.manifest.spec.limits.timeout_seconds or 0)
            for c in child_skills if c.manifest.spec.limits
        )
        if parent_limits.timeout_seconds is not None and sum_to > parent_limits.timeout_seconds:
            typer.echo(
                f"Warning: {parent.name} child timeouts don't add up.", err=True
            )
            typer.echo(
                f"  parent.timeout_seconds = {parent_limits.timeout_seconds}s", err=True
            )
            typer.echo(f"  children sum           = {sum_to}s", err=True)


def _find_run_dir(runs_dir: Path, run_id: Optional[str] = None) -> Path:
    """Find a run directory under runs_dir.

    If run_id is None, returns the lexicographically latest directory.
    If run_id is given, matches it as a prefix against directory names.

    Raises ValueError on missing, ambiguous, or empty runs directory.
    """
    if not runs_dir.exists():
        raise ValueError("No runs found")
    dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir() and _RUN_DIR_RE.match(d.name)])
    if not dirs:
        raise ValueError("No runs found")

    if run_id is None:
        return dirs[-1]

    matches = [d for d in dirs if d.name.startswith(run_id)]
    if not matches:
        raise ValueError(f"No run matching '{run_id}' found")
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValueError(f"Ambiguous run ID '{run_id}': matches {names}")
    return matches[0]


@app.command()
def run(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
    user_input: Annotated[
        Optional[str],
        typer.Argument(help="User input/query for the skill"),
    ] = None,
    runtime: Annotated[
        str,
        typer.Option("--runtime", "-r", help="Runtime to use (claude, codex, gemini)"),
    ] = "claude",
    image: Annotated[
        str,
        typer.Option("--image", "-i", help="Docker image to use"),
    ] = _DEFAULT_IMAGE,
    env: Annotated[
        Optional[list[str]],
        typer.Option("--env", "-e", help="Environment variables (KEY=value)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print command without executing"),
    ] = False,
    shell: Annotated[
        bool,
        typer.Option("--shell", help="Start interactive bash shell instead of running skill"),
    ] = False,
    mcp_debug: Annotated[
        bool,
        typer.Option("--mcp-debug", help="Write MCP debug logs to runs/<timestamp>/mcp-debug.log"),
    ] = False,
    docker_opt: Annotated[
        Optional[list[str]],
        typer.Option("--docker-opt", help="Extra docker run options (e.g. --docker-opt='-p 56535:56535')"),
    ] = None,
    output_mode: Annotated[
        OutputMode,
        typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
    ] = OutputMode.pretty,
    summary_to: Annotated[
        Optional[Path],
        typer.Option("--summary-to", help="Copy run summary.json to this path after the run."),
    ] = None,
    no_resume: Annotated[
        bool,
        typer.Option(
            "--no-resume",
            help=(
                "Skip the auto-detect-failed-run check. Always start "
                "from phase 0, even if the previous run failed."
            ),
        ),
    ] = False,
):
    """Execute a skill with the specified runtime."""
    # Reject cyclic invocations and depth-capped chains before any expensive work.
    _check_call_trace(name)

    # Dispatch: exec-format skills (SKILL.md + zipsa-dist/, no manifest.yaml)
    # route to the LLM run-time immediately, before Skill.load (which requires
    # manifest.yaml). Accept a filesystem path or an installed skill name.
    _skill_dir_candidate = Path(name) if Path(name).is_dir() else None
    if _skill_dir_candidate is not None and _is_exec_format(_skill_dir_candidate):
        rc = run_skill_llm(_skill_dir_candidate, user_input or "", image=image)
        raise typer.Exit(rc)

    # Resume eligibility state — resolved inside the try block below.
    resume_from: Optional[int] = None
    resume_from_run_dir: Optional[Path] = None

    try:
        # Load skill once. Reused for both the resume eligibility check
        # (when --no-resume is not set) and the main execution path.
        skill = Skill.load(_resolve_skill_path(name))
        typer.echo(f"Loaded skill: {skill.name}", err=True)

        # Resolve user_input: substitute default_query if available, else empty
        # string. The hard-fail for missing input is intentionally removed —
        # empty input is a valid signal that the agent should introduce itself
        # and elicit the request via HITL (see runtime-contract.md "Empty
        # user_query"). Note: default_query="" in the manifest is honored as an
        # explicit opt-in to the intro flow (same behavior as no default at all
        # but lets the author make the intent explicit).
        if not user_input and not shell:
            default = skill.manifest.spec.default_query
            user_input = default if default is not None else ""
        # In shell mode the substitution above is skipped; normalize None → "".
        if user_input is None:
            user_input = ""

        # Resume eligibility — auto-detect a recoverable prior run. See
        # docs/superpowers/specs/2026-05-21-resume-failed-run-design.md
        # for the behavior matrix. Runs AFTER default_query substitution
        # so current_args matches what the prior run recorded in
        # summary.user_input (which is also the post-substitution value).
        if not no_resume:
            from .core.resume import find_resumable_run, prompt_user_to_resume
            _phases = skill.manifest.spec.phases
            _phase_count = len(_phases) if isinstance(_phases, list) else 0
            candidate = find_resumable_run(
                skill=name,
                current_version=skill.manifest.metadata.version,
                current_args=user_input,
                current_phase_count=_phase_count,
            )
            if candidate is not None:
                import sys as _sys
                if _sys.stdin.isatty():
                    if prompt_user_to_resume(candidate, stdin=_sys.stdin, stdout=_sys.stderr):
                        resume_from = candidate.failed_phase_index
                        resume_from_run_dir = candidate.run_dir
                else:
                    typer.echo(
                        "Error: previous failed run found "
                        f"({candidate.run_id}, phase '{candidate.failed_phase_id}'); "
                        "pass --no-resume to start fresh, "
                        "or run interactively to resume",
                        err=True,
                    )
                    raise typer.Exit(code=2)

        # Parse environment variables
        env_dict = {}
        if env:
            for pair in env:
                if "=" not in pair:
                    typer.echo(f"Error: Invalid env format '{pair}' (use KEY=value)", err=True)
                    raise typer.Exit(1)
                key, value = pair.split("=", 1)
                env_dict[key] = value

        # Validate declared children before invoking executor (warn-only).
        if skill.manifest.spec.children:
            _validate_children(skill)

        # Resolve spec.requires values (or fail with a clear message).
        requires_values: dict[str, object] = {}
        if skill.manifest.spec.requires:
            try:
                requires_values = resolve_requires(
                    skill.name,
                    skill.manifest.metadata.version,
                    skill.manifest.spec.requires,
                    sys.stdin,
                    sys.stdout,
                    is_interactive=(
                        sys.stdin.isatty()
                        or os.environ.get("ZIPSA_FORCE_INTERACTIVE") == "1"
                    ),
                )
            except RequiresError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(4)

        # Create executor
        executor = DockerExecutor(
            runtime=runtime,
            image=image,
        )

        # Execute skill or start shell. user_input is always a string here
        # (substituted above) — no `or ""` guard needed.
        output = executor.run(skill, user_input=user_input, env=env_dict, dry_run=dry_run, shell=shell, mcp_debug=mcp_debug, extra_docker_opts=docker_opt, requires_values=requires_values, resume_from=resume_from, resume_from_run_dir=resume_from_run_dir)

        if output is None:
            # Dry run or shell mode — no exit code translation, no summary copy.
            return

        # Tee the event stream: renderer sees every event; we capture the
        # zipsa_run_complete event to translate it to a process exit code.
        # Default to infra_failed (5) in case the event never arrives.
        exit_code = 5
        run_dir_from_event: Optional[Path] = None

        def _tee(events: Iterator[dict]) -> Generator[dict, None, None]:
            nonlocal exit_code, run_dir_from_event
            for event in events:
                if event.get("type") == "zipsa_run_complete":
                    exit_code = event.get("exit_code", 5)
                yield event

        render(_tee(output), output_mode)

        # Copy summary.json to --summary-to path if requested.
        # The executor writes it to run_dir/summary.json. We find run_dir
        # by scanning the skill's runs directory for the latest run.
        if summary_to:
            data_dir = _skill_data_dir(skill.name, skill.manifest.metadata.version)
            runs_dir = data_dir / "runs"
            if runs_dir.exists():
                try:
                    run_dir_path = _find_run_dir(runs_dir)
                    src = run_dir_path / "summary.json"
                    if src.exists():
                        summary_to.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, summary_to)
                except (ValueError, OSError):
                    pass  # Quiet on any error — best effort

        raise typer.Exit(exit_code)

    except typer.Exit:
        # Re-raise Exit cleanly so it is not swallowed by the generic handler
        # below (which would print "Error: <exit-code>" as a double message).
        raise
    except KeyboardInterrupt:
        # Ctrl+C — exit 130 (canonical SIGINT convention). The executor's
        # finally already wrote summary.json with status=user_declined +
        # the cost/turns that actually got burned; print a one-liner
        # pointer to it so the user can see what they spent without
        # having to dig through ~/.zipsa/<skill>@*/runs/ themselves.
        try:
            data_dir = _skill_data_dir(skill.name, skill.manifest.metadata.version)
            runs_dir = data_dir / "runs"
            if runs_dir.exists():
                latest = _find_run_dir(runs_dir)
                typer.echo(
                    f"\nInterrupted. Summary at: {latest}/summary.json",
                    err=True,
                )
        except (NameError, ValueError, OSError, AttributeError):
            # Skill load may have failed before run_dir existed.
            pass
        raise typer.Exit(130)
    except SkillNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo(f"Error: Invalid manifest - {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


def _parse_mount_spec(spec: str) -> "tuple[Path, str]":
    """Parse a --mount value: `HOST` or `HOST:CONTAINER`.

    HOST gets ~-expansion and resolution; CONTAINER defaults to the
    resolved host path (same-path mount).
    """
    host_str, sep, container = spec.partition(":")
    host = Path(host_str).expanduser().resolve()
    return host, (container if sep else str(host))


schedule_app = typer.Typer(
    name="schedule", help="Schedule a skill to run on a cron (host scheduler).",
    no_args_is_help=True,
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command(name="add")
def schedule_add(
    path: Annotated[Path, typer.Argument(help="Skill directory to run")],
    cron: Annotated[str, typer.Option("--cron", help="Cron expression, e.g. \"0 8 * * *\"")],
    user_query: Annotated[Optional[str], typer.Argument(help="Optional query for the skill")] = None,
    mount: Annotated[Optional[list[str]], typer.Option("--mount", help="HOST[:CONTAINER] mount, repeatable (e.g. credential file)")] = None,
    image: Annotated[str, typer.Option("--image", "-i", help="Runtime image")] = _DEFAULT_IMAGE,
):
    """Register a host cron job that runs `zipsa exec <path> ...`.

    The schedule is named after the skill (the directory basename);
    scheduling the same skill again just gets the next number. The skill
    stays schedule-agnostic — this wires the OS scheduler (macOS launchd
    today) to run it. Pass the same --mount you'd use with `zipsa exec`
    for credential files.
    """
    from .scheduling import (
        CronError, SchedulerUnavailable, build_exec_command,
        resolve_zipsa_command,
    )

    zipsa = resolve_zipsa_command()
    skill_path = path.resolve()
    command = build_exec_command(
        zipsa=zipsa,
        skill_path=skill_path,
        mounts=[m for m in (mount or [])],
        query=user_query,
    )
    # exec needs a non-default image only if overridden; bake it in when set.
    if image != _DEFAULT_IMAGE:
        command += ["--image", image]

    try:
        sched = get_scheduler()
        label = sched.add(label=skill_path.name, cron=cron, command=command)
    except CronError as e:
        typer.echo(f"Error: invalid cron — {e}", err=True)
        raise typer.Exit(1)
    except SchedulerUnavailable as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Scheduled '{label}' ({cron}): {' '.join(command)}")


@schedule_app.command(name="list")
def schedule_list():
    """List scheduled zipsa jobs."""
    from .scheduling import SchedulerUnavailable

    try:
        jobs = get_scheduler().list()
    except SchedulerUnavailable as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not jobs:
        typer.echo("No scheduled skills.")
        return
    for j in jobs:
        typer.echo(f"  {j.label}  ({j.schedule})\n    {' '.join(j.command)}")


@schedule_app.command(name="remove")
def schedule_remove(
    label: Annotated[str, typer.Argument(help="The scheduled job's name")],
):
    """Remove a scheduled zipsa job."""
    from .scheduling import SchedulerUnavailable

    try:
        removed = get_scheduler().remove(label)
    except SchedulerUnavailable as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not removed:
        typer.echo(f"No scheduled job named '{label}'.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Removed scheduled job '{label}'.")


@app.command(name="create")
def create_skill(
    intent: Annotated[
        Optional[str],
        typer.Argument(help="Rough description of the agent you want to create"),
    ] = None,
    image: Annotated[
        str,
        typer.Option("--image", "-i", help="Runtime image for the authoring container"),
    ] = _DEFAULT_IMAGE,
    skills_dir: Annotated[
        Path,
        typer.Option("--skills-dir", help="Where the finished skill is promoted (default: ./skills)"),
    ] = Path("skills"),
):
    """Author a new zipsa skill, with the user, in the runtime container.

    Spawns the pinned runtime image's claude headless: it converses with
    you over HITL, writes the skill into a staging dir, tests it via the
    real `zipsa exec` (orchestrated by the host), and — once you agree on
    a name — promotes it into <skills-dir>/<name>/. The name is decided
    last; nothing lands until then.

    The authoring workflow + contract ship with the launcher, so this
    works anywhere; only Docker and a Claude login are required.
    """
    from .create import run_create

    if not intent:
        intent = typer.prompt(
            "What kind of agent would you like to create?"
        ).strip()
        if not intent:
            typer.echo("Error: no intent given.", err=True)
            raise typer.Exit(1)

    try:
        rc = run_create(
            intent, skills_dir=skills_dir.resolve(), image=image,
        )
    except FileNotFoundError:
        typer.echo(
            "Error: `docker` not found — install Docker to use `zipsa create`.",
            err=True,
        )
        raise typer.Exit(1)

    raise typer.Exit(rc)


@app.command(name="exec")
def exec_skill(
    path: Annotated[
        Path,
        typer.Argument(help="Path to a skill directory (containing zipsa-dist/)"),
    ],
    user_query: Annotated[
        Optional[str],
        typer.Argument(help="User input/query for the skill"),
    ] = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Run on the host instead of the runtime container (faster, less isolated)"),
    ] = False,
    image: Annotated[
        str,
        typer.Option("--image", "-i", help="Runtime image for docker mode"),
    ] = _DEFAULT_IMAGE,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", help="Host directory for phase artifacts (mounted at /out; default: temp dir)"),
    ] = None,
    mount: Annotated[
        Optional[list[str]],
        typer.Option("--mount", help="Host path mounted read-only in the container (repeatable). HOST mounts at the same absolute path; HOST:CONTAINER overrides the container path. No-op with --local."),
    ] = None,
):
    """Run a skill's phases deterministically (Phase 1).

    Phase files in zipsa-dist/ run sequentially inside the zipsa
    runtime container by default (skill mounted read-only, shared
    artifacts via /out, each phase's result chained into the next
    phase's `prev`). No LLM, no manifest. Result prints as JSON.
    """
    from .core.phase_discovery import PhaseDiscoveryError, discover_phases
    from .exec_runner import (
        ExecRunnerError,
        new_run_dir,
        run_phases,
        write_run_record,
    )

    if not path.is_dir():
        typer.echo(f"Error: skill directory not found: {path}", err=True)
        raise typer.Exit(1)

    try:
        phases = discover_phases(path)
    except PhaseDiscoveryError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    skill_root = path.resolve()
    # One run dir per invocation holds result.json + stdout/stderr logs
    # + artifacts/ (unless --out redirects artifacts elsewhere).
    # Scheduled runs are otherwise invisible — this is the only record
    # they leave behind.
    run_dir = new_run_dir(skill_root.name)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        artifacts_dir = out
    else:
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        results = run_phases(
            phases,
            skill_name=skill_root.name,
            user_query=user_query or "",
            out_dir=artifacts_dir,
            skill_root=skill_root,
            docker_image=None if local else image,
            extra_mounts=[_parse_mount_spec(m) for m in mount or []],
        )
    except ExecRunnerError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    last = results[-1]
    summary = {
        "skill_name": last.skill_name,
        "mode": last.mode,
        "result": last.result,
        "exit_code": last.exit_code,
        "duration_ms": sum(r.duration_ms for r in results),
        "out_dir": last.out_dir,
        "run_dir": str(run_dir),
        "phases": [
            {
                "id": p.id_str,
                "slug": p.slug,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
            }
            for p, r in zip(phases, results)
        ],
    }
    write_run_record(
        run_dir, summary, results,
        phases=[(p.id_str, p.slug) for p in phases[:len(results)]],
    )

    if last.exit_code != 0:
        failed_phase = phases[len(results) - 1]
        typer.echo(
            f"Phase {failed_phase.id_str}.{failed_phase.slug} failed "
            f"(exit {last.exit_code}):\n{last.stderr}",
            err=True,
        )
        if (
            last.mode == "docker"
            and "/skill" in last.stderr
            and "No such file" in last.stderr
        ):
            typer.echo(
                "Hint: the skill path may be outside Docker Desktop's "
                "file sharing list (Settings → Resources → File Sharing) "
                "— such mounts come up empty inside the container. "
                "Move the skill under a shared path (e.g. /Users) or "
                "run with --local.",
                err=True,
            )
        raise typer.Exit(last.exit_code)

    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))


@app.command()
def view(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
    run_id: Annotated[
        Optional[str],
        typer.Argument(help="Run ID prefix to replay (default: latest run)"),
    ] = None,
    output_mode: Annotated[
        OutputMode,
        typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
    ] = OutputMode.pretty,
):
    """Replay the output of a past skill run."""
    try:
        skill = Skill.load(resolve_skill(name))
        runs_dir = skill_runs_dir(skill.name, skill.manifest.metadata.version)
        run_dir = _find_run_dir(runs_dir, run_id)
    except SkillNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo(f"Error: Invalid manifest - {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    output_jsonl = run_dir / "output.jsonl"
    if not output_jsonl.exists():
        typer.echo(f"Run '{run_dir.name}' has no output.jsonl", err=True)
        raise typer.Exit(1)

    def events():
        with open(output_jsonl) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        typer.echo(f"Warning: skipped malformed line in {output_jsonl}", err=True)

    render(events(), output_mode)


@app.command()
def validate(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
):
    """Validate a skill manifest."""
    try:
        skill = Skill.load(resolve_skill(name))
        typer.echo(f"✓ Skill '{skill.name}' is valid")
        typer.echo(f"  Version: {skill.manifest.metadata.version}")
        typer.echo(f"  Purpose: {skill.manifest.spec.purpose}")
        typer.echo(f"  MCP Servers: {len(skill.manifest.spec.mcp)}")
        tool_count = len(skill.manifest.spec.tools.builtin)
        typer.echo(f"  Tools: {tool_count}")

    except SkillNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo("✗ Validation failed:", err=True)
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error["loc"])
            typer.echo(f"  {loc}: {error['msg']}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command(name="list")
def list_installed():
    """List installed skills with run statistics."""
    from .paths import builtin_skills_root

    home = zipsa_home()
    sd = home / "skills"

    home_subdirs = list(home.iterdir()) if home.exists() else []

    installed = []
    broken: list[tuple[str, str]] = []  # (name, reason) for broken entries

    def _add_entry(item: Path, *, is_builtin: bool) -> None:
        """Inner helper — same enumeration logic for both user-installed
        and built-in skills. is_builtin only changes the display tag."""
        health = check_install(item)
        if not health.ok:
            broken.append((item.name, health.reason or "unknown reason"))
            return
        try:
            skill = Skill.load(item)
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            broken.append((item.name, f"{loc}: {first['msg']}"))
            return
        except Exception as e:
            broken.append((item.name, str(e).splitlines()[0]))
            return

        install_json = item / "_install.json"
        install_meta = {}
        if install_json.exists():
            try:
                install_meta = json.loads(install_json.read_text())
            except Exception:
                pass

        # Compute run stats (same logic as before)
        total_runs = 0
        successful_runs = 0
        for run_data_dir in home_subdirs:
            if not run_data_dir.is_dir():
                continue
            if not run_data_dir.name.startswith(f"{skill.name}@"):
                continue
            runs_dir = run_data_dir / "runs"
            if not runs_dir.exists():
                continue
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                summary_file = run_dir / "summary.json"
                if summary_file.exists():
                    try:
                        s = json.loads(summary_file.read_text())
                    except Exception:
                        continue
                    total_runs += 1
                    if s.get("status") == "ok":
                        successful_runs += 1
                else:
                    meta_file = run_dir / "metadata.json"
                    if not meta_file.exists():
                        continue
                    try:
                        meta = json.loads(meta_file.read_text())
                    except Exception:
                        continue
                    total_runs += 1
                    if not meta.get("is_error", True):
                        successful_runs += 1

        installed.append({
            "skill": skill,
            "meta": install_meta,
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "is_link": item.is_symlink(),
            "is_builtin": is_builtin,
            "item": item,
            "health": health,
        })

    # 1) User-installed skills under ~/.zipsa/skills/
    if sd.exists():
        for item in sorted(sd.iterdir()):
            if not item.is_dir() and not item.is_symlink():
                continue
            _add_entry(item, is_builtin=False)

    # 2) Built-in skills bundled with the launcher package. Same
    #    enumeration / display flow, just tagged "(built-in)".
    builtin_root = builtin_skills_root()
    if builtin_root.exists():
        for item in sorted(builtin_root.iterdir()):
            if not item.is_dir():
                continue
            if item.name.startswith("__"):  # skip __pycache__ etc
                continue
            _add_entry(item, is_builtin=True)

    total_count = len(installed) + len(broken)
    if total_count == 0:
        typer.echo("No installed skills.")
        return

    typer.echo(f"Installed skills ({total_count}):\n")

    for entry in installed:
        skill = entry["skill"]
        meta = entry["meta"]
        health = entry["health"]

        name = typer.style(skill.name, fg=typer.colors.BRIGHT_CYAN, bold=True)
        version = typer.style(f"@{skill.manifest.metadata.version}", fg=typer.colors.CYAN)
        if entry["is_builtin"]:
            label = typer.style(" (built-in)", fg=typer.colors.BLUE)
        elif entry["is_link"]:
            label = typer.style(" (linked)", fg=typer.colors.YELLOW)
        else:
            label = ""

        # Orchestrator label: any skill that declares spec.children
        # is a composer. Surfacing this at-a-glance + listing the
        # children with their installed versions lets the user spot
        # missing children before runtime (vs the exit-4 from #83
        # which only fires at `zipsa run`).
        children_names: list[str] = list(skill.manifest.spec.children or [])
        orch_label = typer.style(" (orchestrator)", fg=typer.colors.BRIGHT_MAGENTA) if children_names else ""

        if health.requires_total > 0 and health.requires_set < health.requires_total:
            warn = typer.style(
                f"  ⚠ needs configure ({health.requires_total} required, "
                f"{health.requires_set} set)",
                fg=typer.colors.YELLOW,
            )
        else:
            warn = ""

        typer.echo(f"  {name}{version}{orch_label}{label}{warn}")

        if entry["total_runs"] == 0:
            typer.echo(typer.style("    never run", fg=typer.colors.BRIGHT_BLACK))
        else:
            success_pct = int(entry["successful_runs"] / entry["total_runs"] * 100)
            pct_color = typer.colors.GREEN if success_pct >= 80 else typer.colors.YELLOW if success_pct >= 50 else typer.colors.RED
            runs_str = typer.style(f"{entry['total_runs']} runs", fg=typer.colors.WHITE)
            pct_str = typer.style(f"{success_pct}% success", fg=pct_color)
            typer.echo(f"    {runs_str} · {pct_str}")

        # Children tree (orchestrators only). Box-drawing prefixes:
        # ├─ for non-last, └─ for last. Each line shows the child's
        # installed @version or a red "not installed" marker.
        if children_names:
            for idx, child_name in enumerate(children_names):
                is_last = idx == len(children_names) - 1
                prefix = "└─" if is_last else "├─"
                child_dir = sd / child_name
                child_health = check_install(child_dir) if child_dir.exists() else None
                if child_health is None or not child_health.ok:
                    marker = typer.style("✗ not installed", fg=typer.colors.RED)
                    typer.echo(
                        f"    {typer.style(prefix, fg=typer.colors.BRIGHT_BLACK)} "
                        f"{typer.style(child_name, fg=typer.colors.WHITE)}  {marker}"
                    )
                else:
                    try:
                        child_skill = Skill.load(child_dir)
                        child_ver = child_skill.manifest.metadata.version
                        ver_str = typer.style(f"@{child_ver}", fg=typer.colors.CYAN)
                        ok = typer.style("✓", fg=typer.colors.GREEN)
                        typer.echo(
                            f"    {typer.style(prefix, fg=typer.colors.BRIGHT_BLACK)} "
                            f"{typer.style(child_name, fg=typer.colors.WHITE)}{ver_str}  {ok}"
                        )
                    except Exception as e:
                        err = typer.style(f"✗ load failed: {str(e).splitlines()[0]}", fg=typer.colors.RED)
                        typer.echo(
                            f"    {typer.style(prefix, fg=typer.colors.BRIGHT_BLACK)} "
                            f"{typer.style(child_name, fg=typer.colors.WHITE)}  {err}"
                        )

        if entry["is_link"]:
            path_str = typer.style(str(entry["item"].resolve()), fg=typer.colors.BRIGHT_BLACK)
            typer.echo(f"    {typer.style('Linked from:', fg=typer.colors.BRIGHT_BLACK)} {path_str}")
        elif meta.get("source"):
            src_str = typer.style(meta["source"], fg=typer.colors.BRIGHT_BLACK)
            typer.echo(f"    {typer.style('Source:', fg=typer.colors.BRIGHT_BLACK)} {src_str}")

        typer.echo()

    for entry_name, reason in broken:
        typer.echo(f"  {entry_name}  ✗ broken")
        typer.echo(f"    {reason}")
        typer.echo(f"    Fix: zipsa install --link <new-path>  (or: zipsa uninstall {entry_name})")
        typer.echo()


@app.command(name="where")
def where(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
):
    """Print the install directory of a named skill.

    For linked installs, returns the source path the symlink points
    to (so editing the file there persists). For copy installs,
    returns the install dir itself.

    Composable with shell — e.g.

        cat $(zipsa where my-skill)/manifest.yaml
        vim $(zipsa where my-skill)/SKILL.md

    avoids having to type the long ~/.zipsa/skills/... path.
    """
    install_dir = installed_skill_dir(name)
    if not install_dir.exists():
        typer.echo(f"Error: skill '{name}' not installed", err=True)
        raise typer.Exit(1)
    # symlink → resolve to real source; regular dir → return as-is.
    typer.echo(str(install_dir.resolve()))


@app.command(name="discover")
def discover(
    skills_dir: Annotated[
        str,
        typer.Argument(help="Directory containing skills"),
    ] = ".",
):
    """Scan a directory and list all skills found."""
    try:
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            typer.echo(f"Error: Directory '{skills_dir}' not found", err=True)
            raise typer.Exit(1)

        if not skills_path.is_dir():
            typer.echo(f"Error: '{skills_dir}' is not a directory", err=True)
            raise typer.Exit(1)

        # Find all skill directories
        skills = []
        for item in skills_path.iterdir():
            if not item.is_dir():
                continue

            # Check if manifest.yaml exists
            manifest_path = item / "manifest.yaml"
            if not manifest_path.exists():
                continue

            try:
                skill = Skill.load(item)
                skills.append({
                    "name": skill.name,
                    "version": skill.manifest.metadata.version,
                    "purpose": skill.manifest.spec.purpose,
                    "path": item,
                })
            except Exception:
                # Skip invalid skills
                continue

        if not skills:
            typer.echo("No skills found")
            return

        # Print skills table
        typer.echo(f"Found {len(skills)} skill(s):\n")
        for skill in skills:
            typer.echo(f"  {skill['name']} (v{skill['version']})")
            typer.echo(f"    {skill['purpose']}")
            typer.echo(f"    Path: {skill['path']}")
            typer.echo()

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def configure(
    name: Annotated[str, typer.Argument(help="Installed skill name")],
):
    """Set host-side values that the skill needs to run (spec.requires)."""
    try:
        skill = Skill.load(_resolve_skill_path(name))
    except SkillNotInstalledError:
        typer.echo(f"Error: skill '{name}' is not installed. Try: zipsa install <source>", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error loading {name!r}: {e}", err=True)
        raise typer.Exit(1)

    spec = skill.manifest.spec.requires
    if not spec:
        typer.echo(f"{name} has no required configuration.")
        raise typer.Exit(0)

    typer.echo(f"\n[zipsa] {name}@{skill.manifest.metadata.version}\n")

    req_file = skill_requires_file(name, skill.manifest.metadata.version)
    saved = load_requires(req_file) if req_file.exists() else {}

    # Use sys.stdin/stdout lazily at call site so CliRunner can patch them.
    stream_in = sys.stdin
    stream_out = sys.stdout

    new_values: dict[str, object] = dict(saved)  # start from existing
    try:
        for key, entry in spec.items():
            typer.echo(f"{key} — {entry.prompt}")
            current = saved.get(key)
            try:
                value = prompt_for_value(entry, stream_in, stream_out, current=current)
            except EOFError:
                typer.echo("Error: configure requires an interactive terminal.", err=True)
                raise typer.Exit(4)
            except ValueError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            new_values[key] = value
            if isinstance(value, list):
                typer.echo(f"  ✓ saved {len(value)} item(s)")
            else:
                typer.echo(f"  ✓ saved")
            typer.echo()
    except KeyboardInterrupt:
        typer.echo("\nCancelled. No changes saved.")
        raise typer.Exit(130)

    save_requires(req_file, new_values)
    typer.echo(f"Saved to {req_file}")


@app.command()
def runtimes():
    """List available runtimes."""
    available = list_runtimes()

    typer.echo("Available runtimes:\n")
    for runtime_name in available:
        typer.echo(f"  - {runtime_name}")


@app.command()
def install(
    source: Annotated[
        Optional[str],
        typer.Argument(help=r"GitHub source: user/repo\[/subpath]\[@ref]"),
    ] = None,
    path: Annotated[
        Optional[str],
        typer.Option("--path", help="Install local skill by copy"),
    ] = None,
    link: Annotated[
        Optional[str],
        typer.Option("--link", help="Install local skill by symlink"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite if already installed"),
    ] = False,
):
    """Install a skill from GitHub or a local directory."""
    if sum(bool(x) for x in [source, path, link]) > 1:
        typer.echo("Error: --path, --link, and source are mutually exclusive", err=True)
        raise typer.Exit(1)

    try:
        if path or link:
            local_source = path or link
            is_link = bool(link)
            suffix = " (linked)" if is_link else ""

            # Peek at the manifest to get the skill name so we can check
            # whether an existing entry at that name is broken.  If the
            # source path doesn't exist yet (e.g. in unit tests that mock
            # install_local) this will raise FileNotFoundError and we fall
            # through to the normal install_local delegation below.
            try:
                src_path = Path(local_source).resolve()
                peeked_skill = Skill.load(src_path)
                skill_name = peeked_skill.name
                skill_version = peeked_skill.manifest.metadata.version
                dest = zipsa_home() / "skills" / skill_name

                if dest.exists() or dest.is_symlink():
                    health = check_install(dest)
                    if not health.ok:
                        # Broken entry — replace transparently, no --force needed.
                        if dest.is_symlink() or dest.is_file():
                            dest.unlink()
                        else:
                            shutil.rmtree(dest)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if is_link:
                            dest.symlink_to(src_path)
                        else:
                            shutil.copytree(src_path, dest)
                        _write_install_json(dest, str(src_path), "local", skill_version, "link" if is_link else "copy")
                        typer.echo(f"Replaced broken link: {skill_name}{suffix}")
                        return
                    elif not force:
                        typer.echo(
                            f"Error: Skill '{skill_name}' is already installed. Use --force to overwrite.",
                            err=True,
                        )
                        raise typer.Exit(1)
                    # else: healthy + --force → fall through to install_local

            except typer.Exit:
                raise
            except (FileNotFoundError, ValidationError):
                # Source not loadable (e.g. fake path in unit tests that
                # mock install_local) — delegate to install_local directly.
                pass

            name = install_local(local_source, link=is_link, force=force)
            typer.echo(f"Installed {name}{suffix}")

        elif source:
            name = install_from_github(source, force=force)
            typer.echo(f"Installed {name}")
        else:
            typer.echo("Error: provide a source, --path, or --link", err=True)
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def uninstall(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
):
    """Uninstall a skill (preserves run history)."""
    dest = installed_skill_dir(name)
    if not dest.exists() and not dest.is_symlink():
        typer.echo(f"Error: Skill '{name}' is not installed.", err=True)
        raise typer.Exit(1)

    try:
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    except OSError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Uninstalled {name}")


@app.command()
def connect(
    server_name: Annotated[
        str,
        typer.Argument(help="MCP server name to authorize (e.g. notion, github)"),
    ],
):
    """Pre-authorize OAuth credentials for an MCP server.

    Scans all installed skills for a server with the given name and initiates
    OAuth authorization. Credentials are stored per server and reused across skills.

    Example: zipsa connect notion
    """
    # Scan installed skills for an OAuth server matching server_name
    skills_root = _skills_dir()
    matched_server = None

    if skills_root.exists():
        for skill_dir in sorted(skills_root.iterdir()):
            try:
                skill = Skill.load(skill_dir)
                for s in skill.manifest.spec.mcp:
                    if (
                        s.name == server_name
                        and s.type == "http"
                        and getattr(s, "auth", None)
                        and s.auth.type == "oauth2"
                    ):
                        matched_server = s
                        break
            except Exception:
                continue
            if matched_server:
                break

    if not matched_server:
        typer.echo(
            f"Error: No installed skill has an OAuth2 MCP server named '{server_name}'.",
            err=True,
        )
        typer.echo(
            "Install a skill that uses this server first, e.g.:",
            err=True,
        )
        typer.echo(
            f"  zipsa install <source>",
            err=True,
        )
        raise typer.Exit(1)

    try:
        manager = OAuthManager()
        typer.echo(f"Authorizing {server_name}...")
        manager.ensure_credentials(matched_server.name, matched_server.url)
        typer.echo(f"✓ {server_name}: authorized")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# Subcommands registered on the top-level `app`. Used by the
# skill-name shortcut to distinguish "user typed a real subcommand"
# from "user typed a skill name expecting implicit `run`".
#
# Keep in sync with @app.command(name=...) decorators above.
_KNOWN_COMMANDS = frozenset({
    "run", "exec", "create", "schedule", "view", "validate", "list", "where",
    "discover", "configure", "runtimes", "install", "uninstall", "connect",
})


def _rewrite_argv_for_skill_shortcut(
    argv: list[str],
    skill_installed,  # Callable[[str], bool]
) -> "tuple[list[str], Optional[str]]":
    """Implement the `zipsa <skill-name>` → `zipsa run <skill-name>` sugar.

    Returns `(possibly-rewritten-argv, optional-notice-for-stderr)`.

    The shortcut fires only when ALL of the following hold:
      - argv has at least 2 elements (program + subcommand)
      - argv[1] is not a known subcommand (so typos still hit typer's
        normal "No such command" error path)
      - argv[1] is not a flag (`-V`, `--help`, etc. go to typer)
      - argv[1] is a plain identifier (no slashes / leading `.` or `~`
        — paths can't be skill names, must use explicit `zipsa run`)
      - `skill_installed(argv[1])` returns True

    On a successful rewrite the notice tells the user what just happened
    and shows the canonical form so they learn it by example.
    """
    if len(argv) < 2:
        return argv, None
    candidate = argv[1]
    if candidate.startswith("-"):
        return argv, None
    if candidate in _KNOWN_COMMANDS:
        return argv, None
    # Reject anything path-shaped — skill names are simple identifiers.
    if "/" in candidate or candidate.startswith(".") or candidate.startswith("~"):
        return argv, None
    if not skill_installed(candidate):
        return argv, None
    rest = " ".join(argv[2:])
    canonical = f"zipsa run {candidate}{(' ' + rest) if rest else ''}"
    notice = (
        f"[zipsa] '{candidate}' is an installed skill — running as: {canonical}"
    )
    return [argv[0], "run", *argv[1:]], notice


def main():
    """Entry point for CLI."""
    # `zipsa <skill-name>` sugar. Detects the shortcut on sys.argv,
    # rewrites in place, prints a notice to stderr so the user sees
    # the canonical `zipsa run <name>` form. Then typer dispatches
    # normally — same code path as if the user had typed `run` themselves.
    from .paths import installed_skill_dir
    new_argv, notice = _rewrite_argv_for_skill_shortcut(
        sys.argv,
        skill_installed=lambda name: installed_skill_dir(name).exists(),
    )
    if notice is not None:
        print(notice, file=sys.stderr)
        sys.argv = new_argv
    app()


if __name__ == "__main__":
    main()
