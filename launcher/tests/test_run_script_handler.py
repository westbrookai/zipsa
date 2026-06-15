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
