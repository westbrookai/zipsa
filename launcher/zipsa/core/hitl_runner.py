"""HitlServer — runs an HTTP MCP server in a daemon thread for one
zipsa run. Owns port allocation, per-run Bearer token, and start/stop
lifecycle. Tool wiring is added in a later task; for now the server
exposes the bare framework so port/token can be asserted in tests."""

from __future__ import annotations

import functools
import inspect
import logging
import secrets
import socket
import threading
from typing import Optional


_mcp_log = logging.getLogger("mcp.zipsa")
# Force INFO-level stderr output regardless of root log config. uvicorn
# only configures its own loggers; without our own handler the propagated
# log silently drops at the WARNING-default root.
if not _mcp_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[mcp] %(message)s"))
    _mcp_log.addHandler(_h)
    _mcp_log.setLevel(logging.INFO)
    _mcp_log.propagate = False


def _logged(fn):
    """Wrap an MCP tool function to log one INFO line per invocation.

    Apply BELOW @mcp.tool() so FastMCP's introspection of the wrapped
    function still sees the original signature via __wrapped__ (which
    inspect.signature follows by default). String args > 80 chars are
    truncated in the log; nothing is redacted (the MCP token is the
    only real secret and is never passed as a tool arg).
    """
    name = fn.__name__

    def _format(kwargs: dict) -> str:
        # Identify the caller via the contextvar set by
        # CallerContextMiddleware (parent vs child via run_skill).
        # MCP SDK's own 'Processing request' log doesn't include any
        # session info, so this is how the user tells parent calls
        # apart from child-into-parent-server calls.
        from .caller_context import current_caller
        caller = current_caller.get()
        who = f"{caller.skill}@{caller.version}" if caller else "?"
        safe = {
            k: (v[:80] + "…" if isinstance(v, str) and len(v) > 80 else v)
            for k, v in kwargs.items()
        }
        return f"[{who}] call {name}({safe})"

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def awrapped(**kwargs):
            _mcp_log.info(_format(kwargs))
            return await fn(**kwargs)
        return awrapped

    @functools.wraps(fn)
    def wrapped(**kwargs):
        _mcp_log.info(_format(kwargs))
        return fn(**kwargs)
    return wrapped

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


# When the server binds to 127.0.0.1, FastMCP auto-enables DNS-rebinding
# protection that only accepts Host: 127.0.0.1 / localhost. The container
# connects via Host: host.docker.internal:<port>, so we have to explicitly
# allow that host. The Bearer-token middleware handles real auth; this
# allow-list just sidesteps a defense-in-depth check that doesn't fit our
# topology.
_ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "host.docker.internal:*"]

