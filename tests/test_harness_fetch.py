"""Plan 4c Task 3: fetching artifacts and their checksums (plan 4c E5, E6). The opener is a fixture that
serves artifacts built in a temp dir, or `HttpsOpener` against a loopback server on 127.0.0.1 with a plain
connection; nothing here touches the network. Checksums are computed at run time and compared as values."""

import contextlib
import hashlib
import http.client
import http.server
import io
import json
import os
import stat
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from harness_env import make_repo, sri_of, tar_of

from heterodyne.harness import fetch
from heterodyne.harness.resources import store_core as sc

BASE = "https://github.example.invalid/openai/codex/releases/download/rust-v0.160.0"
REGISTRY = "https://registry.example.invalid"
ASSET = "codex-package-x86_64-unknown-linux-musl.tar.gz"
SUMS = "codex-package_SHA256SUMS"
NPM_NAME = "@anthropic-ai/claude-code-linux-x64"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Response(io.BytesIO):
    status = 200


class Stalled:
    """A response that trickles a byte per read while the clock moves on."""

    status = 200

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def read(self, n: int = -1) -> bytes:
        self.clock.now += 5
        return b"x"

    def close(self) -> None:
        pass


class Server:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.asked: list[tuple[str, float]] = []
        self.special: dict[str, Callable[[], fetch.Response]] = {}

    def __call__(self, url: str, timeout: float) -> fetch.Response:
        self.asked.append((url, timeout))
        if url in self.special:
            return self.special[url]()
        if url not in self.files:
            raise OSError(404, "not found")
        return Response(self.files[url])


def release_files(artifact: bytes, *, listed: bool = True) -> dict[str, bytes]:
    other = hashlib.sha256(b"another package").hexdigest()
    lines = [f"{other}  codex-package-aarch64-unknown-linux-musl.tar.gz"]
    if listed:
        lines.append(f"{hashlib.sha256(artifact).hexdigest()}  {ASSET}")
    return {f"{BASE}/{ASSET}": artifact, f"{BASE}/{SUMS}": ("\n".join(lines) + "\n").encode()}


def npm_files(tarball: bytes, *, integrity: str | None = None, signed: bool = True,
              registry: str = REGISTRY) -> dict[str, bytes]:
    tarball_url = f"{registry}/{NPM_NAME}/-/claude-code-linux-x64-2.1.286.tgz"
    dist: dict[str, object] = {"tarball": tarball_url, "integrity": integrity or sri_of(tarball),
                               "shasum": hashlib.sha1(tarball).hexdigest()}  # noqa: S324 (npm's own field)
    if signed:
        dist["signatures"] = [{"keyid": "k", "sig": "s"}]
    doc = {"name": NPM_NAME, "version": "2.1.286", "dist": dist}
    return {f"{registry}/@anthropic-ai%2Fclaude-code-linux-x64/2.1.286": json.dumps(doc).encode(),
            tarball_url: tarball}


def fetcher(tmp_path: Path, server: Server, clock: Clock | None = None, **kw: object) -> fetch.Fetcher:
    return fetch.Fetcher(server, tmp_path / "cache", clock or Clock(), 60.0, npm_registry=REGISTRY,
                         **kw)  # type: ignore[arg-type] (login_dirs only)


def refused(call: Callable[[], object]) -> str:
    with pytest.raises(sc.Refused) as e:
        call()
    assert str(e.value) == e.value.constant
    return e.value.constant


def cache_files(tmp_path: Path) -> list[Path]:
    cache = tmp_path / "cache"
    return [p for p in cache.rglob("*") if p.is_file()] if cache.exists() else []


def test_a_release_asset_gets_its_line_from_sha256sums(tmp_path: Path) -> None:
    artifact = tar_of({"bin/codex": b"x"})
    a = fetcher(tmp_path, Server(release_files(artifact))).release(BASE, ASSET, SUMS)
    assert a.path.read_bytes() == artifact
    assert a.checksum == "sha256:" + hashlib.sha256(artifact).hexdigest()
    assert a.origin == f"{BASE}/{SUMS}"
    assert stat.S_IMODE(a.path.stat().st_mode) == 0o600
    assert a.path.parent == tmp_path / "cache"


