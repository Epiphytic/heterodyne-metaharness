"""Real child pidfd and directory I/O; manager/cgroup fixture is synthetic.

These are source tests of retained invocation binding, not a systemd or host
writer-exclusion qualification. They never contact or stop a host service.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from heterodyne.actions import deployment_cessation as cessation
from heterodyne.actions.codec import InvalidAction


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    scratch = tempfile.TemporaryDirectory(prefix=".cessation-", dir=Path.cwd())
    tmp_path = Path(scratch.name)
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(10)"])
    fields = {
        "Id": "heterodyne-admind.service", "LoadState": "loaded", "ActiveState": "active",
        "SubState": "running", "MainPID": str(child.pid), "ExecMainPID": str(child.pid),
        "ExecMainStartTimestampMonotonic": "10", "ExecMainExitTimestampMonotonic": "0",
        "InvocationID": "a" * 32,
        "ControlGroup": (
            "/user.slice/user-1001.slice/"
            "user@1001.service/app.slice/heterodyne-admind.service"  # install-agnostic: allow=email
        ),
        "Result": "success", "ExecMainCode": "0", "ExecMainStatus": "0",
    }
    (tmp_path / "cgroup.events").write_text("populated 0\nfrozen 0\n")
    monkeypatch.setattr(cessation._AdmindInvocation, "_show", lambda self: dict(fields))
    monkeypatch.setattr(cessation._AdmindInvocation, "_capture_group", lambda self: os.open(
        tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
    ))
    scope = cessation._ManagerScope("service", 1001, "a" * 64, "b" * 64, "c" * 64, tmp_path / "live.db")
    held = cessation._AdmindInvocation(scope)
    try:
        yield held, child, fields, tmp_path
    finally:
        held.close()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
        scratch.cleanup()


def stopped(fields):
    fields.update({
        "ActiveState": "failed", "SubState": "failed", "MainPID": "0",
        "ExecMainExitTimestampMonotonic": "20", "InvocationID": "", "ControlGroup": "",
        "Result": "timeout", "ExecMainCode": "2", "ExecMainStatus": "9",
    })


def test_forced_reaped_empty_invocation_records_cessation_without_settlement(invocation):
    held, child, fields, _ = invocation
    child.kill()
    child.wait(timeout=2)
    stopped(fields)
    proof = held.observe()
    assert proof.outcome == "forced" and proof.exit_status == 9
    assert proof.invocation_id == "a" * 32 and proof.pid == child.pid
    assert not hasattr(proof, "settle") and not hasattr(proof, "release_target")


def test_claimed_stopped_manager_cannot_hide_actual_live_pid(invocation):
    held, _, fields, _ = invocation
    stopped(fields)
    with pytest.raises(InvalidAction, match="original-process-live"):
        held.observe()


@pytest.mark.parametrize("change", ["invocation", "start", "populated"])
def test_replacement_or_remaining_cgroup_tasks_refuse(invocation, change):
    held, child, fields, path = invocation
    child.kill()
    child.wait(timeout=2)
    stopped(fields)
    if change == "invocation":
        fields["InvocationID"] = "b" * 32
    elif change == "start":
        fields["ExecMainStartTimestampMonotonic"] = "11"
    else:
        (path / "cgroup.events").write_text("populated 1\n")
    with pytest.raises(InvalidAction, match="invocation-changed|cgroup-populated"):
        held.observe()


def test_missing_current_events_is_not_proof_original_cgroup_removed(invocation):
    held, child, fields, path = invocation
    child.kill()
    child.wait(timeout=2)
    stopped(fields)
    (path / "cgroup.events").unlink()
    with pytest.raises(InvalidAction, match="observation-unavailable"):
        held.observe()


def test_stopped_snapshot_includes_latest_committed_wal_without_restore(invocation, monkeypatch):
    held, child, fields, path = invocation
    # Synthetic custody profile only; actual SQLite WAL and retained pidfd are used.
    held.scope = cessation._ManagerScope(
        "service", os.getuid(), "a" * 64, "b" * 64, "c" * 64, path / "live.db",
    )
    monkeypatch.setattr("heterodyne.wsd.backup_copy.MIN_FREE", 0)
    live = sqlite3.connect(path / "live.db")
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA user_version=17")
        live.execute("CREATE TABLE newest(value TEXT)")
        live.execute("INSERT INTO newest VALUES ('current committed state')")
        live.commit()
        child.kill()
        child.wait(timeout=2)
        stopped(fields)
        target = path / "stopped.db"
        observed = held.stopped_snapshot(target)
        with sqlite3.connect(target) as saved:
            assert saved.execute("SELECT value FROM newest").fetchall() == [("current committed state",)]
        assert observed.schema == 17 and observed.cessation.outcome == "forced"
        assert observed.snapshot_bytes > 0
        assert observed.snapshot_sha256 == cessation.stream_hash(target)
        with pytest.raises(InvalidAction, match="snapshot-unavailable"):
            held.stopped_snapshot(target)
    finally:
        live.close()


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), KeyError("service"), subprocess.TimeoutExpired("show", 5)],
)
def test_capture_unavailable_is_typed_hold(monkeypatch, tmp_path, error):
    def fail(self):
        raise error
    monkeypatch.setattr(cessation._AdmindInvocation, "_show", fail)
    scope = cessation._ManagerScope("missing", 1001, "a" * 64, "b" * 64, "c" * 64, tmp_path / "absent")
    with pytest.raises(InvalidAction, match="capture-unavailable"):
        cessation._AdmindInvocation(scope)


@pytest.mark.parametrize("code,status", [(1, 0), (1, 42), (2, 15)])
def test_manager_success_classifies_configured_exit_or_sigterm_clean(invocation, code, status):
    held, child, fields, _ = invocation
    child.kill()
    child.wait(timeout=2)
    stopped(fields)
    fields.update(Result="success", ExecMainCode=str(code), ExecMainStatus=str(status))
    assert held.observe().outcome == "clean"


def test_snapshot_missing_source_and_partial_failure_leave_no_destination(invocation, monkeypatch):
    held, _, _, path = invocation
    proof = cessation.UnitCessation("unit", "boot", "a" * 32, 1, 1, 2, "/group", "success", 1, 0, "clean")
    monkeypatch.setattr(held, "observe", lambda: proof)
    with pytest.raises(InvalidAction, match="snapshot-unavailable"):
        held.stopped_snapshot(path / "saved.db")
    assert not (path / "saved.db").exists()
    source = path / "live.db"
    source.write_bytes(b"fixture source")
    held.scope = cessation._ManagerScope("service", os.getuid(), "a" * 64, "b" * 64, "c" * 64, source)
    def fail(source, destination, cancel):
        destination.write_bytes(b"partial snapshot")
        raise sqlite3.DatabaseError("synthetic corruption")
    monkeypatch.setattr(cessation, "wal_snapshot", fail)
    with pytest.raises(InvalidAction, match="snapshot-unavailable"):
        held.stopped_snapshot(path / "saved.db")
    assert not (path / "saved.db").exists() and not list(path.glob(".stopped-*"))
    def copied(source, destination, cancel):
        destination.write_bytes(b"complete but invalidated")
        return {"sha256": "a" * 64, "bytes": 24, "schema": 1}
    monkeypatch.setattr(cessation, "wal_snapshot", copied)
    observations = iter([proof, InvalidAction("cessation-live-database-changed")])
    def invalidated():
        result = next(observations)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(held, "observe", invalidated)
    with pytest.raises(InvalidAction, match="database-changed"):
        held.stopped_snapshot(path / "saved.db")
    assert not (path / "saved.db").exists() and not list(path.glob(".stopped-*"))


def test_real_cgroup_populated_empty_and_removed_retained_descriptor():
    # Only the inspected descriptor observer runs as root, never Product imports
    # or host service operations. One unique disposable cgroup owns one harmless
    # fixture child, then both are removed. No existing task is reassigned.
    import inspect
    source = (
        "import os, pathlib, subprocess, signal, uuid, time, json\nfrom pathlib import Path\n"
        f"InvalidAction = ValueError\n_READ_FILE = {cessation._READ_FILE}\nclass Probe:\n"
        + inspect.getsource(cessation._AdmindInvocation._empty_group)
        + '''
p = pathlib.Path('/sys/fs/cgroup') / ('heterodyne-proof-' + uuid.uuid4().hex)
p.mkdir()
child = None
fd = -1
try:
    fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    observer = Probe(); observer.group_fd = fd; observer.before = {'ControlGroup': '/' + p.name}
    child = subprocess.Popen(['/usr/bin/python3', '-I', '-S', '-c', 'import time; time.sleep(30)'])
    (p / 'cgroup.procs').write_text(str(child.pid))
    try: observer._empty_group()
    except ValueError as error: assert 'populated' in str(error)
    else: raise AssertionError('live cgroup accepted')
    child.kill(); child.wait(timeout=3)
    deadline = time.monotonic() + 3
    while 'populated 0' not in (p / 'cgroup.events').read_text():
        assert time.monotonic() < deadline
        time.sleep(.01)
    observer._empty_group()
    p.rmdir()
    assert os.fstat(fd).st_nlink == 2
    observer._empty_group()
    print(json.dumps({'populated_refused': True, 'empty_passed': True,
                      'removed_retained_passed': True, 'removed_nlink': os.fstat(fd).st_nlink}))
finally:
    if child is not None and child.poll() is None: child.kill(); child.wait(timeout=3)
    if fd >= 0: os.close(fd)
    if p.exists(): p.rmdir()
'''
    )
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-S", "-c", source],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"removed_retained_passed": true' in result.stdout
    assert '"removed_nlink": 2' in result.stdout


@pytest.mark.parametrize("private_peer_allowed", [True, False])
def test_primary_private_socket_authenticated_before_systemctl(monkeypatch, tmp_path, private_peer_allowed):
    # Synthetic transport properties only: asserts the actual reader checks the
    # primary systemd/private endpoint, plus its possible fallback bus.
    import stat
    from types import SimpleNamespace
    monkeypatch.setattr(cessation.os, "geteuid", lambda: 0)
    monkeypatch.setattr(cessation.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=1001))
    monkeypatch.setattr(cessation, "stream_hash", lambda path: "a" * 64)
    info = os.stat_result((stat.S_IFSOCK | 0o600, 11, 12, 1, 1001, 1001, 0, 0, 0, 0))
    monkeypatch.setattr(cessation.Path, "stat", lambda self, **kwargs: info)
    connected = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, value):
            pass
        def connect(self, path):
            self.path = path
            connected.append(path)
    monkeypatch.setattr(cessation.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(cessation, "peer_credentials_checked", lambda conn: (
        1, 1001 if private_peer_allowed or not conn.path.endswith("systemd/private") else 7, 1,
    ))
    executions = []
    def show(*args, **kwargs):
        executions.append(args)
        return SimpleNamespace(returncode=0, stdout="".join(
            name + "=fixture\n" for name in cessation._PROPERTIES
        ).encode())
    monkeypatch.setattr(cessation.subprocess, "run", show)
    observer = object.__new__(cessation._AdmindInvocation)
    observer.scope = cessation._ManagerScope("service", 1001, "a" * 64, "a" * 64, "a" * 64, tmp_path)
    if private_peer_allowed:
        observer._show()
        assert connected == ["/run/user/1001/systemd/private", "/run/user/1001/bus"] * 2
        assert len(executions) == 1
    else:
        with pytest.raises(InvalidAction, match="manager-peer"):
            observer._show()
        assert connected == ["/run/user/1001/systemd/private"] and not executions


def test_success_result_with_core_dump_is_forced(invocation):
    held, child, fields, _ = invocation
    child.kill()
    child.wait(timeout=2)
    stopped(fields)
    fields.update(Result="success", ExecMainCode="3", ExecMainStatus="11")
    assert held.observe().outcome == "forced"


def test_namespace_root_snapshot_refuses_hostile_sidecars_and_destinations():
    # Actual namespace-root/subordinate-service UID and SQLite filesystem effects.
    # The cessation observation is synthetic; no host service or DB is touched.
    probe = r'''
import hashlib, json, os, sqlite3, subprocess
from pathlib import Path
from heterodyne.actions import deployment_cessation as c
from heterodyne.actions.codec import InvalidAction
from heterodyne.wsd import backup_copy
for mount in ('/tmp', '/run'):
    subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=755', 'tmpfs', mount], check=True)
source = Path('/run/source'); source.mkdir(mode=0o700); os.chown(source, 1, 1)
protected = Path('/run/protected'); protected.mkdir(mode=0o700)
ready_r, ready_w = os.pipe(); done_r, done_w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(ready_r); os.close(done_w); os.setgid(1); os.setuid(1)
    connection = sqlite3.connect(source / 'live.db')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA user_version=17')
    connection.execute('CREATE TABLE newest(value TEXT)')
    connection.execute("INSERT INTO newest VALUES ('newest WAL state')")
    connection.commit(); os.write(ready_w, b'R'); os.read(done_r, 1)
    connection.close(); os._exit(0)
os.close(ready_w); os.close(done_r)
try:
    assert os.read(ready_r, 1) == b'R'
    held = c._AdmindInvocation.__new__(c._AdmindInvocation)
    held.scope = c._ManagerScope('fixture', 1, 'a'*64, 'b'*64, 'c'*64, source/'live.db')
    proof = c.UnitCessation('fixture', 'boot', 'a'*32, pid, 1, 2, '/fixture', 'timeout', 2, 9, 'forced')
    held.observe = lambda: proof
    backup_copy.MIN_FREE = 0
    sentinel = protected/'sentinel'; sentinel.write_bytes(b'root private sentinel'); sentinel.chmod(0o400)
    def facts():
        s = sentinel.stat()
        return s.st_uid, s.st_gid, s.st_mode, s.st_size, hashlib.sha256(sentinel.read_bytes()).hexdigest()
    original = facts(); refused = 0
    for suffix in ('-wal', '-shm'):
        sidecar = source/('live.db'+suffix); saved = source/('saved'+suffix)
        sidecar.rename(saved)
        try:
            for kind in ('symlink', 'hardlink'):
                if kind == 'symlink': sidecar.symlink_to(sentinel)
                else: os.link(sentinel, sidecar)
                try:
                    target = protected/('bad'+suffix+kind)
                    try: held.stopped_snapshot(target)
                    except InvalidAction: refused += 1
                    else: raise AssertionError('hostile sidecar accepted')
                    assert not target.exists() and facts() == original
                finally: sidecar.unlink()
        finally: saved.rename(sidecar)
    unsafe = Path('/run/service-destination'); unsafe.mkdir(mode=0o700); os.chown(unsafe,1,1)
    writable = Path('/run/writable'); writable.mkdir(mode=0o700); writable.chmod(0o720)
    linked = Path('/run/linked'); linked.symlink_to(protected)
    destinations = 0
    for parent in (unsafe, writable, linked):
        target = parent/'refused.db'
        try: held.stopped_snapshot(target)
        except InvalidAction: destinations += 1
        else: raise AssertionError('unprotected destination accepted')
        assert not target.exists()
    target = protected/'good.db'; observed = held.stopped_snapshot(target)
    with sqlite3.connect(target) as saved:
        assert saved.execute('SELECT value FROM newest').fetchall() == [('newest WAL state',)]
    assert observed.schema == 17 and (source/'live.db-shm').stat().st_uid == 1
    assert facts() == original and sentinel.stat().st_nlink == 1
    assert not list(protected.glob('.stopped-*'))
    print(json.dumps({'sidecar_refusals':refused,'destination_refusals':destinations,'newest_wal':True}))
finally:
    os.write(done_w,b'D'); os.close(done_w); os.waitpid(pid,0)
'''
    result = subprocess.run(
        ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert '"sidecar_refusals": 4' in result.stdout
    assert '"destination_refusals": 3' in result.stdout and '"newest_wal": true' in result.stdout
