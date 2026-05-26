"""SkillValidatorHandler — validate a draft skill directory.

Powers the `mcp__zipsa__validate_skill` MCP tool. Skill-builder calls
it right after writing the draft so it can either confirm "ready to
install" to the author or feed the errors back into iteration.

What this checks is the same thing `zipsa validate` checks at the CLI
(Skill.load + pydantic), just returning JSON instead of printing.
Containment guard: only paths under ZIPSA_HOME (typically staging/)
are allowed — the agent has no business validating arbitrary host
paths through this tool.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths as zipsa_paths


class SkillValidatorHandler:
    def validate(self, *, path: str) -> dict:
        """Validate a skill directory and return a structured result.

        Returns: {ok: bool, errors: [str], name?: str, version?: str}.
        Raises RuntimeError("SKILL_PATH_OUTSIDE_HOME: ...") when the
        target path doesn't resolve under ZIPSA_HOME.
        """
        from pydantic import ValidationError
        from .skill import Skill

        p = Path(path).resolve()
        home = zipsa_paths.zipsa_home().resolve()
        try:
            p.relative_to(home)
        except ValueError as e:
            raise RuntimeError(
                f"SKILL_PATH_OUTSIDE_HOME: {path}"
            ) from e

        try:
            skill = Skill.load(p)
        except FileNotFoundError as e:
            return {"ok": False, "errors": [str(e)]}
        except ValidationError as e:
            errors = []
            for err in e.errors():
                loc = " -> ".join(str(x) for x in err["loc"])
                errors.append(f"{loc}: {err['msg']}")
            return {"ok": False, "errors": errors}
        except Exception as e:
            return {"ok": False, "errors": [f"{type(e).__name__}: {e}"]}

        return {
            "ok": True,
            "name": skill.name,
            "version": skill.manifest.metadata.version,
            "errors": [],
        }