def test_an_asset_missing_from_sha256sums_is_refused(tmp_path: Path) -> None:
    artifact = tar_of({"bin/codex": b"x"})
    f = fetcher(tmp_path, Server(release_files(artifact, listed=False)))
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == fetch.NOT_IN_SUMS
    assert cache_files(tmp_path) == []


def test_an_asset_that_differs_from_its_sha256sums_line_is_refused(tmp_path: Path) -> None:
    files = release_files(tar_of({"bin/codex": b"x"}))
    files[f"{BASE}/{ASSET}"] += b"\0"
    f = fetcher(tmp_path, Server(files))
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == sc.CHECKSUM_MISMATCH
    assert cache_files(tmp_path) == []


def test_a_release_url_that_is_not_https_or_an_asset_with_a_slash_is_refused(tmp_path: Path) -> None:
    f = fetcher(tmp_path, Server({}))
    assert refused(lambda: f.release(BASE.replace("https", "http"), ASSET, SUMS)) == fetch.BAD_URL
    assert refused(lambda: f.release(BASE, "../" + ASSET, SUMS)) == fetch.BAD_URL


def test_npm_uses_dist_integrity(tmp_path: Path) -> None:
    tarball = tar_of({"package/package.json": b"{}"})
    server = Server(npm_files(tarball))
    a = fetcher(tmp_path, server).npm(NPM_NAME, "2.1.286")
    assert a.path.read_bytes() == tarball
    assert a.checksum == sri_of(tarball)
    assert a.origin.startswith(REGISTRY)
    wrong = Server(npm_files(tarball, integrity=sri_of(b"another tarball")))
    assert refused(lambda: fetcher(tmp_path / "w", wrong).npm(NPM_NAME, "2.1.286")) == sc.CHECKSUM_MISMATCH
    sha1_only = Server(npm_files(tarball, integrity="sha1-" + "A" * 28))
    assert refused(lambda: fetcher(tmp_path / "s", sha1_only).npm(NPM_NAME, "2.1.286")) == fetch.BAD_DOCUMENT


def test_an_npm_document_for_another_package_or_version_is_refused(tmp_path: Path) -> None:
    tarball = tar_of({"package/package.json": b"{}"})
    files = npm_files(tarball)
    key = next(k for k in files if k.endswith("/2.1.286"))
    doc = json.loads(files[key])
    doc["version"] = "2.1.287"
    files[key] = json.dumps(doc).encode()
    assert refused(lambda: fetcher(tmp_path, Server(files)).npm(NPM_NAME, "2.1.286")) == fetch.BAD_DOCUMENT
    assert refused(lambda: fetcher(tmp_path, Server(files)).npm("../x", "2.1.286")) == fetch.BAD_URL


def test_a_deadline_ends_a_stalled_fetch(tmp_path: Path) -> None:
    clock = Clock()
    artifact = tar_of({"bin/codex": b"x"})
    server = Server(release_files(artifact))
    server.special[f"{BASE}/{ASSET}"] = lambda: Stalled(clock)
    f = fetcher(tmp_path, server, clock)
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == fetch.DEADLINE
    assert all(0 < timeout <= 60.0 for _, timeout in server.asked)
    assert cache_files(tmp_path) == []


def test_an_artifact_over_the_size_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "MAX_ARTIFACT", 10)
    artifact = tar_of({"bin/codex": b"x"})
    f = fetcher(tmp_path, Server(release_files(artifact)))
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == fetch.TOO_LARGE
    assert cache_files(tmp_path) == []


def test_a_failed_request_is_refused(tmp_path: Path) -> None:
    f = fetcher(tmp_path, Server({}))
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == fetch.FETCH_FAILED


def test_a_git_source_must_be_a_full_commit_id(tmp_path: Path) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    f = fetcher(tmp_path, Server({}))
    for bad in (commit[:12], commit.upper(), "HEAD", "main", commit + "0", ""):
        assert refused(lambda bad=bad: f.git(str(repo), bad)) == fetch.BAD_COMMIT
    g = f.git(str(repo), commit)
    assert g.commit == commit
    fds = [os.open(g.archive, os.O_RDONLY), os.open(g.commit_object, os.O_RDONLY)]
    try:
        sc.check_git(fds[0], fds[1], commit)
    finally:
        for fd in fds:
            os.close(fd)
    assert stat.S_IMODE(g.archive.stat().st_mode) == 0o600


