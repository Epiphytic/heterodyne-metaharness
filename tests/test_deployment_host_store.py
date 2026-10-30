"""Filesystem and kernel peer probes; namespace proof is not host installation."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import msgspec
import pytest
from test_deployment_step1 import TARGET, release

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_host_store import _installer_argv, _ProtectedStore, read_submission


@pytest.fixture
def store_root():
    with tempfile.TemporaryDirectory(prefix=".protected-store-", dir=Path.cwd()) as directory:
        yield Path(directory)


def test_actual_descriptor_staging_retained_readback_and_lock(store_root):
    raw = b"exact source bytes"
    r = release(artifacts=msgspec.structs.replace(
        release().artifacts, guard_sha256=hashlib.sha256(raw).hexdigest()
    ))
    source = store_root.parent / (store_root.name + ".input")
    source.write_bytes(raw)
    try:
        store = _ProtectedStore(store_root, os.geteuid() + 1)
        second = _ProtectedStore(store_root, os.geteuid() + 1)
        fd = os.open(source, os.O_RDONLY)
        try:
            os.lseek(fd, len(raw), os.SEEK_SET)
            name = store.stage(r, "guard", fd)
            assert (store_root / name).read_bytes() == raw
            assert (store_root / name).stat().st_mode & 0o777 == 0o400
            assert store.stage(r, "guard", fd) == name
            with store.target_lock(r), pytest.raises(InvalidAction, match="target-busy"):
                with second.target_lock(r):
                    pytest.fail("second holder admitted")
            with second.target_lock(r):
                pass
            (store_root / name).chmod(0o600)
            (store_root / name).write_bytes(b"tamper")
            (store_root / name).chmod(0o400)
            with pytest.raises(InvalidAction, match="staged-artifact-drift"):
                store.stage(r, "guard", fd)
        finally:
            os.close(fd)
            second.close()
            store.close()
    finally:
        source.unlink()


def test_private_store_rejects_links_modes_and_failed_copy(store_root):
    with pytest.raises(InvalidAction, match="separate-executor"):
        _ProtectedStore(store_root, os.geteuid())
    store_root.chmod(0o770)
    with pytest.raises(InvalidAction, match="ancestor"):
        _ProtectedStore(store_root, os.geteuid() + 1)
    store_root.chmod(0o700)
    store = _ProtectedStore(store_root, os.geteuid() + 1)
    try:
        source = store_root / "input"
        source.write_bytes(b"wrong hash")
        fd = os.open(source, os.O_RDONLY)
        try:
            with pytest.raises(InvalidAction, match="source-pin"):
                store.stage(release(), "guard", fd)
            assert not list(store_root.glob("guard-*"))
            assert list(store_root.glob(".partial-guard-*"))
            name = "guard-" + release().artifacts.guard_sha256
            (store_root / name).symlink_to(source)
            with pytest.raises(OSError):
                store.stage(release(), "guard", fd)
        finally:
            os.close(fd)
    finally:
        store.close()


def test_peer_refused_before_waiting_for_input():
    server, client = socket.socketpair()
    try:
        # No bytes or EOF supplied. The actual same-UID peer must be refused
        # before any blocking receive. This does not mock SO_PEERCRED.
        with pytest.raises(InvalidAction, match="peer-refused"):
            read_submission(server, os.geteuid() + 1, (TARGET,))
    finally:
        server.close()
        client.close()


def test_fixed_installer_composition_does_not_execute(store_root):
    r = release()
    argv = _installer_argv(r, store_root / "bootstrap", store_root / "checkout")
    assert argv[:5] == ("/usr/bin/sudo", "-n", "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin")
    script = argv[9]
    assert 'cat -- "$1" >"$b"' in script
    assert "--check --strict --quiet" in script
    assert script.index("--check") < script.index('bash "$b"')
    assert "--no-wsd --no-verity" in script
    assert "--fetch" not in script and "--adopt" not in script
    assert argv[-4:] == (
        str(store_root / "checkout"), r.artifacts.source_commit, r.artifacts.release_sha256, "service"
    )
    wrong = msgspec.structs.replace(
        r, artifacts=msgspec.structs.replace(r.artifacts, bootstrap_sha256="b" * 64)
    )
    with pytest.raises(InvalidAction, match="bootstrap-installer-mismatch"):
        _installer_argv(wrong, store_root / "bootstrap", store_root / "checkout")


_NAMESPACE_PROBE = r'''
import hashlib, json, os, shutil, socket, subprocess, tempfile
from pathlib import Path
import msgspec
from test_deployment_step1 import TARGET, release
from heterodyne.actions.codec import encode_record
from heterodyne.actions.deployment_host_store import _ProtectedStore, read_submission
subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", "/tmp"], check=True)
root = Path(tempfile.mkdtemp(prefix="host-store-namespace-", dir="/tmp"))
root.chmod(0o755)
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    state = root / "state"; state.mkdir(); os.chown(state, 1, 1); state.chmod(0o700)
    source = root / "source"; raw = b"namespace-staged-bytes"; source.write_bytes(raw); source.chmod(0o444)
    r = release(artifacts=msgspec.structs.replace(
        release().artifacts, guard_sha256=hashlib.sha256(raw).hexdigest()
    ))
    original = encode_record(r)
    sockpath = root / "socket"; listener.bind(str(sockpath)); sockpath.chmod(0o666); listener.listen(1)
    receive, send = os.pipe()
    server = os.fork()
    if server == 0:
        os.close(receive); os.setgroups([]); os.setgid(1); os.setuid(1)
        try:
            store = _ProtectedStore(state, 2)
            fd = os.open(source, os.O_RDONLY)
            name = store.stage(r, "guard", fd); os.close(fd)
            conn, _ = listener.accept()
            admitted = read_submission(conn, 2, (TARGET,)); conn.close()
            assert admitted == r
            assert (state / name).read_bytes() == raw
            store.close()
            os.write(send, json.dumps({"admitted": True, "executor_uid": os.geteuid()}).encode())
            os._exit(0)
        except BaseException:
            import traceback; traceback.print_exc(); os._exit(3)
    os.close(send)
    build = os.fork()
    if build == 0:
        os.setgroups([]); os.setgid(2); os.setuid(2)
        try:
            try: (state / "forged").write_bytes(b"untrusted")
            except PermissionError: pass
            else: os._exit(4)
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); conn.connect(str(sockpath))
            conn.sendall(original); conn.shutdown(socket.SHUT_WR); conn.close(); os._exit(0)
        except BaseException:
            import traceback; traceback.print_exc(); os._exit(5)
    result = json.loads(os.read(receive, 4096)); os.close(receive)
    assert os.waitpid(server, 0)[1] == 0 and os.waitpid(build, 0)[1] == 0
    result.update({"schema": "deployment-host-userns-probe.v1", "production_custody": False})
    print(json.dumps(result))
finally:
    listener.close(); shutil.rmtree(root)
'''


def test_actual_separate_uid_staging_and_peer_ingress_in_disposable_namespace():
    result = subprocess.run(
        ["unshare", "--user", "--map-auto", "--map-root-user", "--mount",
         "--propagation", "private", sys.executable, "-c", _NAMESPACE_PROBE],
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src") + os.pathsep + str(Path.cwd() / "tests")},
        capture_output=True, timeout=15, check=False,
    )
    # Unsupported environments do not become a positive custody claim.
    if result.returncode and b"unshare: " in result.stderr:
        pytest.skip("disposable mapped user namespace unavailable")
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {
        "schema": "deployment-host-userns-probe.v1", "admitted": True,
        "executor_uid": 1, "production_custody": False,
    }
