import os

import pytest

from codepilot.patch.guard import GuardedPath


@pytest.mark.skipif(os.name != "nt", reason="Windows directory ownership semantics.")
def test_windows_guard_does_not_claim_an_existing_directory(tmp_path, monkeypatch):
    real_mkdir = os.mkdir

    def racing_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        raise FileExistsError

    monkeypatch.setattr("codepilot.patch.guard.os.mkdir", racing_mkdir)
    with GuardedPath(tmp_path, ("pkg", "file.txt")) as guard:
        assert guard.created_dirs == []

    assert (tmp_path / "pkg").is_dir()
