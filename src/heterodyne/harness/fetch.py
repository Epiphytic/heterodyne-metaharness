"""Fetching a managed install's artifacts (ADR r16 §7 Managed installs; plan 4c E5, E6): a release asset
with its line from the release's `SHA256SUMS`, an npm tarball with its version document's `dist.integrity`,
and a git commit as `git archive` and `git cat-file commit` from a bare cache. Each artifact is a 0600 temp
file in the cache, checked before it is returned, and removed if anything refuses.

Every fetch has one deadline from the injected clock, and every size is bounded. The production opener,
`HttpsOpener`, speaks HTTPS only, refuses a redirect off HTTPS, and holds each request to a total deadline
however slowly the server sends. Refusals carry their
constant as `.constant`, and their message is the constant alone: nothing here names a URL, a path or a
checksum in an error.
"""

import contextlib
import dataclasses
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, urljoin, urlsplit

from heterodyne.harness.resources import store_core as sc

NOT_IN_SUMS = "the release's SHA256SUMS has no line for the asset"
DEADLINE = "the fetch passed its deadline"
TOO_LARGE = "a fetched artifact or document is larger than the store accepts"
BAD_COMMIT = "a git source's commit is not a full lowercase commit ID"
INSIDE_LOGIN_DIR = "a local git source lies inside a login directory"
BAD_URL = "a source's URL, name or version is not one the store fetches"
BAD_DOCUMENT = "a release's SHA256SUMS or an npm version document is malformed or names another package"
FETCH_FAILED = "a fetch failed"
GIT_FAILED = "git could not fetch or export the commit"
INSECURE_REDIRECT = "a fetch was redirected off HTTPS"

MAX_ARTIFACT = 1 << 30
MAX_DOCUMENT = 4 << 20
MAX_REDIRECTS = 5
REDIRECTS = frozenset({301, 302, 303, 307, 308})
NPM_REGISTRY = "https://registry.npmjs.org"
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")            # an asset name or a version
NPM_NAME = re.compile(r"(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")
SUMS_LINE = re.compile(r"([0-9a-fA-F]{64}) [ *](.+)")
URL_SAFE = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._~%@+-]*)*")


class FetchRefused(sc.Refused):
    """A fetch refused, with one of this module's constants (or `store_core`'s for a checksum)."""


class Response(Protocol):
    def read(self, n: int = -1, /) -> bytes: ...

    def close(self) -> None: ...


Opener = Callable[..., Response]
"""`opener(url, timeout=seconds)`: the response must be done (read to its end, or failed) within `timeout`
seconds of the call, every redirect it followed must have stayed on HTTPS, and its `url`, if it has one,
is the last hop's. `HttpsOpener` is the production one."""


