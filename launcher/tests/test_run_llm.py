from pathlib import Path
from zipsa.run_llm import build_run_prompt, build_run_argv


def _skill(tmp_path):
    root = tmp_path / "weather"
    (root / "zipsa-dist").mkdir(parents=True)
    (root / "SKILL.md").write_text("# weather\nFetch then report. Call exec.\n")
    return root


class TestBuildRunPrompt:
    def test_includes_skill_md_and_run_protocol(self, tmp_path):
        root = _skill(tmp_path)
        p = build_run_prompt(root, user_input="Sydney")
        assert "Fetch then report" in p          # SKILL.md inlined
        assert "mcp__zipsa__exec" in p            # how to call scripts
        assert "Sydney" in p                      # the user input

    def test_prepends_intent_when_present(self, tmp_path):
        root = _skill(tmp_path)
        (root / "INTENT.md").write_text("Tell me if I need an umbrella.\n")
        p = build_run_prompt(root, user_input="")
        assert "umbrella" in p
        assert "Intent" in p


class TestBuildRunArgv:
    def test_mounts_skill_ro_and_wires_mcp(self, tmp_path):
        root = _skill(tmp_path)
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{root}:{root}:ro" in argv         # skill mounted read-only
        assert "--mcp-config" in argv
        assert "claude" in argv and "-p" in argv
        assert "bypassPermissions" in argv

    def test_env_file_added_when_given(self, tmp_path):
        root = _skill(tmp_path)
        ef = tmp_path / ".env"; ef.write_text("CLAUDE_CODE_OAUTH_TOKEN=t\n")
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=ef,
        )
        i = argv.index("--env-file")
        assert argv[i + 1] == str(ef)
