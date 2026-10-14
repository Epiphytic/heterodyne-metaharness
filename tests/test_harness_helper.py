"""Plan 4c Task 4: the store helper's socket protocol (plan 4c E1, E3). `serve` runs in a thread over a
`socket.socketpair()` in this process, with an injected peer check; `main` runs as root would run it,
`python3 -I store_core.py`, on one end of a socketpair. No test needs a second uid."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from harness_env import (
    CODEX_LAYOUT,
    CODEX_TREE,
    EXECUTABLE,
    LOGIN_NAMES,
    Spec,
    artifact_fds,
    codex_part,
    fd_count,
    stage,
    staged_manifest,
    staged_root,
    store_dir,
    unseal,
)

from heterodyne.harness.resources import store_core as sc

STORE_CORE = Path(sc.__file__)
OTHER_UID = os.getuid() + 1


@pytest.fixture(autouse=True)
def writable_store(tmp_path: Path) -> Iterator[None]:
    yield
    unseal(tmp_path / "store")


def config(tmp_path: Path) -> sc.HelperConfig:
    return sc.HelperConfig(service_uid=os.getuid(), store=store_dir(tmp_path))


def read_reply(sock: socket.socket) -> dict[str, object]:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1 << 16)
        assert chunk, "the helper closed the connection without a reply"
        data += chunk
    reply = json.loads(data)
    assert isinstance(reply, dict)
    return reply


def serve(cfg: sc.HelperConfig, request: bytes, fds: Sequence[int] = (), *, uid: int | None = None,
          clock: Callable[[], float] = time.time,
          after: Callable[[socket.socket], None] | None = None) -> dict[str, object]:
    """`request` with `fds` sent to a `serve` thread whose peer check returns `uid` (the service uid by
    default); its reply. `after` sees the helper's end once `serve` has returned."""
    client, server = socket.socketpair()
    peer = cfg.service_uid if uid is None else uid
    errors: list[BaseException] = []

    def run() -> None:
        try:
            sc.serve(server, cfg, clock, peer_uid=lambda _: peer)
        except BaseException as exc:            # surfaced below: a failure in the thread fails the test
            errors.append(exc)

    thread = threading.Thread(target=run)
    try:
        thread.start()
        socket.send_fds(client, [request], list(fds))
        reply = read_reply(client)
        thread.join(timeout=120)
        assert not thread.is_alive()
        assert errors == []
        if after is not None:
            after(server)
        return reply
    finally:
        client.close()
        server.close()


def stage_request(cfg: sc.HelperConfig, tree: dict[str, Spec] | None = None) -> tuple[bytes, bytes]:
    part, data = codex_part(tree)
    args = sc.stage_args("codex", CODEX_LAYOUT, [part], LOGIN_NAMES, verity=False,
                         target="x86_64-unknown-linux-musl")
    return sc.encode_request("stage", cfg.store, args), data


def test_a_peer_with_another_uid_gets_peer_refused_and_nothing_is_read(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    request, data = stage_request(cfg)
    pending: list[bytes] = []
    with artifact_fds(tmp_path / "artifacts", [data]) as fds:
        reply = serve(cfg, request, fds, uid=OTHER_UID,
                      after=lambda s: pending.append(s.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)))
    assert reply == {"ok": False, "refused": sc.PEER_REFUSED}
    assert pending == [request[:1]]                 # the request, and its fd, are still unread
    assert not os.path.lexists(tmp_path / "store" / ".staging")


