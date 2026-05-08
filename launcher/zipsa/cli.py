"""CLI for zipsa launcher."""

import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError

from .core.executor import DockerExecutor
from .core.renderer import OutputMode, render
from .core.skill import Skill
from .runtimes import list_runtimes


app = typer.Typer(
    name="zipsa",
    help="SKILL runtime launcher - Execute SKILLs with Claude Code, Codex, or Gemini",
    add_completion=False,
)


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
    skill_dir: Annotated[
        str,
        typer.Argument(help="Path to skill directory or manifest.yaml"),
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
    ] = "ghcr.io/westbrookai/zipsa-runtime:latest",
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
        skill = Skill.load(skill_dir)
        typer.echo(f"Loaded skill: {skill.name}")

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
def validate(
    skill_dir: Annotated[
        str,
        typer.Argument(help="Path to skill directory or manifest.yaml"),
    ],
):
    """Validate a skill manifest."""
    try:
        skill = Skill.load(skill_dir)
        typer.echo(f"✓ Skill '{skill.name}' is valid")
        typer.echo(f"  Version: {skill.manifest.metadata.version}")
        typer.echo(f"  Purpose: {skill.manifest.spec.purpose}")
        typer.echo(f"  MCP Servers: {len(skill.manifest.spec.mcp)}")
        tool_count = len(skill.manifest.spec.tools.builtin) + len(skill.manifest.spec.tools.mcp)
        typer.echo(f"  Tools: {tool_count}")

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
def list_skills(
    skills_dir: Annotated[
        str,
        typer.Argument(help="Directory containing skills"),
    ] = ".",
):
    """List all skills in a directory."""
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


def main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    main()
