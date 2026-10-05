"""The git operations of parking (ADR 0001 §4.3): find or make the WIP commit, and inspect a worktree.

The WIP commit message carries the park mark (`wsd-park: <op id>`), so a replayed park finds the commit
it already made instead of making a second one. The commit is local to the `btq/<id>` branch; nothing
is ever pushed (§5.3).
"""

import subprocess
from pathlib import Path

GIT_TIMEOUT = 60
PARK_MARK = "wsd-park: "


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


def branch(path: Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD")


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def find_wip(worktree: Path, mark: str) -> str | None:
    """The SHA of the commit on HEAD's history whose message holds the mark, if any. Only the last 50
    commits are searched: a park's own commit is at or near the tip."""
    out = git(worktree, "log", "-n", "50", "--format=%H", "--fixed-strings", f"--grep={PARK_MARK}{mark}")
    shas = out.split()
    return shas[0] if shas else None


def wip_commit(worktree: Path, mark: str, summary: str) -> str:
    """Commit everything in the worktree as WIP and return HEAD. Idempotent: if a commit with the mark
    already exists, return it; if there is nothing to commit, an empty commit still records the mark."""
    found = find_wip(worktree, mark)
    if found is not None:
        return found
    git(worktree, "add", "--all")
    git(worktree, "-c", "user.name=wsd", "-c", "user.email=wsd@localhost", "commit", "--allow-empty",
        "--no-verify", "-m", f"WIP: {summary}\n\n{PARK_MARK}{mark}")
    return git(worktree, "rev-parse", "HEAD")


def common_dir(path: Path) -> Path:
    """The git common directory: shared by a repository and every worktree made from it."""
    out = git(path, "rev-parse", "--git-common-dir")
    return (path / out).resolve() if not Path(out).is_absolute() else Path(out).resolve()