def test_stage_over_the_socket_takes_artifact_fds_and_returns_the_digest(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    request, data = stage_request(cfg)
    with artifact_fds(tmp_path / "artifacts", [data]) as fds:
        before = fd_count()
        reply = serve(cfg, request, fds)
        assert fd_count() == before
    assert reply["ok"] is True
    result = reply["result"]
    assert isinstance(result, dict)
    staged = sc.Staged(**result)
    assert staged.digest == sc.digest(staged_manifest(tmp_path, staged))
    publish = serve(cfg, sc.encode_request("publish", cfg.store, {"id": staged.id, "digest": staged.digest}))
    assert publish["ok"] is True
    listed = serve(cfg, sc.encode_request("list", cfg.store, {}))
    assert listed["ok"] is True
    installs = listed["result"]
    assert isinstance(installs, list)
    assert [i["state"] for i in installs] == ["published"]


def test_a_refusal_comes_back_as_its_constant_alone(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    reply = serve(cfg, sc.encode_request("publish", cfg.store, {"id": "0" * 32, "digest": "sha256:"}))
    assert reply == {"ok": False, "refused": sc.NO_SUCH_STAGING}
    assert serve(cfg, b'{"op": "nope"}\n') == {"ok": False, "refused": sc.BAD_REQUEST}
    assert serve(cfg, b"not json\n") == {"ok": False, "refused": sc.BAD_REQUEST}
    with artifact_fds(tmp_path / "artifacts", [b"x"]) as fds:
        assert serve(cfg, sc.encode_request("list", cfg.store, {}), fds) == {"ok": False,
                                                                            "refused": sc.BAD_REQUEST}


@pytest.mark.parametrize(("op", "args"), [("list", {"extra": 1}), ("publish", {"id": "0" * 32})])
def test_a_malformed_request_is_refused_before_recovery_or_the_sweep_changes_anything(
        tmp_path: Path, op: str, args: dict[str, object]) -> None:
    cfg = config(tmp_path)
    staged = stage(tmp_path, CODEX_LAYOUT, [codex_part()])
    expired = time.time() + 1000 * 3600 * cfg.staging_hours
    reply = serve(cfg, sc.encode_request(op, cfg.store, args), clock=lambda: expired)
    assert reply == {"ok": False, "refused": sc.BAD_REQUEST}
    assert staged_root(tmp_path, staged).is_dir()
    assert serve(cfg, sc.encode_request("list", cfg.store, {}), clock=lambda: expired) == {"ok": True,
                                                                                         "result": []}
    assert not staged_root(tmp_path, staged).exists()        # the sweep would have removed it


def test_a_request_for_another_store_is_refused(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    reply = serve(cfg, sc.encode_request("list", tmp_path / "elsewhere", {}))
    assert reply == {"ok": False, "refused": sc.WRONG_STORE}


def test_a_request_over_the_size_limit_or_with_too_many_fds_is_refused(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    client, server = socket.socketpair()
    replies: list[dict[str, object]] = []

    def run() -> None:
        sc.serve(server, cfg, time.time, peer_uid=lambda _: cfg.service_uid)

    thread = threading.Thread(target=run)
    sender_errors: list[BaseException] = []

    def send() -> None:
        try:
            client.sendall(b"x" * (sc.MAX_REQUEST + 1))
        except (BrokenPipeError, ConnectionResetError) as exc:    # the helper stopped reading: expected
            sender_errors.append(exc)

    sender = threading.Thread(target=send)
    try:
        thread.start()
        sender.start()
        replies.append(read_reply(client))
        thread.join(timeout=120)
        server.close()
        sender.join(timeout=120)
    finally:
        client.close()
        server.close()
    assert replies == [{"ok": False, "refused": sc.REQUEST_TOO_LARGE}]
    assert not thread.is_alive() and not sender.is_alive()

    with artifact_fds(tmp_path / "artifacts", [b"x"] * (sc.MAX_FDS + 1)) as fds:
        before = fd_count()
        reply = serve(cfg, sc.encode_request("list", cfg.store, {}), fds)
        assert fd_count() == before                # the fds it did receive are closed
    assert reply == {"ok": False, "refused": sc.TOO_MANY_FDS}


EXEC_EVENTS = frozenset({"subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.system",
                         "os.fork", "os.forkpty"})
_watched: set[int] = set()
_seen: list[str] = []
_hooked = False


def _audit(event: str, _args: tuple[object, ...]) -> None:
    if event in EXEC_EVENTS and threading.get_ident() in _watched:
        _seen.append(event)
        raise RuntimeError("the helper tried to run something")


def test_the_helper_never_executes_from_an_artifact(tmp_path: Path) -> None:
    global _hooked
    if not _hooked:                                 # an audit hook can't be removed: it acts only on _watched
        sys.addaudithook(_audit)
        _hooked = True
    cfg = config(tmp_path)
    tree = dict(CODEX_TREE)
    for tool in ("git", "tar", "gzip", "python3", "sh", "systemctl"):
        tree[f"bin/{tool}"] = EXECUTABLE + b"exit 1\n"
    request, data = stage_request(cfg, tree)
    client, server = socket.socketpair()
    results: list[BaseException | None] = []

    def run() -> None:
        _watched.add(threading.get_ident())
        try:
            sc.serve(server, cfg, time.time, peer_uid=lambda _: cfg.service_uid)
            results.append(None)
        except BaseException as exc:
            results.append(exc)
        finally:
            _watched.discard(threading.get_ident())

    thread = threading.Thread(target=run)
    try:
        with artifact_fds(tmp_path / "artifacts", [data]) as fds:
            thread.start()
            socket.send_fds(client, [request], fds)
            reply = read_reply(client)
            thread.join(timeout=120)
    finally:
        client.close()
        server.close()
    assert results == [None]
    assert _seen == []
    assert reply["ok"] is True


def run_main(cfg_path: Path, request: bytes) -> tuple[int, dict[str, object] | None]:
    client, server = socket.socketpair()
    try:
        proc = subprocess.Popen([sys.executable, "-I", str(STORE_CORE), "--config", str(cfg_path)],
                                stdin=server.fileno(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        server.close()
        client.sendall(request)
        reply: dict[str, object] | None = None
        data = b""
        while chunk := client.recv(1 << 16):
            data += chunk
        if data:
            reply = json.loads(data)
        _, err = proc.communicate(timeout=120)
        assert b"Traceback" not in err, err.decode(errors="replace")[-2000:]
        return proc.returncode, reply
    finally:
        client.close()
        server.close()


def test_main_reads_its_config_and_refuses_a_store_outside_it(tmp_path: Path) -> None:
    store = store_dir(tmp_path)
    cfg_path = tmp_path / "store-helper.json"
    cfg_path.write_text(json.dumps({"service_uid": os.getuid(), "store": str(store)}))
    assert run_main(cfg_path, sc.encode_request("list", store, {})) == (0, {"ok": True, "result": []})
    assert run_main(cfg_path, sc.encode_request("list", tmp_path / "other", {})) == (
        0, {"ok": False, "refused": sc.WRONG_STORE})

    # the real SO_PEERCRED: a config that trusts another uid refuses this one
    cfg_path.write_text(json.dumps({"service_uid": OTHER_UID, "store": str(store)}))
    assert run_main(cfg_path, sc.encode_request("list", store, {})) == (0, {"ok": False,
                                                                           "refused": sc.PEER_REFUSED})

    # a config that can't be read as one serves nothing
    for bad in ({"service_uid": "x", "store": str(store)}, {"service_uid": 1, "store": "relative"},
                {"service_uid": 1, "store": str(store), "extra": 1}):
        cfg_path.write_text(json.dumps(bad))
        code, reply = run_main(cfg_path, sc.encode_request("list", store, {}))
        assert (code, reply) == (sc.CONFIG_EXIT, None)
    with pytest.raises(sc.Refused) as e:
        sc.load_config(tmp_path / "missing.json")
    assert e.value.constant == sc.BAD_CONFIG
