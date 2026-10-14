"""Plan 4c Task 4: the store clients (plan 4c E3, E21). One contract suite runs against `HelperStore`, talking
to `serve` threads on a Unix socket in `short_dir()`, and against `LocalStore` on a temp store: both run
`store_core`, so both must answer alike."""

import contextlib
import dataclasses
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from harness_env import (
    CODEX_LAYOUT,
    FIRST_FILE,
    LOGIN_NAMES,
    artifact_fds,
    codex_part,
    install_root,
    rewrite,
    sample_manifest,
    store_dir,
    unseal,
)
from sandbox_env import short_dir

from heterodyne.harness import store as hs
from heterodyne.harness.resources import store_core as sc

KINDS = ["helper", "local"]
TARGET = "x86_64-unknown-linux-musl"


@contextlib.contextmanager
def helper_server(cfg: sc.HelperConfig) -> Iterator[Path]:
    """A Unix socket whose every connection is served by `serve` in its own thread, as `Accept=yes` gives
    each connection its own helper process."""
    with short_dir() as d:
        path = d / "store.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(8)
        stop = threading.Event()
        workers: list[threading.Thread] = []

        def handle(conn: socket.socket) -> None:
            with conn:
                sc.serve(conn, cfg, time.time)

        def accept() -> None:
            while True:
                conn, _ = listener.accept()
                if stop.is_set():
                    conn.close()
                    return
                worker = threading.Thread(target=handle, args=(conn,))
                workers.append(worker)
                worker.start()

        acceptor = threading.Thread(target=accept)
        acceptor.start()
        try:
            yield path
        finally:
            stop.set()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
                wake.connect(str(path))
            acceptor.join(timeout=120)
            for worker in workers:
                worker.join(timeout=120)
            listener.close()


@contextlib.contextmanager
def open_kind(kind: str, root: Path, *, lock_seconds: float = 30.0) -> Iterator[hs.Store]:
    if kind == "local":
        yield hs.LocalStore(root, lock_seconds=lock_seconds)
        return
    cfg = sc.HelperConfig(service_uid=os.getuid(), store=root, lock_seconds=lock_seconds)
    with helper_server(cfg) as path:
        yield hs.HelperStore(path, root, timeout=120)


@pytest.fixture(autouse=True)
def writable_store(tmp_path: Path) -> Iterator[None]:
    yield
    unseal(tmp_path / "store")


def stage(store: hs.Store, tmp_path: Path) -> sc.Staged:
    part, data = codex_part()
    with artifact_fds(tmp_path / "artifacts", [data]) as fds:
        return store.stage("codex", CODEX_LAYOUT, [part], fds, LOGIN_NAMES, verity=False, target=TARGET)


def refused(fn: object) -> str:
    assert callable(fn)
    with pytest.raises(hs.StoreRefused) as e:
        fn()
    assert str(e.value) == e.value.constant
    return e.value.constant


@pytest.mark.parametrize("kind", KINDS)
def test_stage_publish_state_and_list(tmp_path: Path, kind: str) -> None:
    root = store_dir(tmp_path)
    with open_kind(kind, root) as store:
        assert store.root == root
        assert store.fallback is (kind == "local")
        staged = stage(store, tmp_path)
        m = store.staged_manifest(staged.id)
        assert sc.digest(m) == staged.digest
        assert store.outcome(staged.id) == "staged"
        name = store.publish(staged.id, staged.digest)
        assert name == sc.install_name(m.version, staged.digest)
        assert store.state("codex", name) == "published"
        assert store.outcome(staged.id) == f"published:{name}"
        assert store.publish(staged.id, staged.digest) == name
        assert store.list() == [hs.InstallInfo(name, "codex", m.version, staged.digest, staged.entries,
                                               staged.size, "published")]
        assert store.recover() == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_refusal_carries_the_store_constant(tmp_path: Path, kind: str) -> None:
    with open_kind(kind, store_dir(tmp_path)) as store:
        staged = stage(store, tmp_path)
        assert refused(lambda: store.publish(staged.id, sc.digest(sample_manifest()))) == sc.DIGEST_MISMATCH
        assert refused(lambda: store.staged_manifest("0" * 32)) == sc.NO_SUCH_STAGING
        assert refused(lambda: store.staged_manifest("../x")) == sc.NO_SUCH_STAGING
        assert refused(lambda: store.state("codex", "0.160.0-000000000000")) == sc.NO_SUCH_INSTALL


@pytest.mark.parametrize("kind", KINDS)
def test_verify_and_remove(tmp_path: Path, kind: str) -> None:
    with open_kind(kind, store_dir(tmp_path)) as store:
        staged = stage(store, tmp_path)
        name = store.publish(staged.id, staged.digest)
        store.verify("codex", name)
        path = install_root(tmp_path, name) / FIRST_FILE
        data = path.read_bytes()
        rewrite(path)
        assert refused(lambda: store.verify("codex", name)) == sc.CONTENT_CHANGED
        assert store.state("codex", name) == "broken"
        rewrite(path, data)
        store.verify("codex", name)
        assert store.state("codex", name) == "published"
        store.remove("codex", name)
        assert store.list() == []
        assert not os.path.lexists(install_root(tmp_path, name))


@pytest.mark.parametrize("kind", KINDS)
def test_the_lock_serializes_the_stores(tmp_path: Path, kind: str) -> None:
    root = store_dir(tmp_path)
    with open_kind(kind, root, lock_seconds=0.2) as store:
        staged = stage(store, tmp_path)
        with sc.store_lock(root):
            assert refused(lambda: store.publish(staged.id, staged.digest)) == sc.STORE_BUSY
            assert refused(lambda: store.list()) == sc.STORE_BUSY
        name = store.publish(staged.id, staged.digest)
    with open_kind("local" if kind == "helper" else "helper", root) as other:
        assert other.state("codex", name) == "published"


def test_an_unanswered_helper_is_a_refusal(tmp_path: Path) -> None:
    root = store_dir(tmp_path)
    with short_dir() as d:
        assert refused(lambda: hs.HelperStore(d / "absent.sock", root, timeout=5).list()) == sc.HELPER_FAILED
        path = d / "mute.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)

            def mute() -> None:
                conn, _ = listener.accept()
                conn.close()

            thread = threading.Thread(target=mute)
            thread.start()
            assert refused(lambda: hs.HelperStore(path, root, timeout=5).list()) == sc.HELPER_FAILED
            thread.join(timeout=120)


@dataclasses.dataclass(frozen=True)
class Settings:
    store: Path
    fallback: bool
    helper_socket: Path
    helper_timeout: float = 30.0


def test_open_store_takes_the_fallback_only_when_it_is_set(tmp_path: Path) -> None:
    root = store_dir(tmp_path)
    local = hs.open_store(Settings(root, True, tmp_path / "s.sock"))
    assert isinstance(local, hs.LocalStore) and local.fallback and local.root == root
    helper = hs.open_store(Settings(root, False, tmp_path / "s.sock"))
    assert isinstance(helper, hs.HelperStore) and not helper.fallback and helper.root == root