def test_a_git_commit_the_repo_lacks_is_refused(tmp_path: Path) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    other = "0" * len(commit) if commit != "0" * len(commit) else "1" * len(commit)
    assert refused(lambda: fetcher(tmp_path, Server({})).git(str(repo), other)) == fetch.GIT_FAILED


def test_a_local_git_source_inside_a_login_dir_is_refused(tmp_path: Path) -> None:
    login = tmp_path / "home" / ".codex"
    repo, commit = make_repo(login / "skills" / "repo")
    f = fetcher(tmp_path, Server({}), login_dirs=(login,))
    assert refused(lambda: f.git(str(repo), commit)) == fetch.INSIDE_LOGIN_DIR
    (tmp_path / "alias").symlink_to(login / "skills")
    assert refused(lambda: f.git(str(tmp_path / "alias" / "repo"), commit)) == fetch.INSIDE_LOGIN_DIR
    assert refused(lambda: f.git("relative/repo", commit)) == fetch.BAD_URL
    assert refused(lambda: f.git("ssh://host/repo", commit)) == fetch.BAD_URL


def test_the_git_cache_runs_no_hook_from_the_source(tmp_path: Path) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    marker = tmp_path / "hook-ran"
    for hook in ("post-checkout", "post-merge", "reference-transaction", "pre-auto-gc"):
        path = repo / ".git" / "hooks" / hook
        path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        path.chmod(0o755)
    fetcher(tmp_path, Server({})).git(str(repo), commit)
    assert not os.path.lexists(marker)


def test_npm_signature_availability_is_kept_and_a_release_has_none(tmp_path: Path) -> None:
    tarball = tar_of({"package/package.json": b"{}"})
    assert fetcher(tmp_path / "s", Server(npm_files(tarball))).npm(NPM_NAME, "2.1.286").signature_available
    unsigned = fetcher(tmp_path / "u", Server(npm_files(tarball, signed=False))).npm(NPM_NAME, "2.1.286")
    assert unsigned.signature_available is False
    artifact = tar_of({"bin/codex": b"x"})
    assert fetcher(tmp_path / "r", Server(release_files(artifact))).release(BASE, ASSET, SUMS) \
        .signature_available is False


class Moved(Response):
    url = "http://github.example.invalid/elsewhere"


def test_a_response_whose_last_hop_is_not_https_is_refused(tmp_path: Path) -> None:
    artifact = tar_of({"bin/codex": b"x"})
    server = Server(release_files(artifact))
    server.special[f"{BASE}/{SUMS}"] = lambda: Moved(release_files(artifact)[f"{BASE}/{SUMS}"])
    f = fetcher(tmp_path, server)
    assert refused(lambda: f.release(BASE, ASSET, SUMS)) == fetch.INSECURE_REDIRECT
    assert cache_files(tmp_path) == []


# --- HttpsOpener against a loopback server ---

Route = Callable[[http.server.BaseHTTPRequestHandler], None]


