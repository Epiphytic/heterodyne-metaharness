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


@pytest.mark.parametrize("configured", [False, True])
def test_no_hook_runs_for_a_wip_commit(tmp_path: Path, configured: bool) -> None:
    """`--no-verify` alone leaves prepare-commit-msg and post-commit running; one could strip the park
    mark, so a replay would commit again. No hook runs, a configured core.hooksPath included."""
    repo = git_repo(tmp_path / "r")
    hooks = repo / "custom-hooks" if configured else repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    if configured:
        gitwip.git(repo, "config", "core.hooksPath", str(hooks))
    ran = tmp_path / "ran"
    for name in ("prepare-commit-msg", "commit-msg", "post-commit", "pre-commit"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\necho {name} >> "{ran}"\n'
                        + ('echo stripped > "$1"\n' if name.endswith("msg") else ""))
        hook.chmod(0o755)
    sha = gitwip.wip_commit(repo, "op1", "parked")
    assert not ran.exists()
    assert "wsd-park: op1" in gitwip.git(repo, "log", "-1", "--format=%B", sha)
    assert gitwip.wip_commit(repo, "op1", "parked") == sha


def test_git_failure_is_raised(tmp_path: Path) -> None:
    with pytest.raises(gitwip.GitFailed):
        gitwip.wip_commit(tmp_path, "op1", "not a repo")


def test_a_wip_commit_without_its_mark_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth: should a hook still run and strip the mark, the commit is reported as failed
    rather than returned as the park's commit."""
    repo = git_repo(tmp_path / "r")
    hook = repo / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text('#!/bin/sh\necho stripped > "$1"\n')
    hook.chmod(0o755)
    monkeypatch.setattr(gitwip, "NO_HOOKS", ())
    with pytest.raises(gitwip.GitFailed):
        gitwip.wip_commit(repo, "op1", "parked")