class HttpsOpener:
    """GET over HTTPS only (`INSECURE_REDIRECT` for a redirect to anything else), following at most
    `MAX_REDIRECTS` redirects, with a total deadline: a watchdog shuts the socket down `timeout` seconds after
    the call, so a server that trickles bytes can't hold a read past it (`DEADLINE`). `connection` is
    `http.client.HTTPSConnection`; tests pass a plain one to a loopback server."""

    def __init__(self, *, connection: Callable[..., http.client.HTTPConnection] = http.client.HTTPSConnection,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.connection = connection
        self.monotonic = monotonic

    def __call__(self, url: str, timeout: float) -> "HttpsResponse":
        end = self.monotonic() + timeout
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise FetchRefused(INSECURE_REDIRECT)
            left = end - self.monotonic()
            if left <= 0:
                raise FetchRefused(DEADLINE)
            target = parts.path or "/"
            if parts.query:
                target += "?" + parts.query
            conn = self.connection(parts.hostname, parts.port, timeout=left)
            resp = HttpsResponse(conn, url, left)
            try:
                conn.connect()
                resp.bind(conn.sock)
                conn.request("GET", target,
                             headers={"Accept-Encoding": "identity", "User-Agent": "heterodyne"})
                resp.attach(conn.getresponse())
            except (OSError, http.client.HTTPException):
                resp.close()
                raise FetchRefused(DEADLINE if resp.expired else FETCH_FAILED) from None
            if resp.status not in REDIRECTS:
                return resp
            location = resp.header("Location")
            resp.close()
            if not location:
                raise FetchRefused(FETCH_FAILED)
            url = urljoin(url, location)
        raise FetchRefused(FETCH_FAILED)


class HttpsResponse:
    """One request's response, with the watchdog that ends it at its deadline."""

    def __init__(self, conn: http.client.HTTPConnection, url: str, timeout: float) -> None:
        self.conn = conn
        self.url = url
        self.status = 0
        self.expired = False
        self._resp: http.client.HTTPResponse | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._watchdog = threading.Timer(timeout, self._expire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _expire(self) -> None:
        with self._lock:
            self.expired = True
            self._shutdown()

    def bind(self, sock: socket.socket) -> None:
        """Keep the connected socket: `getresponse` hands a closing connection's socket to the response
        and drops `conn.sock`, so the watchdog can't look it up there."""
        with self._lock:
            self._sock = sock
            if self.expired:
                self._shutdown()

    def _shutdown(self) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.shutdown(socket.SHUT_RDWR)

    def attach(self, resp: http.client.HTTPResponse) -> None:
        self._resp = resp
        self.status = resp.status

    def header(self, name: str) -> str | None:
        return self._resp.getheader(name) if self._resp is not None else None

    def read(self, n: int = -1, /) -> bytes:
        if self._resp is None:
            return b""
        try:
            chunk = self._resp.read(n)
        except (OSError, http.client.HTTPException):
            raise FetchRefused(DEADLINE if self.expired else FETCH_FAILED) from None
        if self.expired:                        # a shut-down socket can look like the body's end
            raise FetchRefused(DEADLINE)
        return chunk

    def close(self) -> None:
        self._watchdog.cancel()
        self.conn.close()


@dataclasses.dataclass(frozen=True)
class Artifact:
    path: Path
    checksum: str               # `sha256:<hex>` or an SRI `sha512-<base64>`
    origin: str                 # where the checksum came from
    signature_available: bool = False       # an npm document's `dist.signatures` (never verified in v1)


@dataclasses.dataclass(frozen=True)
class GitArtifact:
    archive: Path
    commit_object: Path
    commit: str


class Fetcher:
    def __init__(self, opener: Opener, cache: Path, clock: Callable[[], float], deadline_seconds: float, *,
                 login_dirs: Sequence[Path] = (), npm_registry: str = NPM_REGISTRY, git: str = "git") -> None:
        self.opener = opener
        self.cache = cache
        self.clock = clock
        self.deadline_seconds = deadline_seconds
        self.login_dirs = tuple(login_dirs)
        self.npm_registry = npm_registry
        self.git_command = git

    # --- the three kinds of source ---

    def release(self, url_base: str, asset: str, sums_asset: str) -> Artifact:
        """`<url_base>/<asset>`, checked against its line in `<url_base>/<sums_asset>`."""
        if not _https(url_base) or not PLAIN.fullmatch(asset) or not PLAIN.fullmatch(sums_asset):
            raise FetchRefused(BAD_URL)
        end = self.clock() + self.deadline_seconds
        origin = f"{url_base}/{sums_asset}"
        checksum = "sha256:" + _sums_line(self._document(origin, end), asset)
        return self._artifact(f"{url_base}/{asset}", checksum, origin, end)

    def npm(self, name: str, version: str) -> Artifact:
        """The tarball of `name@version`, checked against its version document's `dist.integrity`, which must
        be sha512."""
        if (not NPM_NAME.fullmatch(name) or not PLAIN.fullmatch(version)
                or not _https(self.npm_registry)):
            raise FetchRefused(BAD_URL)
        end = self.clock() + self.deadline_seconds
        origin = f"{self.npm_registry}/{quote(name, safe='@')}/{version}"
        try:
            doc: object = json.loads(self._document(origin, end))
        except (ValueError, RecursionError):
            raise FetchRefused(BAD_DOCUMENT) from None
        fields = cast(dict[str, object], doc) if isinstance(doc, dict) else {}
        dist = fields.get("dist")
        dist = cast(dict[str, object], dist) if isinstance(dist, dict) else {}
        tarball, integrity = dist.get("tarball"), dist.get("integrity")
        if (fields.get("name") != name or fields.get("version") != version or not isinstance(tarball, str)
                or not _https(tarball) or not isinstance(integrity, str)
                or not integrity.startswith(sc.SRI_PREFIX)):
            raise FetchRefused(BAD_DOCUMENT)
        signatures = dist.get("signatures")
        signed = isinstance(signatures, list) and len(cast(list[object], signatures)) > 0
        artifact = self._artifact(tarball, integrity, origin, end)
        return dataclasses.replace(artifact, signature_available=signed)

    def git(self, repo: str, commit: str) -> GitArtifact:
        """`commit` of `repo` (an https URL, or an absolute local path outside every login directory) through
        a bare cache, exported as `git archive` and `git cat-file commit` and checked with
        `store_core.check_git`. No hook of the source or the cache runs."""
        if not sc.COMMIT_ID.fullmatch(commit):
            raise FetchRefused(BAD_COMMIT)
        source = self._git_source(repo)
        end = self.clock() + self.deadline_seconds
        object_format = "sha1" if len(commit) == 40 else "sha256"
        key = hashlib.sha256(source.encode("utf-8", "surrogateescape")).hexdigest()[:32]
        bare = self._cache() / "git" / f"{key}-{object_format}.git"
        if not bare.is_dir():
            bare.parent.mkdir(mode=0o700, exist_ok=True)
            self._run_git(["init", "-q", "--bare", f"--object-format={object_format}", str(bare)], end)
        at = ["-C", str(bare)]
        self._run_git([*at, "fetch", "-q", "--no-tags", "--no-write-fetch-head", source, commit], end)
        archive = self._run_git([*at, "-c", "tar.umask=022", "archive", "--format=tar", commit], end,
                                to_file=True)
        try:
            commit_object = self._run_git([*at, "cat-file", "commit", commit], end, to_file=True)
        except BaseException:
            archive.unlink()
            raise
        try:
            fds = [os.open(archive, os.O_RDONLY | os.O_CLOEXEC),
                   os.open(commit_object, os.O_RDONLY | os.O_CLOEXEC)]
            try:
                sc.check_git(fds[0], fds[1], commit)
            finally:
                for fd in fds:
                    os.close(fd)
        except BaseException:
            archive.unlink()
            commit_object.unlink()
            raise
        return GitArtifact(archive, commit_object, commit)

    # --- the parts they share ---

    def _cache(self) -> Path:
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self.cache

    def _remaining(self, end: float) -> float:
        left = end - self.clock()
        if left <= 0:
            raise FetchRefused(DEADLINE)
        return left

    def _read(self, url: str, end: float, limit: int, sink: Callable[[bytes], object]) -> None:
        """`url`'s body into `sink`, in chunks, within the deadline and `limit` bytes."""
        try:
            resp = self.opener(url, timeout=self._remaining(end))
        except OSError:
            raise FetchRefused(DEADLINE if self.clock() >= end else FETCH_FAILED) from None
        try:
            final = getattr(resp, "url", None)
            if isinstance(final, str) and not final.startswith("https://"):
                raise FetchRefused(INSECURE_REDIRECT)
            if getattr(resp, "status", 200) != 200:
                raise FetchRefused(FETCH_FAILED)
            total = 0
            while True:
                self._remaining(end)
                try:
                    chunk = resp.read(sc.CHUNK)
                except OSError:
                    raise FetchRefused(DEADLINE if self.clock() >= end else FETCH_FAILED) from None
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise FetchRefused(TOO_LARGE)
                sink(chunk)
            self._remaining(end)
        finally:
            resp.close()

    def _document(self, url: str, end: float) -> bytes:
        out = bytearray()
        self._read(url, end, MAX_DOCUMENT, out.extend)
        return bytes(out)

    def _artifact(self, url: str, checksum: str, origin: str, end: float) -> Artifact:
        """`url` into a 0600 temp file in the cache, checked against `checksum`; removed on any refusal."""
        fd, name = tempfile.mkstemp(prefix="artifact-", dir=self._cache())
        path = Path(name)
        try:
            try:
                self._read(url, end, MAX_ARTIFACT, lambda chunk: _write_all(fd, chunk))
                sc.verify_checksum(fd, checksum)
            finally:
                os.close(fd)
        except BaseException:
            path.unlink()
            raise
        return Artifact(path, checksum, origin)

    def _git_source(self, repo: str) -> str:
        if repo.startswith("https://"):
            if not URL_SAFE.fullmatch(repo):
                raise FetchRefused(BAD_URL)
            return repo
        if not Path(repo).is_absolute():
            raise FetchRefused(BAD_URL)
        resolved = Path(repo).resolve()
        for d in self.login_dirs:
            login = Path(d).resolve()
            if resolved == login or login in resolved.parents:
                raise FetchRefused(INSIDE_LOGIN_DIR)
        return str(resolved)

    def _run_git(self, args: list[str], end: float, *, to_file: bool = False) -> Path:
        """`git` with no user or system config, no prompt and no hooks; its stdout into a 0600 temp file in
        the cache if `to_file`. A failure is `GIT_FAILED`, a timeout `DEADLINE`."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file:https",
               "LC_ALL": "C"}
        cmd = [self.git_command, "-c", f"core.hooksPath={os.devnull}", *args]
        out_fd, name = tempfile.mkstemp(prefix="git-", dir=self._cache()) if to_file else (-1, "")
        path = Path(name)
        try:
            try:
                done = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL,
                                      stdout=out_fd if to_file else subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=self._remaining(end), check=False)
            except subprocess.TimeoutExpired:
                raise FetchRefused(DEADLINE) from None
            except OSError:
                raise FetchRefused(GIT_FAILED) from None
            finally:
                if to_file:
                    os.close(out_fd)
            if done.returncode != 0:
                raise FetchRefused(GIT_FAILED)
            if to_file and path.stat().st_size > MAX_ARTIFACT:
                raise FetchRefused(TOO_LARGE)
        except BaseException:
            if to_file:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            raise
        return path


def _https(url: str) -> bool:
    return bool(URL_SAFE.fullmatch(url))


def _sums_line(sums: bytes, asset: str) -> str:
    """The lowercase sha256 hex `sums` gives `asset` (`NOT_IN_SUMS`; two lines that disagree, or a file that
    isn't ASCII, `BAD_DOCUMENT`)."""
    try:
        text = sums.decode("ascii")
    except UnicodeDecodeError:
        raise FetchRefused(BAD_DOCUMENT) from None
    found = {m.group(1).lower() for line in text.splitlines()
             if (m := SUMS_LINE.fullmatch(line.rstrip("\r"))) and m.group(2) == asset}
    if not found:
        raise FetchRefused(NOT_IN_SUMS)
    if len(found) > 1:
        raise FetchRefused(BAD_DOCUMENT)
    return found.pop()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
