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
