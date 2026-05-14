"""CLI for zipsa launcher."""

import json
import re
import shutil
from pathlib import Path
from typing import Annotated, Optional

import typer
from importlib.metadata import version as pkg_version
from pydantic import ValidationError

from .auth.oauth import OAuthManager
from .core.executor import DockerExecutor
from .core.renderer import OutputMode, render
from .core.skill import Skill
from .installer import install_from_github, install_local
from .paths import skill_runs_dir, installed_skill_dir, resolve_skill, skills_dir as _skills_dir, zipsa_home, SkillNotInstalledError
from .runtimes import list_runtimes


_LAUNCHER_VERSION = pkg_version("zipsa")
_RUNTIME_VERSION = (Path(__file__).parent.parent / "RUNTIME_VERSION").read_text().strip()
_DEFAULT_IMAGE = f"ghcr.io/westbrookai/zipsa-runtime:{_RUNTIME_VERSION}"

app = typer.Typer(
    name="zipsa",
    help="SKILL runtime launcher - Execute SKILLs with Claude Code, Codex, or Gemini",
    add_completion=False,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"zipsa launcher {_LAUNCHER_VERSION}")
        typer.echo(f"zipsa runtime  {_RUNTIME_VERSION}")
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
):
    """Execute a skill with the specified runtime."""
    try:
        # Load skill
        skill = Skill.load(resolve_skill(name))
        typer.echo(f"Loaded skill: {skill.name}", err=True)

        # Validate input
        if not shell and not user_input:
            typer.echo("Error: user_input is required unless --shell is specified", err=True)
            raise typer.Exit(1)

        # Parse environment variables
        env_dict = {}
        if env:
            for pair in env:
                if "=" not in pair:
                    typer.echo(f"Error: Invalid env format '{pair}' (use KEY=value)", err=True)
                    raise typer.Exit(1)
                key, value = pair.split("=", 1)
                env_dict[key] = value

        # Create executor
        executor = DockerExecutor(
            runtime=runtime,
            image=image,
        )

        # Execute skill or start shell
        output = executor.run(skill, user_input or "", env=env_dict, dry_run=dry_run, shell=shell, mcp_debug=mcp_debug, extra_docker_opts=docker_opt)

        if output is None:
            # Dry run mode
            return

        # Stream output through renderer
        render(output, output_mode)

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
    sd = _skills_dir()
    if not sd.exists():
        typer.echo("No installed skills.")
        return

    home = zipsa_home()
    home_subdirs = list(home.iterdir()) if home.exists() else []

    installed = []
    for item in sorted(sd.iterdir()):
        if not item.is_dir() and not item.is_symlink():
            continue
        manifest_path = item / "manifest.yaml"
        if not manifest_path.exists():
            continue
        try:
            skill = Skill.load(item)
        except Exception:
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
        })

    if not installed:
        typer.echo("No installed skills.")
        return

    typer.echo(f"Installed skills ({len(installed)}):\n")

    for entry in installed:
        skill = entry["skill"]
        meta = entry["meta"]

        name = typer.style(skill.name, fg=typer.colors.BRIGHT_CYAN, bold=True)
        version = typer.style(f"@{skill.manifest.metadata.version}", fg=typer.colors.CYAN)
        if entry["is_link"]:
            label = typer.style(" (linked)", fg=typer.colors.YELLOW)
        else:
            label = ""
        typer.echo(f"  {name}{version}{label}")

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
        if path:
            name = install_local(path, link=False, force=force)
            typer.echo(f"Installed {name}")
        elif link:
            name = install_local(link, link=True, force=force)
            typer.echo(f"Installed {name} (linked)")
        elif source:
            name = install_from_github(source, force=force)
            typer.echo(f"Installed {name}")
        else:
            typer.echo("Error: provide a source, --path, or --link", err=True)
            raise typer.Exit(1)
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
