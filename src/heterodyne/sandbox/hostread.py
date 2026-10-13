"""The trusted host-side usage read (ADR 0001 §4.4 D9 (d), plan 4b E4). Codex only: S7 found no host
read for Claude. A short-lived `codex app-server` over stdio, in a scratch CODEX_HOME holding a copy of
the account's login, under the account's D8 lock. The account's own login directory never collects the
app-server's state. Logs carry no path, key or token: only fixed texts and exception type names."""

import contextlib
import importlib.util
import json
import logging
import math
import os
import re
import shutil
import signal
import stat
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from heterodyne.agents.base import AdapterError, jwt_exp
from heterodyne.agents.codex import HOST_SOURCE, USAGE_SOURCE, Codex, parse_rate_limits
from heterodyne.fsutil import private_dir
from heterodyne.wsd.accounts import Accounts
from heterodyne.wsd.credlock import CredentialBusy, credential_lock
from heterodyne.wsd.usage import TrustedRead
from heterodyne.wsd.workstream import utc_now

log = logging.getLogger(__name__)

ADAPTER = "codex"
LOGIN = "auth.json"
MAX_LOGIN = 1 << 20
RECOVER_SECONDS = 10.0
SLACK_SECONDS = 1.0     # start-time rounding: a process this close before a home counts as after it
_STAMP = re.compile(r"read-([0-9a-f]{32})-([0-9]+)-.*")
DIAG_ROTATED_DROPPED = ("a trusted usage read's app-server rewrote its copy of the login; the copy was "
                        "dropped and the account's login left as it was. If the account's next session "
                        "can't sign in, log it in again")
DIAG_SCRATCH_KEPT = "a trusted usage read's scratch home could not be removed; the read is discarded"
DIAG_RECOVERED = ("an interrupted trusted usage read was cleaned up: its app-server ended, its scratch home "
                  "removed")
DIAG_RECOVERY_KEPT = ("an interrupted trusted usage read could not be cleaned up; its scratch home under the "
                      "state directory's hostread folder holds a copy of a login")
DIAG_READ_FAILED = "a trusted usage read failed"
DIAG_NOT_READ = "a trusted usage read was not run"
NOT_PINNED_KEY = "the account's key is not the pinned one"
TOO_CLOSE = "the login expires within the read margin"
NOT_AT_PIN = "the codex CLI is not at its pinned version"


class NoRateLimits(Exception):
    """The app-server answered, but with no rate limits."""


class _NotRead(Exception):
    """The read stopped before it started anything; its text is one of the fixed reasons above."""


