"""admind's private wn-agent child (ADR 0001 §8; plan decision D1).

The child has its own home (0700) and control socket, and a bearer token that admind generates on
first use (0600, never logged or printed). It runs inside admind's unit, so the unit owns the identity.
S4 showed that a wn-agent home can't be opened by a second process, so nothing else may use this home
while admind runs.
"""

import asyncio
import contextlib
import os
import secrets
import stat
import time
from collections.abc import Callable
from pathlib import Path

from heterodyne.admind.audit import Audit
from heterodyne.admind.store import private_dir
from heterodyne.marmot.control import ControlClient, ControlError

HEALTHY_RESET = 60.0  # seconds a restarted child must stay up before the backoff resets


class WnAgentError(RuntimeError):
    pass


class WnAgent:
    def __init__(self, binary: str, home: Path, relays: tuple[str, ...], audit: Audit,
                 healthy_reset: float = HEALTHY_RESET) -> None:
        self.healthy_reset = healthy_reset
        self.binary = binary
        self.home = home
        self.relays = relays
        self.audit = audit
        self.socket_path = home / "ctl" / "wn-agent.sock"
        self.token_path = home / "control.token"
        self.proc: asyncio.subprocess.Process | None = None

    def prepare(self) -> None:
        if self.home.is_symlink():
            raise WnAgentError("[admind.marmot] home must not be a symlink")
        try:
            private_dir(self.home)
            private_dir(self.socket_path.parent)
        except OSError as exc:
            raise WnAgentError(f"cannot prepare [admind.marmot] home ({type(exc).__name__})") from None
        try:
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600)
        except FileExistsError:
            return
        except OSError as exc:
            raise WnAgentError(f"cannot create the control token file ({type(exc).__name__})") from None
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_hex(32) + "\n")
        except OSError as exc:
            raise WnAgentError(f"cannot write the control token file ({type(exc).__name__})") from None

    def token(self) -> str:
        """The bearer token, read only from a regular 0600 file owned by this user (never via a symlink)."""
        try:
            # O_NONBLOCK so a FIFO without a writer can't hang the open before fstat rejects it.
            fd = os.open(self.token_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as exc:
            raise WnAgentError(f"cannot open the control token file ({type(exc).__name__})") from None
        try:
            with os.fdopen(fd) as fh:
                st = os.fstat(fh.fileno())
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                        or stat.S_IMODE(st.st_mode) != 0o600):
                    raise WnAgentError("the control token file must be a regular file owned by this "
                                       "user, mode 0600")
                value = fh.read().strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise WnAgentError(f"cannot read the control token file ({type(exc).__name__})") from None
        if len(value) < 32:
            raise WnAgentError("the control token file is empty or too short")
        return value

    def _relay_args(self) -> list[str]:
        return [arg for relay in self.relays for arg in ("--relay", relay)]

    def argv(self) -> list[str]:
        return [self.binary, "--home", str(self.home), "--socket", str(self.socket_path),
                "--auth-token-file", str(self.token_path), *self._relay_args()]

    def bootstrap_argv(self, label: str) -> list[str]:
        return [self.binary, "bootstrap", "--home", str(self.home), "--socket", str(self.socket_path),
                "--auth-token-file", str(self.token_path), "--label", label, "--invite-policy", "deny",
                "--no-quic", "--json", "--wait-for-socket", "30", *self._relay_args()]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, client: ControlClient, wait: float = 30.0) -> None:
        self.prepare()
        try:
            self.proc = await asyncio.create_subprocess_exec(*self.argv(), stdin=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            raise WnAgentError(f"cannot start wn-agent ({type(exc).__name__})") from None
        self.audit.write("wn-agent", action="start", pid=self.proc.pid)
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            if self.proc.returncode is not None:
                raise WnAgentError(f"wn-agent exited with status {self.proc.returncode} during startup")
            try:
                await client.account_list()
                return
            except ControlError:
                if asyncio.get_running_loop().time() > deadline:
                    await self.stop()
                    raise WnAgentError(f"wn-agent did not answer on its socket within {wait:.0f}s") from None
                await asyncio.sleep(0.25)

    async def supervise(self, client: ControlClient, clock: Callable[[], float] | None = None) -> None:
        """Restart the child whenever it exits, with backoff up to 60s (reset only after the child
        stayed up for `healthy_reset` seconds). Runs until cancelled."""
        delay = 1.0
        clock = clock or time.monotonic
        while True:
            if self.proc is None:
                raise WnAgentError("supervise() before start()")
            started = clock()
            status = await self.proc.wait()
            self.audit.write("wn-agent", action="exited", status=status)
            if clock() - started >= self.healthy_reset:
                delay = 1.0
            await asyncio.sleep(delay)
            try:
                await self.start(client)
                delay = min(delay * 2, 60.0)
            except WnAgentError as exc:
                self.audit.write("wn-agent", action="restart-failed", error=str(exc))
                delay = min(delay * 2, 60.0)

    async def stop(self) -> None:
        if self.proc is None or self.proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):  # the child may have exited just now
            self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), 10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()
            await self.proc.wait()

    async def account(self, client: ControlClient) -> str:
        accounts = [a for a in (await client.account_list()).accounts if a.local_signing]
        if len(accounts) != 1:
            raise WnAgentError(f"admind's wn-agent home has {len(accounts)} local-signing accounts, "
                               "expected exactly 1 (run `admind init`)")
        return accounts[0].account_id_hex.lower()
