import os
import shutil
import subprocess
from pathlib import Path

import pytest
from wsd_env import git_repo

from heterodyne.sandbox import sessiongit
from heterodyne.wsd import gitwip

BRANCH = "btq/btq-1"
IDENT = ("-c", "user.name=agent", "-c", "user.email=agent@example.org")


def linked(tmp_path: Path) -> tuple[Path, Path]:
    repo = git_repo(tmp_path / "repo")
    worktree = tmp_path / "wt"
    gitwip.git(repo, "worktree", "add", "-q", "-b", BRANCH, str(worktree))
    return repo, worktree


def inside(private: Path, worktree: Path, *args: str) -> str:
    """git as the agent runs it: the worktree's `.git` file finds the private directory at its bind."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_DIR": str(private), "GIT_WORK_TREE": str(worktree)}
    return subprocess.run(["git", *args], cwd=worktree, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def tip(repo: Path, ref: str) -> str:
    return gitwip.git(repo, "rev-parse", ref)


def test_pin_takes_the_git_dir_from_the_repository_not_the_dot_git_file(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    marker = tmp_path / "ran"
    evil = git_repo(tmp_path / "evil")
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o700)
    gitwip.git(evil, "config", "core.fsmonitor", str(hook))
    (worktree / ".git").write_text(f"gitdir: {evil / '.git'}\n")        # the agent redirects it
    p = gitwip.pin(repo, worktree, BRANCH)
    assert p.git_dir == (repo / ".git" / "worktrees" / "wt").resolve()
    assert p.common == (repo / ".git").resolve()
    sha = gitwip.wip_commit(p, "m1", "test")
    assert tip(repo, BRANCH) == sha and not marker.exists()


def test_pin_refuses_a_head_moved_off_the_bead_branch(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    (repo / ".git" / "worktrees" / "wt" / "HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(gitwip.GitFailed, match="bead branch"):
        gitwip.pin(repo, worktree, BRANCH)


@pytest.mark.parametrize("name", ["HEAD", "commondir", "gitdir"])
def test_pin_refuses_linked_metadata(tmp_path: Path, name: str) -> None:
    repo, worktree = linked(tmp_path)
    meta = repo / ".git" / "worktrees" / "wt" / name
    copy = tmp_path / f"copy-{name}"
    copy.write_text(meta.read_text())
    meta.unlink()
    meta.symlink_to(copy)
    with pytest.raises(gitwip.GitFailed):
        gitwip.pin(repo, worktree, BRANCH)


def test_pin_refuses_a_linked_worktree_directory(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    real = repo / ".git" / "worktrees" / "wt"
    real.rename(tmp_path / "moved")
    real.symlink_to(tmp_path / "moved")
    with pytest.raises(gitwip.GitFailed):
        gitwip.pin(repo, worktree, BRANCH)


def test_pin_refuses_per_worktree_config(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    gitwip.git(repo, "config", "extensions.worktreeConfig", "true")
    with pytest.raises(gitwip.GitFailed, match="per-worktree"):
        gitwip.pin(repo, worktree, BRANCH)


def test_the_main_worktree_is_never_seeded(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    gitwip.git(repo, "checkout", "-q", "-b", BRANCH)
    p = gitwip.pin(repo, repo, BRANCH)                  # plan 3's callers may still commit in it
    with pytest.raises(gitwip.GitFailed, match="main worktree"):
        sessiongit.seed(p, tmp_path / "private", read_only=False)


def test_a_session_commit_lands_on_the_bead_branch_alone(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    rw, objects, pointer = sessiongit.seed(p, private, read_only=False)
    assert (rw.source, rw.target, rw.read_only) == (private, p.git_dir, False)
    assert (pointer.source, pointer.target, pointer.read_only) == (p.work_tree / ".git",) * 2 + (True,)
    assert (objects.source, objects.target, objects.read_only) == (p.common / "objects",) * 2 + (True,)
    main = tip(repo, "main")
    assert inside(private, worktree, "rev-parse", "main") == main            # the snapshot is readable
    (worktree / "a.txt").write_text("work")
    inside(private, worktree, "add", "a.txt")
    inside(private, worktree, *IDENT, "commit", "-q", "-m", "agent work")
    made = inside(private, worktree, "rev-parse", "HEAD")
    inside(private, worktree, "update-ref", "refs/heads/main", made)       # moves only its own copy
    (worktree / ".git").write_text("gitdir: /nowhere\n")
    assert sessiongit.land(p, private) == made
    assert tip(repo, BRANCH) == made and tip(repo, "main") == main
    assert (worktree / ".git").read_text() == f"gitdir: {p.git_dir}\n"
    assert sessiongit.land(p, private) is None                             # idempotent
    assert gitwip.git(repo, "fsck", "--connectivity-only", "--no-dangling") == ""


def test_a_packed_bead_branch_still_lands(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    inside(private, worktree, "pack-refs", "--all")
    assert sessiongit.land(p, private) == tip(repo, BRANCH)


def test_landing_refuses_a_link_in_the_session_objects(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    (private / "objects" / "zz").symlink_to(tmp_path)
    with pytest.raises(gitwip.GitFailed, match="link"):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == before


def test_btq_git_never_reads_the_session_git_dir(tmp_path: Path) -> None:
    """btq's git is not pinned: it follows the worktree's `.git`. The pointer is trusted when it is bound
    read-only, so no config, hook or fsmonitor the agent writes in its private directory runs on the host."""
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    (worktree / ".git").write_text(f"gitdir: {private}\n")              # planted in an earlier generation
    sessiongit.seed(p, private, read_only=False)
    assert (worktree / ".git").read_text() == f"gitdir: {p.git_dir}\n"
    marker = tmp_path / "ran"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o700)
    with (private / "config").open("a") as fh:                         # what the agent can write
        fh.write(f"[core]\n\tfsmonitor = {hook}\n\thooksPath = {tmp_path}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], env=env, check=True,
                   capture_output=True)                                 # as btq runs it
    assert not marker.exists()


@pytest.mark.parametrize("swap", ["objects", "objects/info"])
def test_landing_refuses_a_linked_object_store_and_writes_nothing_outside(tmp_path: Path, swap: str) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    inside(private, worktree, *IDENT, "commit", "-q", "--allow-empty", "-m", "agent work")
    outside = tmp_path / "outside"
    shutil.move(private / swap, outside)                  # the agent moves the store out, links it back
    next(outside.rglob("alternates")).unlink()
    (private / swap).symlink_to(outside)
    files = {q: q.read_bytes() for q in outside.rglob("*") if q.is_file()}
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)
    assert {q: q.read_bytes() for q in outside.rglob("*") if q.is_file()} == files
    assert not any(outside.rglob("alternates")) and tip(repo, BRANCH) == before


def test_landing_ignores_an_alternates_file_the_agent_rewrote(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    other = git_repo(tmp_path / "other")
    gitwip.git(other, *IDENT, "commit", "-q", "--allow-empty", "-m", "foreign")   # one this repo never had
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    before = tip(repo, BRANCH)
    (private / "objects" / "info" / "alternates").write_text(f"{other / '.git' / 'objects'}\n")
    (private / "refs" / "heads" / "btq" / "btq-1").write_text(tip(other, "main") + "\n")
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)
    assert tip(repo, BRANCH) == before


def test_landing_into_a_vanished_worktree_is_a_git_failure(tmp_path: Path) -> None:
    repo, worktree = linked(tmp_path)
    p = gitwip.pin(repo, worktree, BRANCH)
    private = tmp_path / "private"
    sessiongit.seed(p, private, read_only=False)
    shutil.rmtree(worktree)
    with pytest.raises(gitwip.GitFailed):
        sessiongit.land(p, private)
