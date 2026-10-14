"""Plan 4c Task 3: fetching artifacts and their checksums (plan 4c E5, E6). The opener is a fixture that
serves artifacts built in a temp dir; nothing here touches the network. Checksums are computed at run time
and compared as values."""

import hashlib
import io
import json
import os
import stat
from collections.abc import Callable
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


def npm_files(tarball: bytes, *, integrity: str | None = None) -> dict[str, bytes]:
    tarball_url = f"{REGISTRY}/{NPM_NAME}/-/claude-code-linux-x64-2.1.286.tgz"
    doc = {"name": NPM_NAME, "version": "2.1.286",
           "dist": {"tarball": tarball_url, "integrity": integrity or sri_of(tarball),
                    "shasum": hashlib.sha1(tarball).hexdigest(),  # noqa: S324 (npm's own field)
                    "signatures": [{"keyid": "k", "sig": "s"}]}}
    return {f"{REGISTRY}/@anthropic-ai%2Fclaude-code-linux-x64/2.1.286": json.dumps(doc).encode(),
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
