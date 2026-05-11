"""Shared pytest fixtures."""

import os
import pytest


@pytest.fixture(autouse=True)
def isolated_zipsa_home(tmp_path, monkeypatch):
    """Redirect ZIPSA_HOME to a temp directory for every test.

    Prevents tests from writing to the real ~/.zipsa/ on the developer's machine.
    """
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / ".zipsa"))
