"""CLI for zipsa launcher."""

import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError

from .core.executor import DockerExecutor
from .core.skill import Skill
from .runtimes import list_runtimes


app = typer.Typer(
    name="zipsa",
    help="SKILL runtime launcher - Execute SKILLs with Claude Code, Codex, or Gemini",
    add_completion=False,
)


@app.command()
def run(
    skill_dir: Annotated[
        str,
        typer.Argument(help="Path to skill directory or manifest.yaml"),
    ],
    user_input: Annotated[
        str,
        typer.Argument(help="User input/query for the skill"),
    ],
    runtime: Annotated[
        str,
        typer.Option("--runtime", "-r", help="Runtime to use (claude, codex, gemini)"),
    ] = "claude",
    image: Annotated[
        str,
        typer.Option("--image", "-i", help="Docker image to use"),
    ] = "ghcr.io/westbrookai/zipsa-runtime:latest",
    workspace: Annotated[
        Optional[Path],
        typer.Option("--workspace", "-w", help="Workspace directory"),
    ] = None,
    env: Annotated[
        Optional[list[str]],
        typer.Option("--env", "-e", help="Environment variables (KEY=value)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print command without executing"),
    ] = False,
):
    """Execute a skill with the specified runtime."""
    try:
        # Load skill
        skill = Skill.load(skill_dir)
        typer.echo(f"Loaded skill: {skill.name}")

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
            workspace=workspace or Path.cwd(),
        )

        # Execute skill
        output = executor.run(skill, user_input, env=env_dict, dry_run=dry_run)

        if output is None:
            # Dry run mode
            return

        # Stream output
        for event in output:
            if event.get("type") == "text":
                typer.echo(event.get("content", ""))
            else:
                # For other event types, print raw JSON for debugging
                import json
                typer.echo(json.dumps(event))

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
