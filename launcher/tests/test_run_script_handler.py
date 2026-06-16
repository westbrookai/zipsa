from pathlib import Path
from zipsa.core.run_script_handler import RunScriptHandler


def _skill(tmp_path: Path) -> Path:
    dist = tmp_path / "s" / "zipsa-dist"
    dist.mkdir(parents=True)
    (dist / "1.fetch.py").write_text(
        "import json,sys\n"
        "p=json.loads(sys.stdin.read())\n"
        "print(json.dumps({'q': p['ctx']['user_query'], 'prev': p['prev']}))\n"
    )
    (tmp_path / "s" / "SKILL.md").write_text("# s\n")
    return tmp_path / "s"


class TestRunScriptHandler:
    def test_runs_named_script_local(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)  # local mode
        out = h.run(script="1.fetch", args="hello", prev={"x": 1})
        assert out["status"] == "ok"
        assert out["result"] == {"q": "hello", "prev": {"x": 1}}
        assert out["exit_code"] == 0

    def test_unknown_script_is_error_not_crash(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)
        out = h.run(script="9.nope")
        assert out["status"] == "failed"
        assert out["error"]["code"] == "script_not_found"

    def test_rejects_path_escape(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)
        out = h.run(script="../../etc/passwd")
        assert out["status"] == "failed"
        assert out["error"]["code"] == "script_not_found"

    def test_forwards_mounts_to_run_phase(self, tmp_path, monkeypatch):
        import zipsa.core.run_script_handler as mod
        root = tmp_path / "s2"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "zipsa-dist" / "1.do.py").write_text(
            "import json,sys; print(json.dumps({'ok': True}))\n")
        (root / "SKILL.md").write_text("# s\n")
        captured = {}

        def fake_run_phase(path, **kw):
            captured.update(kw)
            from zipsa.exec_runner import ExecResult
            return ExecResult(skill_name="s", mode="local", result={"ok": True},
                              exit_code=0, duration_ms=1, out_dir="/tmp",
                              stdout="", stderr="")
        monkeypatch.setattr(mod, "run_phase", fake_run_phase)
        h = RunScriptHandler(docker_image="img", skill_root=root)
        h.run(script="1.do", mounts=[("/host/creds.json", "/mnt/creds.json")])
        # Host path threads through as a Path (exec_runner does
        # f"{host_path}:..." and build_run_argv expects Path host paths).
        assert captured["extra_mounts"] == [(Path("/host/creds.json"), "/mnt/creds.json")]

    def test_tilde_mount_is_expanded(self, tmp_path, monkeypatch):
        """A ~-prefixed host path must be expanded to an absolute path, not forwarded literally."""
        import zipsa.core.run_script_handler as mod
        root = tmp_path / "s3"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "zipsa-dist" / "1.do.py").write_text(
            "import json,sys; print(json.dumps({'ok': True}))\n")
        (root / "SKILL.md").write_text("# s\n")
        captured = {}

        def fake_run_phase(path, **kw):
            captured.update(kw)
            from zipsa.exec_runner import ExecResult
            return ExecResult(skill_name="s", mode="local", result={"ok": True},
                              exit_code=0, duration_ms=1, out_dir="/tmp",
                              stdout="", stderr="")
        monkeypatch.setattr(mod, "run_phase", fake_run_phase)
        h = RunScriptHandler(docker_image="img", skill_root=root)
        h.run(script="1.do", mounts=[("~/.zipsa/credentials/tfnsw.json", "/mnt/creds.json")])
        host_path, _ = captured["extra_mounts"][0]
        # Must NOT contain literal ~ — must be expanded to the real home dir.
        assert "~" not in str(host_path)
        assert host_path.is_absolute()
        assert host_path == Path("~/.zipsa/credentials/tfnsw.json").expanduser().resolve()

    def test_default_mounts_applied_to_run_phase(self, tmp_path, monkeypatch):
        """default_mounts from constructor are included in run_phase extra_mounts."""
        import zipsa.core.run_script_handler as mod
        root = tmp_path / "s4"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "zipsa-dist" / "1.do.py").write_text(
            "import json,sys; print(json.dumps({'ok': True}))\n")
        (root / "SKILL.md").write_text("# s\n")
        captured = {}

        def fake_run_phase(path, **kw):
            captured.update(kw)
            from zipsa.exec_runner import ExecResult
            return ExecResult(skill_name="s", mode="local", result={"ok": True},
                              exit_code=0, duration_ms=1, out_dir="/tmp",
                              stdout="", stderr="")
        monkeypatch.setattr(mod, "run_phase", fake_run_phase)

        default_host = Path("/resolved/creds.json")
        h = RunScriptHandler(
            docker_image="img", skill_root=root,
            default_mounts=[(default_host, "/mnt/creds.json")],
        )
        h.run(script="1.do")
        assert len(captured["extra_mounts"]) == 1
        assert captured["extra_mounts"][0] == (default_host, "/mnt/creds.json")

    def test_default_mounts_merged_with_per_call_mounts(self, tmp_path, monkeypatch):
        """default_mounts come first; per-call mounts are appended after."""
        import zipsa.core.run_script_handler as mod
        root = tmp_path / "s5"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "zipsa-dist" / "1.do.py").write_text(
            "import json,sys; print(json.dumps({'ok': True}))\n")
        (root / "SKILL.md").write_text("# s\n")
        captured = {}

        def fake_run_phase(path, **kw):
            captured.update(kw)
            from zipsa.exec_runner import ExecResult
            return ExecResult(skill_name="s", mode="local", result={"ok": True},
                              exit_code=0, duration_ms=1, out_dir="/tmp",
                              stdout="", stderr="")
        monkeypatch.setattr(mod, "run_phase", fake_run_phase)

        default_host = Path("/resolved/default.json")
        h = RunScriptHandler(
            docker_image="img", skill_root=root,
            default_mounts=[(default_host, "/mnt/default.json")],
        )
        h.run(script="1.do", mounts=[("/host/extra.json", "/mnt/extra.json")])
        mounts = captured["extra_mounts"]
        assert len(mounts) == 2
        # default mount comes first
        assert mounts[0] == (default_host, "/mnt/default.json")
        # per-call mount is second, host path resolved
        extra_host, extra_container = mounts[1]
        assert extra_container == "/mnt/extra.json"
        assert extra_host.is_absolute()
        assert "~" not in str(extra_host)

    def test_default_mounts_str_host_is_expanded(self, tmp_path, monkeypatch):
        """default_mounts with str host entry (not pre-resolved Path) are expanded."""
        import zipsa.core.run_script_handler as mod
        root = tmp_path / "s6"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "zipsa-dist" / "1.do.py").write_text(
            "import json,sys; print(json.dumps({'ok': True}))\n")
        (root / "SKILL.md").write_text("# s\n")
        captured = {}

        def fake_run_phase(path, **kw):
            captured.update(kw)
            from zipsa.exec_runner import ExecResult
            return ExecResult(skill_name="s", mode="local", result={"ok": True},
                              exit_code=0, duration_ms=1, out_dir="/tmp",
                              stdout="", stderr="")
        monkeypatch.setattr(mod, "run_phase", fake_run_phase)

        h = RunScriptHandler(
            docker_image="img", skill_root=root,
            # Pass a str (not Path) as the host side — must still be expanded
            default_mounts=[("~/.zipsa/credentials/svc.json", "/mnt/svc.json")],  # type: ignore[list-item]
        )
        h.run(script="1.do")
        host_path, container = captured["extra_mounts"][0]
        assert "~" not in str(host_path)
        assert host_path.is_absolute()
        assert container == "/mnt/svc.json"
