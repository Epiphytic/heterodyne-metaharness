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
    """A live process of this user whose environment can't be read, and which can't be ruled out by its
    start time: it may hold a home, so the scan is incomplete."""


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


def _status(pid: str) -> str:
    """/proc/<pid>/status, from one open: the kernel renders all of it from one task, at once."""
    return Path(f"/proc/{pid}/status").read_text()


def _stat(pid: str) -> list[str]:
    """/proc/<pid>/stat's fields from the state on (field 3 first): readable for any process."""
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def _task_ids(pid: str) -> list[str]:
    return [task.name for task in Path(f"/proc/{pid}/task").iterdir()]


def _task_environ(pid: str, tid: str) -> bytes:
    with Path(f"/proc/{pid}/task/{tid}/environ").open("rb") as f:
        return f.read()


PF_EXITING = 0x4                # /proc/<pid>/stat field 9: set as a task starts to exit, never cleared
_MATCH, _CLEAR, _SETTLE = "match", "clear", "settle"


def _facts(status: str) -> dict[str, str]:
    return {key: value.strip() for key, _, value in (line.partition(":") for line in status.splitlines())}


def _ours(facts: Mapping[str, str]) -> bool:
    """Whether the process runs with this user's real or effective uid. Status stays readable when the
    environment doesn't (another user's process, or one of ours made non-dumpable)."""
    if "Uid" not in facts:
        return True             # no Uid line: not ruled out
    real, effective = (int(u) for u in facts["Uid"].split()[:2])
    return bool({real, effective} & {os.getuid(), os.geteuid()})


def _ruled_out(pid: str, facts: Mapping[str, str], since: float | None) -> bool:
    """A live process whose environment can't be read is not a holder only if it is another user's, or
    started before the earliest home was made (`since`, less the slack). Anything else may be one. The
    start time is field 22, on the boot clock in clock ticks; an exec keeps it (`de_thread` gives the
    new leader the old one's), so it is the same process's whichever image `stat` reads."""
    if not _ours(facts):
        return True
    return since is not None and int(_stat(pid)[19]) / os.sysconf("SC_CLK_TCK") < since - SLACK_SECONDS


def _through_workers(pid: str) -> bytes | None:
    """A zombie leader's group environment, read through one of its other threads (they share one
    memory map): the first non-empty read, or None. A thread that is gone, refused or empty is skipped
    for the next; a listing can also end early (`next_tid` stops when its task loses its PID)."""
    try:
        tids = _task_ids(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    for tid in tids:
        if tid == pid:
            continue
        try:
            environ = _task_environ(pid, tid)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if environ:
            return environ
    return None


def _matched(fd: int, environ: bytes, marks: set[bytes]) -> str:
    """Signal 0 through the pidfd, opened before the read, proves the PID wasn't reused meanwhile."""
    signal.pidfd_send_signal(fd, 0)
    return _CLEAR if marks.isdisjoint(environ.split(b"\0")) else _MATCH


def _judge(pid: str, fd: int, marks: set[bytes], since: float | None) -> str:
    """One process's outcome: _MATCH (a holder), _CLEAR (not one), or _SETTLE (not known yet: the scan
    is incomplete and runs again). Raises _Unknown for a live process that may be a holder, or
    FileNotFoundError or ProcessLookupError once the process is gone.

    A non-empty environ is the group's environment then. Empty, EACCES (from the start of an exit) and
    ESRCH (no memory map) prove nothing alone, so one read of status decides:
    - `Z`/`X` with `Threads: 1` in that one read: the group is dead. The read is of one task throughout,
      and counts the group's unreleased threads under that task's lock (0 once it is released), so its
      only unreleased thread was a zombie: none is left to clone or exec.
    - `Z`/`X` with more threads: a zombie leader with live threads, read through them.
    - `Threads: 0`, a read missing either fact, or an empty environ otherwise: settle. An empty read may
      be an exiting leader's, read just before a worker exec'd and took its PID.
    - EACCES or ESRCH otherwise: settle if `PF_EXITING` is set (one task's flag, which never shows the
      group is dead), else the start and uid rules, else unknown.
    PID and pidfd continuity never show two reads saw the same task: an exec moves a worker into the
    leader's PID. So nothing is cleared on facts from two reads, but the uid and start time, which an
    exec keeps."""
    try:
        environ, why = _environ(pid), "empty"
    except PermissionError:
        environ, why = b"", "refused"
    except ProcessLookupError:          # ESRCH: no memory map; /proc/<pid> may well still be there
        environ, why = b"", "no map"
    if environ:
        return _matched(fd, environ, marks)
    facts = _facts(_status(pid))
    state, threads = facts.get("State", "")[:1], facts.get("Threads", "")
    if not state or not threads.isdigit():
        return _SETTLE
    if state in ("Z", "X"):
        if threads == "1":
            return _CLEAR
        if threads == "0":
            return _SETTLE
        group = _through_workers(pid)
        return _SETTLE if group is None else _matched(fd, group, marks)
    if why == "empty" or int(_stat(pid)[6]) & PF_EXITING:
        return _SETTLE
    if why == "refused" and _ruled_out(pid, facts, since):
        return _CLEAR
    raise _Unknown("a process that may hold a home can't be read")


def _holders(marks: set[bytes], since: float | None) -> tuple[list[int], list[int]]:
    """pidfds of the live processes whose environment holds one of `marks`, and the PIDs of those the
    scan couldn't judge yet (see `_judge`), whose pidfds it closed: while any is settling the scan is
    incomplete. The pidfd is opened before the process is read. A process that is gone is skipped. An
    unknown, or any other failure (a pidfd that can't be opened, `/proc` that can't be listed), means
    the scan is incomplete, so it raises, with every pidfd it opened closed."""
    found: list[int] = []
    settling: list[int] = []
    try:
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                fd = os.pidfd_open(int(entry.name))
            except ProcessLookupError:
                continue
            try:
                outcome = _judge(entry.name, fd, marks, since)
            except (FileNotFoundError, ProcessLookupError):
                os.close(fd)             # gone
                continue
            except BaseException:
                os.close(fd)
                raise
            if outcome == _MATCH:
                found.append(fd)
                continue
            os.close(fd)                 # not a holder, or not known yet: either way nothing is sent
            if outcome == _SETTLE:
                settling.append(int(entry.name))
    except BaseException:
        for fd in found:
            os.close(fd)
        raise
    return found, settling


def _end_holders(marks: set[bytes], since: float | None, seconds: float) -> None:
    """SIGKILL, through their pidfds, the processes `marks` find, until a complete scan finds none: no
    holder and none settling. Raises TimeoutError if one is still found or settling after `seconds`, or
    at once what a failed scan (an unknown) or signal raised."""
    deadline = time.monotonic() + seconds
    while True:
        fds, settling = _holders(marks, since)
        if not fds and not settling:
            return
        try:
            for fd in fds:
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
        finally:
            for fd in fds:
                os.close(fd)
        if time.monotonic() > deadline:
            raise TimeoutError("an app-server outlived the recovery deadline, or a process stayed unjudged")
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
