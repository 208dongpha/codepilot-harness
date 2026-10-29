import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("CODEPILOT_") or name == "GEMINI_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
