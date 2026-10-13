"""Plan 4b Task 8: the trusted host-side read (D9 (d), E4), against the fake app-server."""

import errno
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sandbox_env import fake_jwt, install_fake_cli
from wsd_env import Clock, accounts_at

from heterodyne.agents.codex import HOST_SOURCE
from heterodyne.config import ConfigError
from heterodyne.sandbox import hostread
from heterodyne.sandbox.hostread import (
    DIAG_NOT_READ,
    DIAG_READ_FAILED,
    DIAG_RECOVERED,
    DIAG_RECOVERY_KEPT,
    DIAG_ROTATED_DROPPED,
    DIAG_SCRATCH_KEPT,
    NOT_PINNED_KEY,
    TOO_CLOSE,
    HostUsageReader,
    recover,
)
from heterodyne.wsd.accounts import ConfiguredAccounts
from heterodyne.wsd.credlock import LOCK_BUSY, credential_lock
from heterodyne.wsd.usage import Observation, TrustedRead

DAY = 86_400
RESET = 1_800_003_600
SNAPSHOT = {"limitId": "codex", "rateLimitReachedType": "rate_limit_reached",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": RESET}}
WANTED = TrustedRead((Observation("codex:codex:300m", "5h", 100, RESET, HOST_SOURCE),), True, RESET)
POPEN = subprocess.Popen        # the class itself, before a test wraps or patches it


class Rig:
    def __init__(self, tmp_path: Path, **env: str) -> None:
        self.clock = Clock()
        self.home = tmp_path / "home"
        self.login = self.home / ".codex" / "auth.json"
        self.login.parent.mkdir(parents=True)
        self.original = json.dumps({"tokens": {"access_token": fake_jwt(self.clock() + DAY)}})
        self.login.write_text(self.original)
        self.login.chmod(0o600)
        self.accounts = accounts_at(self.home)
        self.key = self.accounts.current_key("codex", "default")
        self.codex = install_fake_cli(tmp_path / "i", "codex")
        self.scratch = tmp_path / "hostread"
        self.credlocks = tmp_path / "k"
        self.pid_file = tmp_path / "read.pid"
        self.env = {"PATH": os.environ["PATH"], "HOME": str(self.home),
                    "HZ_FAKE_PID_FILE": str(self.pid_file), "HZ_FAKE_RATE_LIMITS": json.dumps(SNAPSHOT),
                    **env}
        self.reader = HostUsageReader(self.accounts, str(self.codex), os.environ["PATH"], self.credlocks,
                                      self.scratch, 1, env=self.env, limit_seconds=5, clock=self.clock)

    def read(self, profile: str = "p-two", key: str | None = None) -> TrustedRead | None:
        return self.reader.read(profile, "default", self.key if key is None else key)

    def scratch_empty(self) -> bool:
        return not self.scratch.exists() or list(self.scratch.iterdir()) == []

    def server_gone(self) -> bool:
        """The read's app-server started and is gone. It was this process's child, so a process that
        was killed but not reaped would still answer signal 0 as a zombie."""
        try:
            os.kill(int(self.pid_file.read_text()), 0)
        except ProcessLookupError:
            return True
        return False


@pytest.fixture
def logged(caplog: pytest.LogCaptureFixture) -> Callable[[], list[str]]:
    """Every line hostread logs, info and up, in order."""
    caplog.set_level(logging.INFO, logger=hostread.__name__)
    return lambda: [r.getMessage() for r in caplog.records if r.name == hostread.__name__]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[str]]:
    """Every app-server the read starts, recorded so a test can reap it by its own handle."""
    made: list[subprocess.Popen[str]] = []

    def recording(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        proc = POPEN(*args, **kwargs)
        made.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording)
    return made


