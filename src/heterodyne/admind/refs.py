"""`--ref PATH[@REV]`: a repository path resolved by the client into a pinned GitHub link (cxmkf K21, K21h),
and the bare-path hints (K22p).

The client resolves the commit locally, then verifies on GitHub that the commit and the path exist at the
repository `origin` names; the daemon checks the resulting link's form again and never trusts this. Every
git and gh call is an argument array with a timeout, stdin from /dev/null, bounded output and stderr
discarded. git runs with a configuration and an environment built from scratch, so a hostile checkout can't
run code or turn a revision into an option; it never touches the network. gh always targets github.com.
Refusals here are the client's: nothing reaches the daemon or its audit.
"""

import json
import os
import re
import selectors
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import cast
from urllib.parse import quote

from heterodyne.admind.links import MAX_LABEL, LinkSpec

GIT_SECONDS, GH_SECONDS = 5.0, 10.0
MAX_OUTPUT = 1 << 20            # bytes read from one call; more is a failure
MAX_LS_FILES = 16 << 20         # `git ls-files` for the hints
MAX_HINTS = 5
REV = re.compile(r"[0-9A-Za-z][0-9A-Za-z._/@^~-]{0,199}")
SHA = re.compile(r"[0-9a-f]{40}")
_NAME = r"[A-Za-z0-9._-]{1,100}"
_REMOTES = (re.compile(rf"https://github\.com/({_NAME})/({_NAME}?)(?:\.git)?/?"),
            re.compile(rf"(?:ssh://)?git@github\.com[:/]({_NAME})/({_NAME}?)(?:\.git)?/?"))
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_STATUS = re.compile(rb"\AHTTP/[0-9.]+ ([0-9]{3})")
GIT = ("git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
       "-c", "protocol.allow=never", "-c", "core.sshCommand=false", "-c", "credential.helper=")
_GH_DROP = ("GH_HOST", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG")

NOT_GITHUB = "origin is not a GitHub repository"
UNCHECKED = "can't check the link on GitHub; pass --link with a pinned URL instead"

Runner = Callable[[Sequence[str], dict[str, str], float, int], tuple[int, bytes] | None]


class RefError(ValueError):
    """A `--ref` that can't be made a link; the message is for the poster."""


def run(argv: Sequence[str], env: dict[str, str], seconds: float, limit: int) -> tuple[int, bytes] | None:
    """(exit status, stdout) of `argv`, or None if it can't start, outlives `seconds` or prints more than
    `limit` bytes (it is killed). No shell; stdin is /dev/null and stderr is discarded."""
    try:
        proc = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,  # noqa: S603
                                stderr=subprocess.DEVNULL, env=env, shell=False)
    except OSError:
        return None
    if proc.stdout is None:     # not reached: stdout is a pipe
        proc.kill()
        return None
    out = bytearray()
    deadline = time.monotonic() + seconds
    with proc, selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        while (left := deadline - time.monotonic()) > 0:
            if not sel.select(left):
                continue
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            out += data
            if len(out) > limit:
                proc.kill()
                return None
        else:
            proc.kill()
            return None
        try:
            status = proc.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            return None
    return status, bytes(out)


def git_env() -> dict[str, str]:
    """git's environment, from scratch: no inherited `GIT_*` (K21h)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/nonexistent"),
            "LANG": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def gh_env() -> dict[str, str]:
    """gh's environment: the caller's (for its login), never another host or debug output (K21h)."""
    env = {k: v for k, v in os.environ.items() if k not in _GH_DROP}
    return {**env, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}


def check_path(path: str) -> str | None:
    """Why PATH is refused before any call, or None."""
    if not 1 <= len(path) <= 1000 or path.startswith(("/", "-")) or ":" in path or _CONTROLS.search(path):
        return "--ref PATH must be a relative path of 1-1000 characters, without ':' or controls"
    if any(seg in ("", ".", "..") for seg in path.split("/")):
        return "--ref PATH must not have an empty, '.' or '..' segment"
    return None


def split(spec: str) -> tuple[str, str]:
    """PATH[@REV] as (path, rev): REV is after the last `@`, `HEAD` when there is none."""
    path, at, rev = spec.rpartition("@")
    return (path, rev) if at else (spec, "HEAD")


def label(path: str) -> str:
    """The link's label: the path, or its last characters behind `…` when it is over MAX_LABEL."""
    return path if len(path) <= MAX_LABEL else "…" + path[-(MAX_LABEL - 1):]