from .hitl_mcp import HitlIO
from .memory_store import MemoryStore  # noqa: F401  (for type hints)
from .caller_context import CallerInfo, CallerContextMiddleware  # noqa: F401


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class HitlServer:
    """HTTP MCP server (FastMCP) bound to 127.0.0.1:<random-port>.

    Supports multiple concurrent callers: each caller is identified by a
    unique Bearer token that maps to a CallerInfo (skill name + version).
    Memory tools are routed to the appropriate per-skill MemoryStore based
    on the caller resolved from the incoming request's Bearer token.

    Usage — single-skill (backward-compat):
        server = HitlServer(io_, skill_store=ms, global_store=gms,
                            primary_caller=CallerInfo("my-skill", "1.0.0"))

    Usage — multi-skill (Phase 2):
        server = HitlServer(io_, global_store=gms,
                            primary_caller=CallerInfo("parent", "1.0.0"))
        server.start()
        server.register_caller(child_token, CallerInfo("child", "2.0.0"))
    """

    def __init__(
        self,
        io_: HitlIO,
        skill_store: "MemoryStore | None" = None,
        global_store: "MemoryStore | None" = None,
        primary_caller: "CallerInfo | None" = None,
    ) -> None:
        self._io = io_
        self._skill_store = skill_store  # legacy — pre-populated for primary's store
        self._global_store = global_store
        self._primary_caller = primary_caller
        self.port: int = 0
        self.token: str = ""
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None
        self._token_map: dict[str, CallerInfo] = {}
        self._skill_stores_by_caller: dict[str, MemoryStore] = {}
        # Pre-populate primary's store so existing tests / single-skill
        # top-level runs keep working without rebuilding their store.
        if primary_caller is not None and skill_store is not None:
            self._skill_stores_by_caller[primary_caller.skill] = skill_store

    def register_caller(self, token: str, caller: CallerInfo) -> None:
        """Authorize `token` as belonging to a specific skill+version.

        Called by RunSkillHandler when spawning a child, and by start()
        itself when registering the primary caller. Idempotent: re-
        registering a token updates the CallerInfo (used by RunSkillHandler
        when the child's actual version becomes known from summary.json).
        """
        self._token_map[token] = caller

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)

        # Register the primary caller (if set) so that requests bearing
        # the auto-generated launcher token resolve to a known CallerInfo.
        if self._primary_caller is not None:
            self.register_caller(self.token, self._primary_caller)

        mcp = FastMCP(
            "zipsa",
            host="127.0.0.1",
            port=self.port,
            stateless_http=False,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_ALLOWED_HOSTS,
            ),
        )

        from .hitl_mcp import AskHandler, ConfirmHandler, ChooseHandler, HitlUnattended
        from .caller_context import current_caller
        from . import memory_store as _ms_module
        from zipsa import paths

        ask_h = AskHandler(self._io)
        confirm_h = ConfirmHandler(self._io)
        choose_h = ChooseHandler(self._io)

        @mcp.tool()
        @_logged
        def ask(prompt: str) -> str:
            """Ask the user a free-text question and return their reply."""
            try:
                return ask_h.run(prompt=prompt)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        @_logged
        def confirm(message: str, default: bool | None = None) -> bool:
            """Ask the user a yes/no question."""
            try:
                return confirm_h.run(message=message, default=default)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        @_logged
        def choose(prompt: str, options: list[str]) -> str:
            """Ask the user to choose one of the given options."""
            try:
                return choose_h.run(prompt=prompt, options=options)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        def _store_for_scope(scope: str) -> MemoryStore:
            """Look up the appropriate MemoryStore for the current request.

            For scope="global", returns the shared global_store (if configured).
            For scope="skill" (the default), looks up the per-skill store
            keyed by the caller's skill name, lazy-creating it if needed.
            """
            if scope == "global":
                if self._global_store is None:
                    raise RuntimeError("global_store_not_configured")
                return self._global_store
            # scope == "skill" (or any other value — let the caller validate)
            caller = current_caller.get()
            if caller is None:
                # Middleware should reject before reaching here, but be defensive.
                raise RuntimeError("caller_unknown")
            key = caller.skill
            if key not in self._skill_stores_by_caller:
                self._skill_stores_by_caller[key] = _ms_module.MemoryStore(
                    paths.resolve_skill_memory_path(key)
                )
            return self._skill_stores_by_caller[key]

        @mcp.tool()
        @_logged
        def recall(key: str, scope: str = "skill") -> str | None:
            """Read a value previously stored via remember.

            Returns null if the key is not set in the given scope.
            Scope: "skill" (default, per-skill private) or "global"
            (shared across all skills).
            """
            store = _store_for_scope(scope)
            value = store.get(key)
            if value is None or isinstance(value, str):
                return value
            import json as _json
            return _json.dumps(value, ensure_ascii=False)

        @mcp.tool()
        @_logged
        def remember(key: str, value: str, scope: str = "skill") -> None:
            """Store a value for future runs of this (or any) skill.

            Scope: "skill" (default, per-skill private) or "global"
            (shared across all skills).
            """
            store = _store_for_scope(scope)
            store.set(key, value)

        @mcp.tool()
        @_logged
        def forget(key: str, scope: str = "skill") -> bool:
            """Delete a stored value. Returns true if removed, false if missing."""
            store = _store_for_scope(scope)
            return store.delete(key)

        @mcp.tool()
        @_logged
        def list_memory(scope: str = "skill") -> list[str]:
            """List keys in the chosen scope."""
            store = _store_for_scope(scope)
            return list(store.keys())

        @mcp.tool()
        @_logged
        def ask_once(
            key: str,
            prompt: str,
            scope: str = "skill",
            default: str | None = None,
        ) -> str:
            """Ask the user a question and cache the answer permanently.

            If the key already has a value (in the chosen scope), returns
            that value without prompting. Otherwise asks the user, stores
            the answer, and returns it. The "cached config" pattern in one
            call — no risk of forgetting to remember.

            If `default` is given: an empty answer (the user just hits
            Enter) resolves to `default`, and in a non-interactive run the
            question resolves to `default` instead of failing. Pass the
            value you mention in the prompt as `default` rather than
            relying on the agent inferring that empty input means "use the
            default".

            Use this for values that, once given, should never be asked
            again (workspace name, default city, preferred language).

            For one-off questions whose answers should NOT be stored
            (current date, "are you sure?"), use the bare `ask` tool.
            """
            store = _store_for_scope(scope)
            cached = store.get(key)
            if cached is not None:
                return cached if isinstance(cached, str) else str(cached)
            try:
                answer = ask_h.run(prompt=prompt)
            except HitlUnattended as e:
                if default is None:
                    raise RuntimeError(f"HITL_UNATTENDED: {e}") from e
                answer = default
            else:
                if answer == "" and default is not None:
                    answer = default
            store.set(key, answer)
            return answer

        # Cross-skill data exchange — always registered, no store dependency.
        from .artifact_handler import ArtifactHandler
        artifact_h = ArtifactHandler()

        @mcp.tool()
        @_logged
        def get_artifact(
            skill: str, version: str, run_id: str, name: str
        ) -> dict:
            """Read an artifact written by a past (or in-progress) skill run.

            Skills write structured output to their run's artifacts/ dir
            (container path /home/agent/runs/current/artifacts/<name>).
            Use this to consume that output from another skill or another
            agent turn — log-mediated data sharing.

            Args:
              skill: skill name (e.g. "agenthud-report")
              version: skill version (e.g. "0.1.0")
              run_id: timestamp directory under runs/ (e.g.
                "2026-05-21_120000_000")
              name: flat filename — no slashes, no '..', no absolute paths

            Returns:
              {"name": str, "size": int, "content": object} — `content`
              is parsed JSON for *.json files, utf-8 text otherwise.

            Errors:
              ARTIFACT_NOT_FOUND, ARTIFACT_BAD_NAME, ARTIFACT_TOO_LARGE,
              ARTIFACT_BAD_JSON.
            """
            return artifact_h.run(
                skill=skill, version=version, run_id=run_id, name=name,
            )

        # Skill-builder support — three tools the authoring agent uses
        # during its discover → draft → validate cycle. Always registered
        # (no caller scoping); access is gated by the agent's allowedTools.
        from .skill_catalog_handler import SkillCatalogHandler
        from .skill_files_handler import SkillFilesHandler
        from .skill_validator_handler import SkillValidatorHandler
        catalog_h = SkillCatalogHandler()
        files_h = SkillFilesHandler()
        validator_h = SkillValidatorHandler()

        @mcp.tool()
        @_logged
        def list_skills_catalog() -> dict:
            """List installed skills with run statistics.

            Returns {skills: [{name, version, purpose, model, description,
            tags, total_runs, successful_runs}, ...]}. Use this during
            the discover phase to check whether the user's desired skill
            already exists, or to find atomic skills that could be
            composed as children of an orchestrator.
            """
            return catalog_h.run()

        @mcp.tool()
        @_logged
        def write_skill_files(name: str, files: dict) -> dict:
            """Write a draft skill into ~/.zipsa/staging/<name>/.

            Only three filenames are accepted: `SKILL.md` (author's
            natural-language source), `zipsa-dist/manifest.yaml`
            (launcher config), and `zipsa-dist/instruction.md`
            (agent-facing instructions). Re-writing overwrites; this
            tool is meant to be called repeatedly as the draft evolves.

            Returns {path, written_files}. Errors: SKILL_NAME_BAD,
            SKILL_FILE_BAD_NAME, SKILL_FILE_BAD_CONTENT, SKILL_FILES_EMPTY.
            """
            return files_h.write(name=name, files=files)

        @mcp.tool()
        @_logged
        def validate_skill(path: str) -> dict:
            """Validate the skill directory at `path` (must be under
            ZIPSA_HOME — typically a staging dir written by
            write_skill_files).

            Returns {ok: bool, errors: [str], name?, version?}. Use this
            after every draft revision to either confirm "ready to
            install" or feed errors back into the next iteration.
            """
            return validator_h.validate(path=path)

        from .run_log_handler import RunLogHandler
        run_log_h = RunLogHandler()

        @mcp.tool()
        @_logged
        def read_run_log(
            skill: str, version: str, run_id: str,
            phase_id: str = "",
        ) -> dict:
            """Read a past run's output.jsonl as a compact per-turn
            summary so an analysis agent (skill-builder) can decide
            what to refine without dumping the raw multi-MB stream
            into its context.

            Each turn is condensed to ~280 chars in a stable vocabulary
            (`S:` system, `A: 💭/🔧/💬` assistant, `U: ✓` user,
            `R:` final result). Multi-phase runs concatenate all phases
            in order with `--- phase: <id> ---` markers; pass `phase_id`
            to restrict to one phase. Total output capped at 100KB
            (kept from the TAIL — most recent is most useful for
            "what went wrong"). truncated=True signals the trim happened.

            Args:
              skill, version, run_id: identify the run dir under ~/.zipsa/
              phase_id: optional — restrict to a single phase id

            Returns: {log, total_turns, total_cost_usd, phase_id, truncated}
            Errors: RUN_LOG_BAD_NAME, RUN_LOG_NOT_FOUND.
            """
            return run_log_h.read(
                skill=skill, version=version, run_id=run_id,
                phase_id=phase_id or None,
            )

        from .run_skill_handler import RunSkillHandler
        run_skill_h = RunSkillHandler(server=self)

        @mcp.tool()
        @_logged
        async def run_skill(name: str, args: str = "") -> dict:
            """Invoke a child skill declared in this skill's spec.children.

            Returns {status, exit_code, skill, version, run_id, summary,
            is_staging (always false for this tool)}. Pair `skill`+
            `version`+`run_id` with `mcp__zipsa__get_artifact` to read
            the child's outputs.

            Args:
              name: child skill name (must be in this skill's spec.children)
              args: passed to child as user_query (string). For structured
                    data, JSON-encode it yourself; the child SKILL.md
                    decides whether to parse user_query as JSON.
            """
            # Run blocking handler in a thread so the event loop stays
            # free to process other MCP requests (especially the child
            # container's MCP calls back into this same server).
            import asyncio
            return await asyncio.to_thread(run_skill_h.run, name=name, args=args)

        from .run_staging_skill_handler import RunStagingSkillHandler
        run_staging_h = RunStagingSkillHandler(server=self)

        @mcp.tool()
        @_logged
        async def run_staging_skill(name: str, args: str = "") -> dict:
            """Invoke a skill that lives in ~/.zipsa/staging/<name>/
            (not yet installed). Designed for skill-builder's iterate
            loop — author drafts a skill, runs it via this tool,
            analyzes the result via mcp__zipsa__read_run_log, refines,
            repeats. Permission gated by caller's spec.allows_staging_run.

            Same result shape as run_skill, plus `is_staging: true` so
            callers can distinguish staging runs from regular ones (e.g.
            for tagging or different cost accounting). run_skill returns
            `is_staging: false` for symmetry.

            Args:
              name: directory under ~/.zipsa/staging/
              args: passed to child as user_query (same as run_skill)
            """
            import asyncio
            return await asyncio.to_thread(
                run_staging_h.run, name=name, args=args,
            )

        app = mcp.streamable_http_app()
        app.add_middleware(CallerContextMiddleware, token_map=self._token_map)
        config = uvicorn.Config(
            app,
            # Bind to all interfaces so secondary containers (Phase 2
            # children) can reach us via host.docker.internal. With
            # 127.0.0.1 binding, Docker Desktop's gateway only forwarded
            # the FIRST container's traffic; subsequent containers got
            # connection timeout (observed on macOS Docker Desktop 4.x).
            # Auth still enforced by CallerContextMiddleware (every
            # request needs a registered Bearer token).
            host="0.0.0.0",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._uvicorn_server.run,
            daemon=True,
            name=f"hitl-mcp-{self.port}",
        )
        self._thread.start()

        # Wait until the server actually accepts connections
        deadline = 5.0
        step = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(("127.0.0.1", self.port))
                s.close()
                return
            except OSError:
                threading.Event().wait(step)
                elapsed += step
        raise RuntimeError(f"HitlServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = None
        self._thread = None
