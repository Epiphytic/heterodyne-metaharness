"""Real child pidfd and directory I/O; manager/cgroup fixture is synthetic.

These are source tests of retained invocation binding, not a systemd or host
writer-exclusion qualification. They never contact or stop a host service.
"""

import os
import sqlite3
import subprocess
import sys

import pytest

from heterodyne.actions import deployment_cessation as cessation
from heterodyne.actions.codec import InvalidAction


@pytest.fixture
def invocation(tmp_path, monkeypatch):
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
    original_stat = cessation.Path.stat
    def protected_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self in {cessation.Path("/tmp"), cessation.Path("/dev/shm")}:  # noqa: S108 - synthetic custody only
            values = list(info)
            values[0] &= ~0o022
            return os.stat_result(values)
        return info
    monkeypatch.setattr(cessation.Path, "stat", protected_stat)
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
