"""The git operations of parking (ADR 0001 §4.3): find or make the WIP commit, and inspect a worktree.

The WIP commit message carries the park mark (`wsd-park: <op id>`), so a replayed park finds the commit
it already made instead of making a second one. The commit is local to the `btq/<id>` branch; nothing
is ever pushed (§5.3).

Every git call on a bead's worktree goes through `pin` and `pinned_git`: its directories come from the
trusted repository path, never from the worktree's `.git` file, which a sandboxed agent can rewrite
(plan 4 D26). `git` is for repository-level calls on trusted paths only (`worktree add`, tests).
"""

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT = 60
PARK_MARK = "wsd-park: "
# No hook runs for a WIP commit: `--no-verify` skips only pre-commit and commit-msg, and a
# prepare-commit-msg hook could strip the park mark. A hooks path that holds no hooks turns them all off.
NO_HOOKS = ("-c", f"core.hooksPath={os.devnull}")


class GitFailed(Exception):
    pass


def git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    if result.returncode:
        raise GitFailed(f"git {args[0]} exited {result.returncode}")
    return result.stdout.strip()


FULL_SHA = re.compile(r"[0-9a-f]{40}")
NO_FSMONITOR = ("-c", "core.fsmonitor=false")
# Pinned git reads the repository's own config only, never the system or global one.
ISOLATED = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
# A program an attribute can select. The agent writes the worktree's `.gitattributes` (and any script a
# relative command names), so with one configured, host git could run the agent's code on the host.
DRIVER = re.compile(r"filter\..+\.(clean|smudge|process)|diff\..+\.(textconv|command)|merge\..+\.driver")
INCLUDE = re.compile(r"include\.path|includeif\..+\.path")
META_LIMIT = 4096


@dataclass(frozen=True)
class Pinned:
    """A bead's worktree with its git directories taken from the trusted repository path, never from
    the worktree's own `.git` file, which an agent can rewrite (D26)."""
    work_tree: Path
    git_dir: Path               # <common>/worktrees/<n>, or the common directory for the main worktree
    common: Path                # <repo>/.git
    branch: str                 # btq/<id>


def no_link(base: Path, *parts: str) -> Path:
    path = base
    for part in parts:
        path = path / part
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise GitFailed("git metadata is a link")
    return path


def read_meta(path: Path, limit: int = META_LIMIT) -> str:
    """The whole of a regular file, never through a link. GitFailed if it holds more than `limit` bytes:
    metadata is never truncated."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GitFailed("git metadata is not a regular file")
        data = b""
        while chunk := os.read(fd, min(1 << 16, limit + 1 - len(data))):
            data += chunk
            if len(data) > limit:
                raise GitFailed("git metadata is too large")
        return data.decode("utf-8", "replace")
    finally:
        os.close(fd)


def pin(repo: Path, worktree: Path, branch: str) -> Pinned:
    """Pin `worktree` of `repo` on `branch`. GitFailed unless the worktree is one of the repository's own,
    its HEAD is exactly the branch, and nothing on the way is a link. Nor may any config git reads here,
    btq's unpinned git included, name a program an attribute can select (DRIVER), or the repository's
    own config include another file, which could gain one after this check."""
    try:
        root = repo.resolve(strict=True)
        common = no_link(root, ".git")
        work_tree = worktree.resolve(strict=True)
        if work_tree == root:
            git_dir = common
        else:
            pointer = f"{work_tree / '.git'}\n"
            found = [d for d in sorted(no_link(common, "worktrees").iterdir())
                     if stat.S_ISDIR(os.lstat(d).st_mode) and os.path.lexists(d / "gitdir")
                     and read_meta(d / "gitdir") == pointer]
            if len(found) != 1:
                raise GitFailed("the worktree is not one of the repository's linked worktrees")
            git_dir = found[0]
            if read_meta(git_dir / "commondir") != "../..\n":
                raise GitFailed("the worktree's common directory is redirected")
        if read_meta(git_dir / "HEAD") != f"ref: refs/heads/{branch}\n":
            raise GitFailed("the worktree's HEAD is not its bead branch")
    except OSError:
        raise GitFailed("the worktree's git metadata can't be read") from None
    p = Pinned(work_tree, git_dir, common, branch)
    if pinned_git(p, "config", "--get", "extensions.worktreeConfig", ok=(0, 1)).strip():
        raise GitFailed("the repository uses per-worktree config")
    own = pinned_git(p, "config", "--file", str(common / "config"), "--no-includes", "--name-only", "--list")
    if any(INCLUDE.fullmatch(name) for name in own.decode().split()):
        raise GitFailed("the repository's config includes another file")
    every = pinned_git(p, "config", "--name-only", "--list", host_config=True)
    if any(DRIVER.fullmatch(name) for name in every.decode().split()):
        raise GitFailed("a git filter, diff or merge driver is configured")
    return p


def pinned_git(p: Pinned, *args: str, alternates: Path | None = None, data: bytes | None = None,
               ok: tuple[int, ...] = (0,), host_config: bool = False) -> bytes:
    """git on the pinned directories, with no hook, no fsmonitor, no inherited GIT_* variable and only
    the repository's own config; `host_config` reads the system and global config too, as btq's would."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_DIR": str(p.git_dir), "GIT_COMMON_DIR": str(p.common), "GIT_WORK_TREE": str(p.work_tree)}
    if not host_config:
        env |= ISOLATED
    if alternates is not None:
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(alternates)
    try:
        result = subprocess.run(["git", *NO_HOOKS, *NO_FSMONITOR, *args], cwd=p.work_tree, env=env,
                                input=data, capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    if result.returncode not in ok:
        raise GitFailed(f"git {args[0]} exited {result.returncode}")
    return result.stdout


def _text(p: Pinned, *args: str) -> str:
    return pinned_git(p, *args).decode().strip()


def find_wip(p: Pinned, mark: str) -> str | None:
    """The SHA of the commit on HEAD's history whose message holds the mark, if any. Only the last 50
    commits are searched: a park's own commit is at or near the tip."""
    shas = _text(p, "log", "-n", "50", "--format=%H", "--fixed-strings", f"--grep={PARK_MARK}{mark}").split()
    return shas[0] if shas else None


def wip_commit(p: Pinned, mark: str, summary: str) -> str:
    """Commit everything in the worktree as WIP and return HEAD. Idempotent: if a commit with the mark
    already exists, return it; if there is nothing to commit, an empty commit still records the mark."""
    found = find_wip(p, mark)
    if found is not None:
        return found
    _text(p, "add", "--all")
    _text(p, "-c", "user.name=wsd", "-c", "user.email=wsd@localhost", "commit", "--allow-empty",
          "--no-verify", "-m", f"WIP: {summary}\n\n{PARK_MARK}{mark}")
    head = _text(p, "rev-parse", "HEAD")
    if find_wip(p, mark) != head:      # the mark is what a replayed park looks for
        raise GitFailed("the WIP commit does not carry its park mark")
    return head


def descends_from(p: Pinned, base: str) -> bool:
    """HEAD is the commit `base` or a descendant of it. git refuses (and this is False) when `base` names
    no object, or an object that is not a commit (a tree, a blob)."""
    try:
        pinned_git(p, "merge-base", "--is-ancestor", base, "HEAD")
    except GitFailed:
        return False
    return True
