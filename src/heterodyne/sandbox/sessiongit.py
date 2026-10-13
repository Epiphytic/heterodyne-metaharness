"""The session's own git directory (ADR 0001 §7: agents commit to their local btq/<id> branch; D26).

The sandbox never sees the repository's real git directory. Each generation gets a private one, bound at
the linked worktree's git-directory path so the worktree's own `.git` file finds it, with the common
object store read-only behind `objects/info/alternates`. Once the sandbox is gone, `land` imports the
bead branch alone, through pinned host git only.

An operator's resolve of commits `land` refused (btq-xb56t) never uses the private directory as a git
directory, an alternate or a config source: `snapshot` copies its bead-branch tip and its object files
alone into a wsd-owned store and checks them there, and `import_snapshot` imports from that store."""

import contextlib
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from heterodyne.agents.base import write_at
from heterodyne.sandbox.spec import Bind
from heterodyne.wsd.gitwip import (
    FULL_SHA,
    GIT_TIMEOUT,
    ISOLATED,
    NO_FSMONITOR,
    NO_HOOKS,
    NO_SIGN,
    GitFailed,
    Pinned,
    no_link,
    pinned_git,
    read_meta,
)

SNAPSHOT = ("refs/heads", "refs/remotes", "refs/tags")
PACKED_LIMIT = 64 << 20         # packed-refs larger than this fails the landing; it is never truncated
OBJECT_LIMIT = 1 << 30          # a loose object or pack larger than this fails the snapshot
SUBJECTS = 20                   # commit subjects a view shows
LOOSE_DIR = re.compile(r"[0-9a-f]{2}")
LOOSE = re.compile(r"[0-9a-f]{38}")
PACK = re.compile(r"pack-[0-9a-f]{40}([0-9a-f]{24})?\.pack")
UNREADABLE = "the session's git objects can't be read"
LINKED = "the session's git objects hold a link or a special file"
HARD_LINKED = "the session's git objects hold a hard link"
TOO_LARGE = "a session git object is too large"
CORRUPT = "the session's git objects fail git's integrity check"
NOT_A_COMMIT = "the session's bead branch is not a commit"
INCOMPLETE = "the session's commits are incomplete"


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
        raise GitFailed(UNREADABLE)

    for _top, dirs, files, topfd in os.fwalk(".", dir_fd=objfd, onerror=fail):
        for name in (*dirs, *files):
            mode = os.stat(name, dir_fd=topfd, follow_symlinks=False).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise GitFailed(LINKED)


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
    _import(p, objects, tip)
    pinned_git(p, "update-ref", "-m", "wsd: land the session's commits", f"refs/heads/{p.branch}", tip, old)
    return tip


def _import(p: Pinned, objects: Path, tip: str) -> None:
    """Copy what `tip` needs from `objects` into the repository, checked strictly, and confirm it is
    connected there alone."""
    names = pinned_git(p, "rev-list", "--objects", tip, "--not", "--all", alternates=objects)
    if names.strip():
        pack = pinned_git(p, "pack-objects", "--stdout", alternates=objects, data=names)
        pinned_git(p, "index-pack", "--stdin", "--strict", "--fix-thin", data=pack)
    pinned_git(p, "rev-list", "--quiet", "--objects", tip, "--not", "--all")      # connected, here alone


# --- the operator's resolve (btq-xb56t) ---


@dataclass(frozen=True)
class Snapshot:
    """A wsd-owned copy of a session's objects. `tip` is None, with a fixed `error`, unless the copy
    passed every check: nothing in `dest` is read before that."""
    dest: Path
    tip: str | None
    error: str = ""


