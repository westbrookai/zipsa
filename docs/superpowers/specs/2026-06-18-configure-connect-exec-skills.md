# configure (requires) + connect (mcp) for exec skills (#161)

Part of epic **#155 — First-class exec skills**. Depends on the metadata
keystone (#156). `configure` and `connect` are manifest-only today; make
them work for exec skills.

## Problem
- `zipsa configure <name>` reads host-side `spec.requires` via
  `Skill.load` and saves to `requires.yaml`. Exec skills declare their
  requires in `zipsa/package.yaml` (`ExecSkill.requires`), which `configure`
  never reads.
- `zipsa connect <server>` pre-authorizes OAuth by scanning installed
  skills' manifest `spec.mcp`. Exec metadata has **no `mcp` field** — the
  keystone spec deferred it explicitly ("add when #161 needs them"). This
  is the issue that needs it.

## configure — reuse the existing requires path
`ExecSkill.requires` is `dict[str, Requirement]`; the exec `Requirement`
already carries `.type` (`directory` | `list[directory]`) and `.prompt`,
which is exactly what `prompt_for_value` + `save_requires` consume. So
configure for exec is a dispatch, not new logic:
- `_is_exec_format` → `load_exec_skill`; iterate `exec_skill.requires`,
  prompt with `prompt_for_value`, save with `save_requires` to
  `skill_requires_file(name, exec_skill.version)` — the same save path the
  run loader already reads. No requires.yaml format change.

## connect — add a minimal `mcp` field to exec metadata
Add an `mcp` list to `zipsa/package.yaml` / `ExecSkill`, carrying just what
OAuth pre-auth needs (mirrors the legacy fields so `connect` treats both
uniformly):

```yaml
mcp:
  - name: notion
    type: http              # default; only http supports oauth2 here
    url: https://mcp.notion.com/mcp
    auth:
      type: oauth2          # oauth2 | none
```

- New `ExecMcpServer` (+ `ExecMcpAuth`) models in `exec_skill.py`;
  `ExecSkill.mcp: list[ExecMcpServer]`. Loader maps `package["mcp"]`.
- `connect` scans exec skills too: `_is_exec_format` → `load_exec_skill`,
  match `s.name == server && s.type == "http" && s.auth.type == "oauth2"`,
  then `OAuthManager.ensure_credentials(s.name, s.url)` — same call as
  legacy (both expose `.name` + `.url`).

YAGNI: only the OAuth-relevant subset (no headersHelper / env /
allowed_tools / stdio) until a real need appears.

## Tests
- Loader: `package.yaml` with `mcp` populates `ExecSkill.mcp`; absent → `[]`.
- configure: exec skill with `requires` prompts + saves to the requires
  file; exec skill with no requires prints "no required configuration".
- connect: finds an exec skill's oauth2 http server and calls
  `ensure_credentials`; a non-oauth / wrong-name server is not matched.