def _usage_module() -> Any:
    """codex_usage.py, the in-sandbox read's file: the host read runs the same client."""
    spec = importlib.util.spec_from_file_location("heterodyne_codex_usage", USAGE_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError("codex_usage.py can't be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_regular(path: Path) -> bytes:
    """`path`'s bytes, refusing a final link, a non-regular file and anything over MAX_LOGIN."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LOGIN:
            raise OSError("not a login file")
        data = os.read(fd, MAX_LOGIN + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_LOGIN:
        raise OSError("not a login file")
    return data


def _expiry(login: bytes) -> float:
    """The access token's expiry, from the bytes the read copies: the same bytes, so no second read."""
    try:
        tokens: Any = json.loads(login).get("tokens")
    except (ValueError, AttributeError) as exc:
        raise AdapterError("the login's expiry can't be read") from exc
    return jwt_exp(cast(dict[str, Any], tokens).get("access_token") if isinstance(tokens, dict) else None)


def _write_new(folder: Path, name: str, data: bytes) -> None:
    """A new 0600 file in `folder` holding all of `data`, flushed to disk. On any failure the file is
    unlinked before the error goes on: no part of a login is left behind."""
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        raise


class _Unknown(Exception):
    """A process of this user whose environment can't be read, and which can't be ruled out by its start
    time: it may hold a home, so the scan is incomplete."""


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")


def home_prefix(made_ns: int, boot: str | None = None) -> str:
    """A scratch home's name prefix: the boot and the boot clock (CLOCK_BOOTTIME, which a process's start
    time in /proc counts on too, so a wall-clock step can't move either) just before the home is made.
    Every process that can hold the home is started after it exists, so after `made_ns`."""
    return f"read-{_boot_id() if boot is None else boot}-{made_ns}-"


def _since(homes: list[Path]) -> float | None:
    """The boot-clock time before which no holder of `homes` can have started; None if a home's stamp
    can't be read (then no process can be ruled out by its start time). A home from an earlier boot adds
    nothing: none of its processes outlived that boot."""
    boot = _boot_id()
    earliest = math.inf
    for home in homes:
        stamp = _STAMP.fullmatch(home.name)
        if stamp is None:
            return None
        if stamp[1] == boot:
            earliest = min(earliest, int(stamp[2]) / 1e9)
    return earliest


def _environ(pid: str) -> bytes:
    with Path(f"/proc/{pid}/environ").open("rb") as f:
        return f.read()


def _ours(pid: str) -> bool:
    """Whether the process runs with this user's real or effective uid. /proc/<pid>/status stays readable
    when the environment doesn't (another user's process, or one of ours made non-dumpable)."""
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Uid:"):
            real, effective = (int(u) for u in line.split()[1:3])
            return bool({real, effective} & {os.getuid(), os.geteuid()})
    return True                 # no Uid line: not ruled out


def _stat(pid: str) -> list[str]:
    """/proc/<pid>/stat's fields from the state on (field 3 first): readable for any process."""
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def _ruled_out(pid: str, since: float | None) -> bool:
    """A process whose environment can't be read is not a holder only if it is another user's, a zombie
    (it holds no memory or files, and its environment reads as empty or not at all), or started before
    the earliest home was made (`since`, less the slack). Anything else may be one. The start time is
    field 22, on the boot clock in clock ticks."""
    if not _ours(pid):
        return True
    fields = _stat(pid)
    if fields[0] in ("Z", "X"):
        return True
    return since is not None and int(fields[19]) / os.sysconf("SC_CLK_TCK") < since - SLACK_SECONDS


def _holders(marks: set[bytes], since: float | None) -> list[int]:
    """pidfds of the live processes whose environment holds one of `marks`. The pidfd is opened before
    the process is read, and signal 0 through it afterwards proves the PID wasn't reused meanwhile.
    A zombie's environment is empty, so a killed process stops being found once it ends. A process that
    is gone is skipped, as is one whose environment can't be read and which `_ruled_out` rules out. One
    it can't rule out, or any other failure (a pidfd that can't be opened, `/proc` that can't be listed),
    means the scan is incomplete, so it raises, with every pidfd it opened closed."""
    found: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                fd = os.pidfd_open(int(entry.name))
            except ProcessLookupError:
                continue
            try:
                try:
                    environ = set(_environ(entry.name).split(b"\0"))
                except PermissionError:
                    if not _ruled_out(entry.name, since):
                        raise _Unknown("a process that may hold a home can't be read") from None
                    os.close(fd)         # not a holder, whichever process it is (signal 0 may be refused)
                    continue
                signal.pidfd_send_signal(fd, 0)
            except (FileNotFoundError, ProcessLookupError):
                os.close(fd)             # gone
                continue
            except BaseException:
                os.close(fd)
                raise
            if marks.isdisjoint(environ):
                os.close(fd)
            else:
                found.append(fd)
    except BaseException:
        for fd in found:
            os.close(fd)
        raise
    return found


def _end_holders(marks: set[bytes], since: float | None, seconds: float) -> None:
    """SIGKILL, through their pidfds, the processes `marks` find, until a complete scan finds none.
    Raises TimeoutError if one is still found after `seconds`, or what a failed scan or signal raised."""
    deadline = time.monotonic() + seconds
    while fds := _holders(marks, since):
        try:
            for fd in fds:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            for fd in fds:
                os.close(fd)
        if time.monotonic() > deadline:
            raise TimeoutError("an app-server outlived the recovery deadline")
        time.sleep(0.05)


def recover(scratch_root: Path, *, seconds: float = RECOVER_SECONDS) -> None:
    """E4: clean up the reads a crash interrupted. Called at wsd's start under its instance lock, when
    no read can be running, so every `read-*` home is a crashed read's: its app-servers (found by their
    CODEX_HOME) are killed through a pidfd, then the home is removed. The homes are the only record of
    which processes to look for, so they are removed only once a complete scan finds none of those
    processes left; otherwise all of them are kept for the next start. Never raises."""
    try:
        homes = [p for p in scratch_root.iterdir() if p.name.startswith("read-")]
    except FileNotFoundError:
        return
    except Exception:
        log.warning(DIAG_RECOVERY_KEPT)
        return
    if not homes:
        return
    try:
        _end_holders({f"CODEX_HOME={home}".encode() for home in homes}, _since(homes), seconds)
    except Exception:
        log.warning(DIAG_RECOVERY_KEPT)      # unconfirmed: the next start must still find its app-server
        return
    for home in homes:
        shutil.rmtree(home, ignore_errors=True)
    kept = any(os.path.lexists(home) for home in homes)
    log.warning(DIAG_RECOVERY_KEPT if kept else DIAG_RECOVERED)


class HostUsageReader:
    def __init__(self, accounts: Accounts, binary: str, path: str, credlocks: Path, scratch_root: Path,
                 lock_wait_seconds: float, *, env: Mapping[str, str], read_margin_seconds: int = 300,
                 limit_seconds: float = 30.0, clock: Callable[[], int] = utc_now,
                 wait_clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.accounts = accounts
        self.binary = binary                 # `[adapters.codex] binary`, located on each read
        self.path = path
        self.credlocks = credlocks
        self.scratch_root = scratch_root
        self.lock_wait_seconds = lock_wait_seconds
        self.env = dict(env)                 # the app-server's environment, CODEX_HOME aside
        self.read_margin_seconds = read_margin_seconds
        self.limit_seconds = limit_seconds
        self.clock = clock
        self.wait_clock = wait_clock
        self.sleep = sleep
        self.adapter = Codex()

    def read(self, profile: str, account: str, credential_key: str) -> TrustedRead | None:
        if self.accounts.adapter(profile) != ADAPTER:
            return None
        try:
            with credential_lock(self.credlocks, credential_key, self.lock_wait_seconds,
                                 clock=self.wait_clock, sleep=self.sleep):
                return self._locked(account, credential_key)
        except (CredentialBusy, _NotRead) as why:
            log.info("%s: %s", DIAG_NOT_READ, why)      # fixed texts only (credlock's and this module's)
            return None
        except Exception as exc:  # noqa: BLE001 - never raises (UsageReader); the type name only
            log.warning("%s: %s", DIAG_READ_FAILED, type(exc).__name__)
            return None

    def _locked(self, account: str, credential_key: str) -> TrustedRead | None:
        if self.accounts.current_key(ADAPTER, account) != credential_key:
            raise _NotRead(NOT_PINNED_KEY)
        _, files = self.accounts.login_paths(ADAPTER, account)
        login = Path(os.path.realpath(files[0]))
        original = _read_regular(login)
        if _expiry(original) - self.clock() < self.read_margin_seconds:
            raise _NotRead(TOO_CLOSE)         # S7 (4): only an expired token is refreshed; keep clear of it
        cli = self.adapter.locate(self.binary, self.path)
        if cli.version != self.adapter.version_pin:
            raise _NotRead(NOT_AT_PIN)
        private_dir(self.scratch_root)
        made_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)    # before the home: its holders start after
        scratch = Path(tempfile.mkdtemp(prefix=home_prefix(made_ns), dir=self.scratch_root))
        try:
            _write_new(scratch, LOGIN, original)
            limits = _usage_module().read(str(cli.binary), {**self.env, "CODEX_HOME": str(scratch)},
                                          self.limit_seconds)
            report = parse_rate_limits(limits, HOST_SOURCE)
            if report is None:
                raise NoRateLimits("the answer held no rate limits")
            try:
                changed = _read_regular(scratch / LOGIN) != original
            except OSError:
                changed = True                # replaced by something that isn't a login file
        finally:
            try:            # a descendant that escaped the client's process group may still hold the copy
                _end_holders({f"CODEX_HOME={scratch}".encode()}, made_ns / 1e9, RECOVER_SECONDS)
                shutil.rmtree(scratch, ignore_errors=True)
            except Exception:  # noqa: S110 - unconfirmed: kept, the next start's recover looks again
                pass
            kept = os.path.lexists(scratch)
            if kept:
                log.warning(DIAG_SCRATCH_KEPT)
        if kept:
            return None
        if changed:
            log.warning(DIAG_ROTATED_DROPPED)  # E4: never written back; D8's writers don't take the lock
        return TrustedRead(report.windows, report.limited, report.reset_hint)
