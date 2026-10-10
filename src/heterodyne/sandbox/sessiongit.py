"""The session's own git directory (ADR 0001 §7: agents commit to their local btq/<id> branch; D26).

The sandbox never sees the repository's real git directory. Each generation gets a private one, bound at
the linked worktree's git-directory path so the worktree's own `.git` file finds it, with the common
object store read-only behind `objects/info/alternates`. Once the sandbox is gone, `land` imports the
bead branch alone, through pinned host git only."""

import contextlib
import os
import shutil
import stat
from pathlib import Path

from heterodyne.agents.base import write_at
from heterodyne.sandbox.spec import Bind
from heterodyne.wsd.gitwip import FULL_SHA, GitFailed, Pinned, no_link, pinned_git, read_meta

SNAPSHOT = ("refs/heads", "refs/remotes", "refs/tags")
PACKED_LIMIT = 64 << 20         # packed-refs larger than this fails the landing; it is never truncated


def _write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        write_at(fd, name, text)
    finally:
        os.close(fd)


def _pointer(p: Pinned) -> None:
    """Rewrite the worktree's `.git` file to the trusted pointer."""
    try:
        fd = os.open(p.work_tree, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise GitFailed("the worktree can't be opened") from None
    try:
        write_at(fd, ".git", f"gitdir: {p.git_dir}\n")
    except IsADirectoryError:
        raise GitFailed("the worktree's .git was replaced by a directory") from None
    except OSError:
        raise GitFailed("the worktree's .git can't be written") from None
    finally:
        os.close(fd)


def seed(p: Pinned, private: Path, read_only: bool) -> tuple[Bind, Bind, Bind]:
    """Build a fresh private git directory for one generation. Returns its bind, the common store's, and a
    read-only bind of the worktree's `.git` pointer over itself: host git that reads the pointer (btq's)
    then always finds the real git directory, which the sandbox never sees (D26)."""
    if p.git_dir == p.common:
        raise GitFailed("a sandbox can't hold the main worktree: its git directory is inside it")
    if os.path.lexists(private):
        shutil.rmtree(private)                  # avoids symlink attacks; what it held was landed or refused
    tip = pinned_git(p, "rev-parse", "--verify", f"refs/heads/{p.branch}^{{commit}}").decode().strip()
    if not FULL_SHA.fullmatch(tip):
        raise GitFailed("the bead branch has no commit")
    listed = pinned_git(p, "for-each-ref", "--format=%(objectname) %(refname)", *SNAPSHOT).decode()
    others = [line for line in listed.splitlines() if not line.endswith(f" refs/heads/{p.branch}")]
    _write(private, "HEAD", f"ref: refs/heads/{p.branch}\n")
    _write(private, "config", "[core]\n\trepositoryformatversion = 0\n\tbare = false\n")
    _write(private, "packed-refs", "".join(f"{line}\n" for line in others))
    owner, _, leaf = p.branch.rpartition("/")
    _write(private / "refs" / "heads" / owner, leaf, f"{tip}\n")
    _write(private / "objects" / "info", "alternates", f"{p.common / 'objects'}\n")
    for name in ("index", "shallow"):
        source = p.git_dir / name if name == "index" else p.common / name
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, private / name)
    _pointer(p)                                 # trusted before it is bound read-only
    dot_git = p.work_tree / ".git"
    return (Bind(private, p.git_dir, read_only), Bind(p.common / "objects", p.common / "objects", True),
            Bind(dot_git, dot_git, True))


def _subdir(dirfd: int, name: str) -> int:
    """`name` in the open directory `dirfd`, opened as a directory and never through a link."""
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)


def _plain_tree(objfd: int) -> None:
    """Everything under the open object store is a plain directory or a regular file. The walk is anchored
    at the descriptor, so a link anywhere, the store itself included, is refused rather than followed, and
    an entry that can't be read fails the landing."""
    def fail(_: OSError) -> None:
        raise GitFailed("the session's git objects can't be read")

    for _top, dirs, files, topfd in os.fwalk(".", dir_fd=objfd, onerror=fail):
        for name in (*dirs, *files):
            mode = os.stat(name, dir_fd=topfd, follow_symlinks=False).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise GitFailed("the session's git objects hold a link or a special file")


def _trusted_alternates(p: Pinned, private: Path) -> None:
    """Check the private object store and point its alternates at the common store, every step relative
    to a descriptor opened without following a link (the sandbox is gone, so nothing changes under it)."""
    try:
        gitfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise GitFailed("the session's git directory can't be opened") from None
    try:
        objfd = _subdir(gitfd, "objects")
        try:
            _plain_tree(objfd)
            with contextlib.suppress(FileExistsError):
                os.mkdir("info", 0o700, dir_fd=objfd)
            infofd = _subdir(objfd, "info")
            try:
                write_at(infofd, "alternates", f"{p.common / 'objects'}\n")
            finally:
                os.close(infofd)
        finally:
            os.close(objfd)
    except OSError:
        raise GitFailed("the session's git objects can't be read") from None
    finally:
        os.close(gitfd)


def _tip(private: Path, branch: str) -> str:
    owner, _, leaf = branch.rpartition("/")
    loose = private / "refs" / "heads" / owner / leaf
    try:
        if os.path.lexists(loose):
            no_link(private, "refs", "heads", *owner.split("/"))
            text = read_meta(loose).strip()
        else:
            packed = read_meta(private / "packed-refs", PACKED_LIMIT).splitlines()
            text = next((line.split()[0] for line in packed if line.endswith(f" refs/heads/{branch}")), "")
    except OSError:
        raise GitFailed("the session's bead branch can't be read") from None
    if not FULL_SHA.fullmatch(text):
        raise GitFailed("the session's bead branch is not a commit")
    return text


def land(p: Pinned, private: Path) -> str | None:
    """Import the session's bead-branch tip, a fast-forward of the landed one. The new tip, or None when
    it didn't move. Idempotent."""
    _pointer(p)                                 # defence in depth: the bind kept it read-only
    _trusted_alternates(p, private)
    objects = private / "objects"                   # checked above: a plain directory, no link below it
    tip = _tip(private, p.branch)
    old = pinned_git(p, "rev-parse", f"refs/heads/{p.branch}").decode().strip()
    if tip == old:
        return None
    if pinned_git(p, "cat-file", "-t", tip, alternates=objects).decode().strip() != "commit":
        raise GitFailed("the session's bead branch is not a commit")
    # A fast-forward only: the landed tip (which descends from the bead's base) is never rewound or
    # replaced by an unrelated history. The session may rewrite only its own, unlanded commits.
    try:
        pinned_git(p, "merge-base", "--is-ancestor", old, tip, alternates=objects)
    except GitFailed:
        raise GitFailed("the session's bead branch does not descend from its landed tip") from None
    names = pinned_git(p, "rev-list", "--objects", tip, "--not", "--all", alternates=objects)
    if names.strip():
        pack = pinned_git(p, "pack-objects", "--stdout", alternates=objects, data=names)
        pinned_git(p, "index-pack", "--stdin", "--strict", "--fix-thin", data=pack)
    pinned_git(p, "rev-list", "--quiet", "--objects", tip, "--not", "--all")      # connected, here alone
    pinned_git(p, "update-ref", "-m", "wsd: land the session's commits", f"refs/heads/{p.branch}", tip, old)
    return tip
