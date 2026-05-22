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


_LAUNCHER_VERSION = pkg_version("zipsa")
_RUNTIME_VERSION = (Path(__file__).parent.parent / "RUNTIME_VERSION").read_text().strip()
_DEFAULT_IMAGE = f"ghcr.io/westbrookai/zipsa-runtime:{_RUNTIME_VERSION}"


def _resolve_skill_path(name: str) -> Path:
    """Resolve an installed skill name to its directory path.

    Thin wrapper around resolve_skill so tests can patch a single symbol.
    """
    return resolve_skill(name)


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
    """Warn (on stderr) about (a) declared children that aren't installed,
    (b) sum of children's max_cost_usd / timeout_seconds exceeding
    parent's. Never raises."""
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
            f"Warning: {parent.name} declares children that can't be loaded:", err=True
        )
        for child_name, reason in missing:
            typer.echo(f"  {child_name}: {reason}", err=True)

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
                    is_interactive=sys.stdin.isatty(),
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
        # Ctrl+C — exit 130 (canonical SIGINT convention).
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
    home = zipsa_home()
    sd = home / "skills"
    if not sd.exists():
        typer.echo("No installed skills.")
        return

    home_subdirs = list(home.iterdir()) if home.exists() else []

    installed = []
    broken: list[tuple[str, str]] = []  # (name, reason) for broken entries
    for item in sorted(sd.iterdir()):
        if not item.is_dir() and not item.is_symlink():
            continue

        health = check_install(item)
        if not health.ok:
            broken.append((item.name, health.reason or "unknown reason"))
            continue

        try:
            skill = Skill.load(item)
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            broken.append((item.name, f"{loc}: {first['msg']}"))
            continue
        except Exception as e:
            broken.append((item.name, str(e).splitlines()[0]))
            continue

        install_json = item / "_install.json"
        install_meta = {}
        if install_json.exists():
            try:
                install_meta = json.loads(install_json.read_text())
            except Exception:
                pass

        # Compute run stats from zipsa_home()/<name>@<version>/runs/
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
                # Prefer summary.json (single source of truth). Fall back
                # to legacy metadata.json for pre-consolidation runs.
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
            "item": item,
            "health": health,  # NEW
        })

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
        if entry["is_link"]:
            label = typer.style(" (linked)", fg=typer.colors.YELLOW)
        else:
            label = ""

        if health.requires_total > 0 and health.requires_set < health.requires_total:
            warn = typer.style(
                f"  ⚠ needs configure ({health.requires_total} required, "
                f"{health.requires_set} set)",
                fg=typer.colors.YELLOW,
            )
        else:
            warn = ""

        typer.echo(f"  {name}{version}{label}{warn}")

        if entry["total_runs"] == 0:
            typer.echo(typer.style("    never run", fg=typer.colors.BRIGHT_BLACK))
        else:
            success_pct = int(entry["successful_runs"] / entry["total_runs"] * 100)
            pct_color = typer.colors.GREEN if success_pct >= 80 else typer.colors.YELLOW if success_pct >= 50 else typer.colors.RED
            runs_str = typer.style(f"{entry['total_runs']} runs", fg=typer.colors.WHITE)
            pct_str = typer.style(f"{success_pct}% success", fg=pct_color)
            typer.echo(f"    {runs_str} · {pct_str}")

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


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
