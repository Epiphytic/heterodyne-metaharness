from pathlib import Path

import pytest
from wsd_env import git_repo

from heterodyne.wsd import gitwip


def test_wip_commit_is_idempotent(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("x")
    first = gitwip.wip_commit(repo, "op1", "parked btq-1")
    (repo / "b.txt").write_text("later")
    assert gitwip.wip_commit(repo, "op1", "parked btq-1") == first
    assert gitwip.find_wip(repo, "op1") == first
    assert gitwip.find_wip(repo, "op2") is None
    assert "b.txt" in gitwip.git(repo, "status", "--porcelain")


def test_wip_commit_with_nothing_to_commit_still_records_the_mark(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    sha = gitwip.wip_commit(repo, "op1", "parked")
    assert gitwip.find_wip(repo, "op1") == sha


def test_wip_commit_skips_hooks(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    gitwip.wip_commit(repo, "op1", "parked")


def test_git_failure_is_raised(tmp_path: Path) -> None:
    with pytest.raises(gitwip.GitFailed):
        gitwip.wip_commit(tmp_path, "op1", "not a repo")