@contextlib.contextmanager
def loopback(routes: dict[str, Route]) -> Iterator[tuple[str, threading.Event]]:
    """A plain-HTTP server on 127.0.0.1 serving `routes` by path; the base URL is `https://` because the
    tests' `HttpsOpener` reaches it with a plain connection. The event ends any trickle."""
    stop = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            with contextlib.suppress(OSError):
                route(self)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (http.server's name)
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_address[1]}", stop
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def body(data: bytes) -> Route:
    def route(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(200)
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    return route


def redirect(location: str) -> Route:
    def route(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()
    return route


def opener() -> fetch.HttpsOpener:
    return fetch.HttpsOpener(connection=http.client.HTTPConnection)


def real_fetcher(tmp_path: Path, registry: str, deadline: float = 30.0) -> fetch.Fetcher:
    return fetch.Fetcher(opener(), tmp_path / "cache", time.monotonic, deadline, npm_registry=registry)


def test_https_opener_follows_a_redirect_that_stays_on_https(tmp_path: Path) -> None:
    artifact = tar_of({"bin/codex": b"x"})
    files = release_files(artifact)
    routes: dict[str, Route] = {}
    with loopback(routes) as (base, _):
        routes["/rel/" + SUMS] = redirect(f"{base}/moved/{SUMS}")
        routes["/moved/" + SUMS] = body(files[f"{BASE}/{SUMS}"])
        routes["/rel/" + ASSET] = body(artifact)
        a = real_fetcher(tmp_path, base).release(f"{base}/rel", ASSET, SUMS)
    assert a.path.read_bytes() == artifact


@pytest.mark.parametrize("which", ["sums", "npm-document"])
def test_a_metadata_redirect_off_https_is_refused(tmp_path: Path, which: str) -> None:
    artifact = tar_of({"bin/codex": b"x"})
    routes: dict[str, Route] = {}
    with loopback(routes) as (base, _):
        plain = base.replace("https://", "http://", 1)
        if which == "sums":
            routes["/rel/" + SUMS] = redirect(f"{plain}/evil/{SUMS}")
            routes["/evil/" + SUMS] = body(release_files(artifact)[f"{BASE}/{SUMS}"])
            routes["/rel/" + ASSET] = body(artifact)
            def call() -> object:
                return real_fetcher(tmp_path, base).release(f"{base}/rel", ASSET, SUMS)
        else:
            files = npm_files(artifact, registry=base)
            doc = next(k for k in files if k.endswith("/2.1.286"))
            path = doc.removeprefix(base)
            routes[path] = redirect(f"{plain}/evil")
            routes["/evil"] = body(files[doc])
            def call() -> object:
                return real_fetcher(tmp_path, base).npm(NPM_NAME, "2.1.286")
        assert refused(call) == fetch.INSECURE_REDIRECT
    assert cache_files(tmp_path) == []


def test_a_trickle_is_ended_by_the_real_deadline(tmp_path: Path) -> None:
    artifact = tar_of({"bin/codex": b"x"})

    def trickle_route(stop: threading.Event) -> Route:
        def route(h: http.server.BaseHTTPRequestHandler) -> None:
            h.send_response(200)
            h.send_header("Content-Length", str(1 << 20))
            h.end_headers()
            while not stop.is_set():                    # a byte far inside every socket timeout
                h.wfile.write(b"x")
                h.wfile.flush()
                stop.wait(0.05)
        return route

    routes: dict[str, Route] = {}
    with loopback(routes) as (base, stop):
        routes["/rel/" + SUMS] = body(release_files(artifact)[f"{BASE}/{SUMS}"])
        routes["/rel/" + ASSET] = trickle_route(stop)
        f = real_fetcher(tmp_path, base, deadline=2.0)
        start = time.monotonic()
        assert refused(lambda: f.release(f"{base}/rel", ASSET, SUMS)) == fetch.DEADLINE
        assert time.monotonic() - start < 30            # unbounded without the watchdog; slack for load
    assert cache_files(tmp_path) == []


def test_closing_a_response_closes_the_socket_a_closing_connection_handed_it(tmp_path: Path) -> None:
    def missing(h: http.server.BaseHTTPRequestHandler) -> None:
        h.send_response(404)
        h.send_header("Content-Length", "0")
        h.end_headers()

    with loopback({"/missing": missing}) as (base, _):
        resp = opener()(f"{base}/missing", timeout=30.0)
        sock = resp._sock  # pyright: ignore[reportPrivateUsage]
        assert resp.status == 404 and sock is not None and sock.fileno() >= 0
        resp.close()
        assert sock.fileno() == -1


def test_git_archive_output_is_bounded_as_it_streams_and_git_is_killed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, commit = make_repo(tmp_path / "repo")
    pidfile = tmp_path / "archive.pid"
    git = tmp_path / "git-endless-archive"
    git.write_text(f"""#!/bin/sh
case " $* " in
  *" archive "*) echo $$ > '{pidfile}'; exec yes x ;;
  *) exec git "$@" ;;
esac
""")
    git.chmod(0o755)
    monkeypatch.setattr(fetch, "MAX_ARTIFACT", 1 << 16)
    f = fetcher(tmp_path, Server({}), git=str(git))
    assert refused(lambda: f.git(str(repo), commit)) == fetch.TOO_LARGE
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):         # killed and reaped, not left writing
        os.kill(pid, 0)
    assert [p for p in cache_files(tmp_path) if p.name.startswith("git-")] == []