def _store_git(p: Pinned, dest: Path, *args: str, alternate: bool = True) -> bytes:
    """Host git on the snapshot store: no hook, no fsmonitor, no signing, no inherited GIT_* variable and no
    config but the store's own. The bead repository's objects are its only alternate, given on each call,
    so the store's own files never name one, and `fsck` (which walks every alternate) checks its own."""
    env = _clean_env() | {"GIT_DIR": str(dest)}
    if alternate:
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(p.common / "objects")
    try:
        result = subprocess.run(["git", *NO_HOOKS, *NO_FSMONITOR, *NO_SIGN, *args], cwd=dest, env=env,
                                capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    if result.returncode:
        raise GitFailed(f"git {args[0]} exited {result.returncode}")
    return result.stdout


def _clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | ISOLATED


def _copy(srcfd: int, name: str, target: Path) -> None:
    """Copy one object file by its descriptor: a regular file with a single link, never through a link,
    and within OBJECT_LIMIT however it grows while it is read."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=srcfd)
    except OSError:
        raise GitFailed(UNREADABLE) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise GitFailed(LINKED)
        if st.st_nlink != 1:
            raise GitFailed(HARD_LINKED)
        if st.st_size > OBJECT_LIMIT:
            raise GitFailed(TOO_LARGE)
        out = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            copied = 0
            while chunk := os.read(fd, 1 << 20):
                copied += len(chunk)
                if copied > OBJECT_LIMIT:
                    raise GitFailed(TOO_LARGE)
                os.write(out, chunk)
        finally:
            os.close(out)
    except OSError:
        raise GitFailed(UNREADABLE) from None
    finally:
        os.close(fd)


def _copy_objects(private: Path, dest: Path) -> None:
    """Copy the loose objects and packs alone: indexes, `info/` (alternates included) and every other file
    are left behind. The walk is anchored at descriptors opened without following a link."""
    try:
        gitfd = os.open(private, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise GitFailed("the session's git directory can't be opened") from None
    try:
        objfd = _subdir(gitfd, "objects")
        try:
            _plain_tree(objfd)
            for name in sorted(os.listdir(objfd)):  # noqa: PTH208 - by descriptor, never by path
                if not (LOOSE_DIR.fullmatch(name) or name == "pack"):
                    continue
                pattern = PACK if name == "pack" else LOOSE
                target = dest / "objects" / name
                target.mkdir(mode=0o700, exist_ok=True)
                subfd = _subdir(objfd, name)
                try:
                    for leaf in sorted(os.listdir(subfd)):  # noqa: PTH208
                        if pattern.fullmatch(leaf):
                            _copy(subfd, leaf, target)
                finally:
                    os.close(subfd)
        finally:
            os.close(objfd)
    except OSError:
        raise GitFailed(UNREADABLE) from None
    finally:
        os.close(gitfd)


def _check(p: Pinned, dest: Path, tip: str) -> None:
    """Every pack re-indexed strictly, every object re-hashed and checked, the tip a commit and complete
    from the store and the bead repository's objects."""
    try:
        for pack in sorted((dest / "objects" / "pack").glob("pack-*.pack")):
            _store_git(p, dest, "index-pack", "--strict", str(pack))
        _store_git(p, dest, "fsck", "--strict", "--full", "--no-dangling", alternate=False)
    except GitFailed:
        raise GitFailed(CORRUPT) from None
    try:
        kind = _store_git(p, dest, "cat-file", "-t", tip).decode().strip()
    except GitFailed:
        raise GitFailed(INCOMPLETE) from None
    if kind != "commit":
        raise GitFailed(NOT_A_COMMIT)
    try:
        _store_git(p, dest, "rev-list", "--quiet", "--objects", tip)
    except GitFailed:
        raise GitFailed(INCOMPLETE) from None


def snapshot(p: Pinned, private: Path, dest: Path) -> Snapshot:
    """Copy the session's bead-branch tip and object files from `private` into `dest`, a fresh wsd-owned
    store, and check them there. `private` is only read. The caller confirms first that nothing can write
    it, and removes `dest` when done. A tip or object that can't be read or fails a check is a Snapshot
    with no tip and a fixed reason; GitFailed only when the store itself can't be made."""
    dest.mkdir(mode=0o700)
    try:
        subprocess.run(["git", "init", "-q", "--bare", "--template=", str(dest)], env=_clean_env(),
                       capture_output=True, timeout=GIT_TIMEOUT, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    _store_git(p, dest, "config", "core.hooksPath", os.devnull, alternate=False)
    try:
        tip = _tip(private, p.branch)
        _copy_objects(private, dest)
        _check(p, dest, tip)
    except GitFailed as exc:
        return Snapshot(dest, None, str(exc))
    return Snapshot(dest, tip)


@dataclass(frozen=True)
class Comparison:
    ahead: int
    behind: int
    subjects: tuple[bytes, ...]         # raw and untrusted: the caller makes them printable


def compare(p: Pinned, snap: Snapshot, landed: str) -> Comparison:
    """The snapshot's tip against the landed one: commits ahead and behind, and the subjects of the first
    SUBJECTS commits ahead, read from the checked store alone."""
    if snap.tip is None:
        raise GitFailed("the snapshot has no tip")
    counts = _store_git(p, snap.dest, "rev-list", "--left-right", "--count", f"{landed}...{snap.tip}")
    behind, ahead = (int(n) for n in counts.split())
    listed = _store_git(p, snap.dest, "log", "-z", "--no-show-signature", "--no-mailmap", "--no-notes",
                        "--format=%s", "-n", str(SUBJECTS), f"{landed}..{snap.tip}")
    return Comparison(ahead, behind, tuple(s for s in listed.split(b"\0") if s))


def landed(p: Pinned) -> str:
    text = pinned_git(p, "rev-parse", "--verify", f"refs/heads/{p.branch}^{{commit}}").decode().strip()
    if not FULL_SHA.fullmatch(text):
        raise GitFailed("the bead branch has no commit")
    return text


def fast_forward(p: Pinned, snap: Snapshot, old: str) -> bool:
    if snap.tip is None:
        return False
    try:
        _store_git(p, snap.dest, "merge-base", "--is-ancestor", old, snap.tip)
    except GitFailed:
        return False
    return True


def ref_at(p: Pinned, ref: str) -> str | None:
    """The SHA `ref` names in the repository, or None if there is no such ref."""
    text = pinned_git(p, "rev-parse", "--verify", "-q", ref, ok=(0, 1)).decode().strip()
    return text or None


def import_snapshot(p: Pinned, snap: Snapshot, tip: str, ref: str, old: str) -> None:
    """Import `tip` from the checked store into the repository, then move `ref` from `old` to it, compare
    and swap. `tip` is the one the caller confirmed, used throughout and never read again; `old` is the
    zero SHA to create the ref only."""
    if snap.tip is None or snap.tip != tip:
        raise GitFailed("the snapshot does not hold that tip")
    _import(p, snap.dest / "objects", tip)
    pinned_git(p, "update-ref", "-m", "wsd: resolve the session's unlanded commits", ref, tip, old)
