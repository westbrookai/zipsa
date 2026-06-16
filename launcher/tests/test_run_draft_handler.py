from pathlib import Path
from unittest.mock import patch
from zipsa.core.run_draft_handler import RunDraftHandler


class TestRunDraftHandler:
    @patch("zipsa.core.run_draft_handler.run_skill_llm")
    def test_runs_skill_and_shapes_result(self, mock_run, tmp_path):
        mock_run.return_value = 0
        h = RunDraftHandler(image="img", skill_root=tmp_path)
        out = h.run(args="hi", mounts=[("/h/c.json", "/mnt/c.json")])
        assert out["status"] == "ok"
        assert out["exit_code"] == 0
        # forwarded to run_skill_llm
        _, kwargs = mock_run.call_args
        assert kwargs["image"] == "img"
        assert kwargs["extra_mounts"] == [(Path("/h/c.json"), "/mnt/c.json")]

    @patch("zipsa.core.run_draft_handler.run_skill_llm")
    def test_nonzero_exit_is_failed(self, mock_run, tmp_path):
        mock_run.return_value = 1
        h = RunDraftHandler(image="img", skill_root=tmp_path)
        assert h.run()["status"] == "failed"

    @patch("zipsa.core.run_draft_handler.run_skill_llm")
    def test_tilde_mount_is_expanded(self, mock_run, tmp_path):
        """A ~-prefixed host path must be expanded to absolute before forwarding to run_skill_llm."""
        mock_run.return_value = 0
        h = RunDraftHandler(image="img", skill_root=tmp_path)
        h.run(mounts=[("~/.zipsa/credentials/tfnsw.json", "/mnt/creds.json")])
        _, kwargs = mock_run.call_args
        host_path, _ = kwargs["extra_mounts"][0]
        # Must NOT contain literal ~ — must be resolved to the real home dir.
        assert "~" not in str(host_path)
        assert host_path.is_absolute()
        assert host_path == Path("~/.zipsa/credentials/tfnsw.json").expanduser().resolve()