def origin(text: str) -> tuple[str, str] | None:
    """(owner, repo) of a GitHub remote URL, HTTPS or SSH, or None."""
    for form in _REMOTES:
        m = form.fullmatch(text.strip())
        if m is not None and m.group(2) and m.group(2) not in (".", ".."):
            return m.group(1), m.group(2)
    return None


class Resolver:
    """Resolves `--ref`s in `cwd`. `runner` is `run`, or a stand-in in tests."""

    def __init__(self, cwd: str | None = None, runner: Runner = run) -> None:
        self.cwd = cwd
        self.runner = runner

    def git(self, *args: str, limit: int = MAX_OUTPUT) -> tuple[int, bytes] | None:
        argv = [*GIT, *(("-C", self.cwd) if self.cwd is not None else ()), *args]
        return self.runner(argv, git_env(), GIT_SECONDS, limit)

    def gh(self, endpoint: str) -> tuple[int, bytes] | None:
        argv = ["gh", "api", "--hostname", "github.com", "--method", "GET", "--include", endpoint]
        return self.runner(argv, gh_env(), GH_SECONDS, MAX_OUTPUT)

    def resolve(self, spec: str) -> LinkSpec:
        """K21's four steps; raises RefError with the poster's message."""
        path, rev = split(spec)
        why = check_path(path)
        if why is not None:
            raise RefError(why)
        if not REV.fullmatch(rev):
            raise RefError("--ref REV must be a revision of 1-200 characters starting with a letter or digit")
        out = self.git("rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}")
        sha = out[1].decode("ascii", "replace").strip() if out is not None and out[0] == 0 else ""
        if not SHA.fullmatch(sha):
            raise RefError(f"--ref: {rev} is not a commit in this repository")
        kind = self.git("cat-file", "-t", f"{sha}:{path}")
        what = kind[1].strip() if kind is not None and kind[0] == 0 else b""
        if self.git("cat-file", "-e", f"{sha}:{path}") != (0, b"") or what not in (b"blob", b"tree"):
            raise RefError(f"--ref: {path} is not a file or directory at {sha}")
        remote = self.git("remote", "get-url", "--end-of-options", "origin")
        repo = None if remote is None or remote[0] != 0 else origin(remote[1].decode("utf-8", "replace"))
        if repo is None:
            raise RefError(NOT_GITHUB)
        owner, name = repo
        commit = self.gh(f"repos/{owner}/{name}/commits/{sha}")
        status = _status(commit)
        if status == 404:
            raise RefError(f"{sha} is not on GitHub at {owner}/{name}; push it first")
        if status != 200 or commit is None or commit[0] != 0 or _sha(commit[1]) != sha:
            raise RefError(UNCHECKED)
        encoded = quote(path, safe="/-._~")
        contents = _status(self.gh(f"repos/{owner}/{name}/contents/{encoded}?ref={sha}"))
        if contents == 404:
            raise RefError(f"{path} does not exist at {sha} on GitHub")
        if contents != 200:
            raise RefError(UNCHECKED)
        return LinkSpec(label(path), f"https://github.com/{owner}/{name}/{what.decode()}/{sha}/{encoded}")

    def hints(self, *texts: str) -> list[str]:
        """K22p: up to MAX_HINTS hints for tokens in `texts` that are exactly a tracked path with a `/`.
        Never refuses; no hints if git can't list the files."""
        out = self.git("ls-files", "--full-name", "-z", limit=MAX_LS_FILES)
        if out is None or out[0] != 0:
            return []
        tracked = {p for p in out[1].decode("utf-8", "replace").split("\0") if "/" in p}
        found: list[str] = []
        for text in texts:
            for token in text.split():
                path = _bare(token)
                if path in tracked and path not in found:
                    found.append(path)
        return [f"hint: {p} is a bare path; --ref {p} renders it as a link" for p in found[:MAX_HINTS]]


def _bare(token: str) -> str:
    if "://" in token:
        return ""
    t = token.strip("\"'`[]()<>{}")
    if t.endswith((".", ",", ":", ";", ")")):
        t = t[:-1]
    return t.strip("\"'`[]()<>{}")


def _status(reply: tuple[int, bytes] | None) -> int | None:
    """The HTTP status from the first line of `gh api --include` output."""
    m = None if reply is None else _STATUS.match(reply[1])
    return None if m is None else int(m.group(1))


def _sha(reply: bytes) -> str | None:
    """The `sha` of a commit from `gh api --include` output: the JSON after the headers."""
    _, sep, body = reply.replace(b"\r\n", b"\n").partition(b"\n\n")
    try:
        data: object = json.loads(body) if sep else None
    except ValueError:
        return None
    sha = cast("dict[str, object]", data).get("sha") if isinstance(data, dict) else None
    return sha if isinstance(sha, str) else None