def test_a_read_gives_the_accounts_windows_and_leaves_nothing(tmp_path: Path,
                                                             logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read() == WANTED
    assert rig.scratch_empty() and rig.server_gone()
    assert rig.login.read_text() == rig.original
    assert logged() == []


def test_a_claude_profile_has_no_host_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read("p-one", "ck1-" + "0" * 32) is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()      # nothing was started
    assert logged() == []


def test_a_key_that_changed_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    assert rig.read(key="ck1-" + "0" * 32) is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {NOT_PINNED_KEY}"]


def test_a_login_too_close_to_expiry_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    rig.login.write_text(json.dumps({"tokens": {"access_token": fake_jwt(rig.clock() + 299)}}))
    assert rig.read() is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {TOO_CLOSE}"]


def test_a_busy_lock_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    with credential_lock(rig.credlocks, rig.key, 0):
        assert rig.read() is None
    assert not rig.scratch.exists() and not rig.pid_file.exists()
    assert logged() == [f"{DIAG_NOT_READ}: {LOCK_BUSY}"]
    assert rig.read() == WANTED                                 # released


def test_a_refreshed_login_is_dropped_and_never_written(tmp_path: Path,
                                                         logged: Callable[[], list[str]]) -> None:
    rotated = json.dumps({"tokens": {"access_token": fake_jwt(Clock()() + 2 * DAY)}})
    rig = Rig(tmp_path, HZ_FAKE_ROTATE=rotated)
    before = rig.login.stat()
    assert rig.read() == WANTED                                 # taken under the lock, from the pinned key
    after = rig.login.stat()
    assert rig.login.read_text() == rig.original
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert [p.name for p in rig.login.parent.iterdir()] == ["auth.json"]       # nothing written beside it
    assert logged() == [DIAG_ROTATED_DROPPED]
    assert rig.scratch_empty() and rig.server_gone()


def test_short_writes_still_copy_the_whole_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    real_write = os.write
    copied: list[bytes] = []

    def one_byte(fd: int, data: bytes) -> int:
        copied.append(bytes(data[:1]))
        return real_write(fd, data[:1])

    monkeypatch.setattr(hostread.os, "write", one_byte)
    assert rig.read() == WANTED
    assert b"".join(copied) == rig.original.encode()            # one byte at a time, every byte
    assert logged() == []
    assert rig.scratch_empty() and rig.server_gone()


@pytest.mark.parametrize("call", ["write", "fsync"])
def test_a_copy_that_fails_leaves_no_file_and_starts_nothing(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch,
                                                            logged: Callable[[], list[str]],
                                                            call: str) -> None:
    rig = Rig(tmp_path)

    def fails(*_a: object) -> int:
        raise OSError("injected")

    monkeypatch.setattr(hostread.os, call, fails)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: OSError"]
    assert rig.scratch_empty() and not rig.pid_file.exists()    # the copy unlinked, its home removed
    assert rig.login.read_text() == rig.original
    assert [p.name for p in rig.login.parent.iterdir()] == ["auth.json"]


CRASHING_READER = """
import os, signal, sys
sys.path[:0] = [sys.argv[1]]
from test_sandbox_hostread import Rig
from heterodyne.sandbox import hostread
from pathlib import Path
rig = Rig(Path(sys.argv[2]), HZ_FAKE_RATE_FAULT="hang")
rig.reader.limit_seconds = 600
if sys.argv[3] == "copied":
    hostread._usage_module = lambda: os.kill(os.getpid(), signal.SIGKILL)
rig.read()
"""


def wait_for(done: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/environ").read_bytes() != b""     # a zombie, not yet reaped, has none


@pytest.mark.parametrize("crash", ["copied", "started"])
def test_a_crashed_read_is_recovered_at_the_next_start(tmp_path: Path, logged: Callable[[], list[str]],
                                                       crash: str) -> None:
    """The reader is killed after the copy is written, or once its app-server runs. Its home, holding
    the copy, outlives it; so does the orphaned app-server. `recover` finds and removes both."""
    rig = Rig(tmp_path / "rig")
    reader = POPEN([sys.executable, "-c", CRASHING_READER, str(Path(__file__).parent),
                    str(tmp_path / "crash"), crash],
                   env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    scratch = tmp_path / "crash" / "hostread"
    pid_file = tmp_path / "crash" / "read.pid"
    if crash == "started":
        assert wait_for(pid_file.exists)
        assert wait_for(lambda: pid_file.read_text().strip() != "")
        reader.kill()
    assert reader.wait(30) == -signal.SIGKILL
    homes = list(scratch.iterdir())
    assert len(homes) == 1 and (homes[0] / "auth.json").exists()            # the crash left the copy
    orphan = int(pid_file.read_text()) if crash == "started" else None
    if orphan is not None:
        assert alive(orphan)                                    # its timer died with the reader
    recover(scratch)
    assert list(scratch.iterdir()) == []
    if orphan is not None:
        assert wait_for(lambda: not alive(orphan))
    assert logged() == [DIAG_RECOVERED]
    assert rig.read() == WANTED                                 # another rig's reads are untouched


def test_recover_leaves_other_processes_and_reports_a_kept_home(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch,
                                                                logged: Callable[[], list[str]]) -> None:
    scratch = tmp_path / "hostread"
    (scratch / "read-abc").mkdir(parents=True)
    bystander = POPEN([sys.executable, "-c", "import time; time.sleep(60)"],      # a near miss: not killed
                      env={**os.environ, "CODEX_HOME": str(scratch / "read-abc") + "-not"})
    try:
        monkeypatch.setattr(hostread.shutil, "rmtree", lambda *_a, **_k: None)
        recover(scratch)
        assert logged() == [DIAG_RECOVERY_KEPT]
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(10)
    recover(tmp_path / "missing")                               # nothing there: nothing done, nothing logged
    assert logged() == [DIAG_RECOVERY_KEPT]


def open_fds() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))


@pytest.mark.parametrize("fault", ["pidfd", "proc", "signal", "deadline"])
def test_an_unconfirmed_recovery_keeps_the_homes_and_never_raises(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch,
                                                                  logged: Callable[[], list[str]],
                                                                  fault: str) -> None:
    """A pidfd that can't be opened, `/proc` that can't be listed, a kill that fails, or an app-server
    that outlives the deadline: recover can't confirm the app-server is gone. It keeps the home, the
    only way the next start can find that app-server, leaks no pidfd and raises nothing. The next
    recover, with the fault gone, finishes the job."""
    scratch = tmp_path / "hostread"
    home = scratch / "read-abc"
    home.mkdir(parents=True)
    orphan = POPEN([sys.executable, "-c", "import time; time.sleep(60)"],
                   env={**os.environ, "CODEX_HOME": str(home)})
    try:
        real_scandir, real_signal = os.scandir, signal.pidfd_send_signal

        def no_pidfd(_pid: int) -> int:
            raise OSError(errno.EMFILE, "too many open files")

        def no_proc(path: Any) -> Any:
            if path == "/proc":
                raise OSError(errno.EIO, "unreadable")
            return real_scandir(path)

        def no_kill(fd: int, sig: int) -> None:
            if sig == signal.SIGKILL:
                if fault == "signal":
                    raise PermissionError(errno.EPERM, "not permitted")
                return                                          # "deadline": the kill never lands
            real_signal(fd, sig)

        if fault == "pidfd":
            monkeypatch.setattr(hostread.os, "pidfd_open", no_pidfd)
        elif fault == "proc":
            monkeypatch.setattr(hostread.os, "scandir", no_proc)
        else:
            monkeypatch.setattr(hostread.signal, "pidfd_send_signal", no_kill)
        before = open_fds()
        recover(scratch, seconds=0.3)
        assert open_fds() == before
        assert logged() == [DIAG_RECOVERY_KEPT]
        assert home.exists() and orphan.poll() is None
        monkeypatch.undo()
        recover(scratch)
        assert orphan.wait(10) == -signal.SIGKILL
        assert not os.path.lexists(home)
        assert logged() == [DIAG_RECOVERY_KEPT, DIAG_RECOVERED]
    finally:
        orphan.kill()
        orphan.wait(10)


@pytest.mark.parametrize(("env", "reason"), [
    ({"HZ_FAKE_RATE_FAULT": "error"}, "AnswerError"), ({"HZ_FAKE_RATE_FAULT": "garbage"}, "JSONDecodeError"),
    ({"HZ_FAKE_RATE_FAULT": "hang"}, "TimeoutError"), ({"HZ_FAKE_RATE_FAULT": "close"}, "ClosedOutput"),
    ({"HZ_FAKE_RATE_FAULT": "exit"}, "ExitStatus"), ({"HZ_FAKE_RATE_LIMITS": "null"}, "NoRateLimits")])
def test_a_failed_read_is_none_names_why_and_leaves_nothing(tmp_path: Path, logged: Callable[[], list[str]],
                                                            env: dict[str, str], reason: str) -> None:
    rig = Rig(tmp_path, **env)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: {reason}"]
    assert rig.scratch_empty() and rig.server_gone()           # started, killed and reaped
    assert rig.login.read_text() == rig.original


def test_a_missing_cli_stops_before_anything_starts(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    shutil.rmtree(tmp_path / "i")
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: AdapterError"]     # locate's, never a reading or a refusal
    assert not rig.scratch.exists() and not rig.pid_file.exists()


def test_an_app_server_that_cannot_be_reaped_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           logged: Callable[[], list[str]],
                                                           started: list[subprocess.Popen[str]]) -> None:
    rig = Rig(tmp_path)
    real_wait = POPEN.wait

    def stuck(self: "subprocess.Popen[str]", timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(self.args, timeout or 0)

    monkeypatch.setattr(POPEN, "wait", stuck)
    try:
        assert rig.read() is None
    finally:
        monkeypatch.setattr(POPEN, "wait", real_wait)
        reaped = [real_wait(p, 10) for p in started]
    assert reaped == [-signal.SIGKILL]                          # it was killed; reaped here, by its handle
    assert logged() == [f"{DIAG_READ_FAILED}: NotGone"]
    assert rig.scratch_empty() and rig.server_gone()


def test_a_scratch_home_that_cant_be_removed_is_no_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    monkeypatch.setattr(hostread.shutil, "rmtree", lambda *_a, **_k: None)
    assert rig.read() is None
    assert logged() == [DIAG_SCRATCH_KEPT]
    assert rig.server_gone()


def test_a_read_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)

    def broken(_self: ConfiguredAccounts, _adapter: str, _account: str) -> str:
        raise ConfigError("account default is not configured for adapter codex")

    monkeypatch.setattr(ConfiguredAccounts, "current_key", broken)
    assert rig.read() is None
    assert logged() == [f"{DIAG_READ_FAILED}: ConfigError"]      # the type name: no text, path or key


def test_a_linked_login_is_not_read(tmp_path: Path, logged: Callable[[], list[str]]) -> None:
    rig = Rig(tmp_path)
    elsewhere = tmp_path / "elsewhere.json"
    rig.login.rename(elsewhere)
    rig.login.symlink_to(elsewhere)
    rig.key = rig.accounts.current_key("codex", "default")      # the key follows the link (D1)
    assert rig.read() == WANTED                                 # the real path is read, not the link
    assert rig.login.is_symlink() and elsewhere.read_text() == rig.original
    assert logged() == []
