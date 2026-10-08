"""A small tmux wrapper on a private server socket (`tmux -L <name>`, or `tmux -S <path>` if given).

Targets use exact matching: `=name` for sessions and `=name:` for the window or pane (tmux 3.4 rejects
`=name` as a window target). Text is passed to tmux as UTF-8 bytes, so a C locale under a service
manager can't mangle it. Pastes use bracketed paste (`paste-buffer -p`), so newlines inside the text do
not submit it early. The pane is kept after its process exits (`remain-on-exit`) so its last screen
can still be read.
"""

import contextlib
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

LAUNCHER_FAILED = "tmux server could not be started through the launcher"


class TmuxError(RuntimeError):
    pass


class TmuxPasteUncertain(TmuxError):
    """A paste may or may not have been submitted: the Enter step, or something after it, failed or
    timed out. Unlike any earlier TmuxError, retrying could deliver the text twice."""


class Tmux:
    def __init__(self, socket_name: str, binary: str = "tmux",
                 launcher: Callable[[], Sequence[str]] | None = None, *,
                 socket_path: Path | None = None) -> None:
        """`launcher` returns a command prefix (called afresh for each start, so it can carry a unique
        name) used only for the invocation that starts the server (see `new_session`), for example a
        service manager's way to run it in a cgroup of its own. Every other call runs tmux directly.
        `socket_path` pins the server to that exact socket (`-S`) instead of `socket_name` in tmux's
        own directory (`-L`)."""
        self.socket_name = socket_name
        self.binary = binary
        self.launcher = launcher
        self.socket_path = socket_path

    def _selector(self) -> tuple[str, str]:
        if self.socket_path is not None:
            return ("-S", str(self.socket_path))
        return ("-L", self.socket_name)

    def _run(self, *args: str, data: bytes | None = None,
             check: bool = True) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run([self.binary, *self._selector(), *args], input=data,
                              capture_output=True, timeout=15, check=False)
        if check and proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise TmuxError(f"tmux {args[0]} failed: {detail}")
        return proc

    def has_session(self, name: str) -> bool:
        return self._run("has-session", "-t", f"={name}", check=False).returncode == 0

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        # One invocation, so retention is set before the process can start (and exit): a separate
        # set-option afterwards races an immediately-exiting process.
        args = ("start-server", ";", "set-option", "-g", "remain-on-exit", "on", ";",
                "new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd), "--", *argv)
        if self.launcher is None:
            self._run(*args)
            return
        # The server is forked by this invocation and stays wherever it starts, so only this call is
        # wrapped, always (no probe, so no race with a server dying between probe and start). If a server
        # is already running the wrapped client just asks it and its scope is collected on exit.
        # A failing launcher is an error: never fall back to starting the server unwrapped.
        try:
            proc = subprocess.run([*self.launcher(), self.binary, *self._selector(), *args],
                                  capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise TmuxError(LAUNCHER_FAILED) from None
        if proc.returncode != 0:
            raise TmuxError(LAUNCHER_FAILED)

    def pane_dead(self, name: str) -> bool:
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_dead}")
        return proc.stdout.decode().strip() == "1"

    def paste(self, name: str, text: str) -> None:
        """Paste `text` and submit it. Only a failure of the `load-buffer` step, strictly before anything
        reaches the pane, raises a plain TmuxError (definitely not delivered, so a retry is safe). A failure
        or timeout of `paste-buffer`, or of anything after it, raises TmuxPasteUncertain: text may already
        sit in the application's input line (a retry would duplicate it) or have been submitted."""
        buffer = f"hz-{uuid.uuid4().hex}"
        try:
            self._run("load-buffer", "-b", buffer, "-", data=text.encode("utf-8"))
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._drop_buffer(buffer)
            raise TmuxError(f"tmux paste failed ({type(exc).__name__})") from None
        except BaseException:
            self._drop_buffer(buffer)
            raise
        try:
            self._run("paste-buffer", "-p", "-d", "-b", buffer, "-t", f"={name}:")
            time.sleep(0.3)  # let the application finish reading the paste before Enter arrives
            self._run("send-keys", "-t", f"={name}:", "Enter")
            self._run("delete-buffer", "-b", buffer, check=False)
        except Exception as exc:  # noqa: BLE001 - whatever failed, the text may already be in the pane
            self._drop_buffer(buffer)
            raise TmuxPasteUncertain(f"tmux paste may have been delivered ({type(exc).__name__})") from None
        except BaseException:
            self._drop_buffer(buffer)
            raise

    def _drop_buffer(self, buffer: str) -> None:
        with contextlib.suppress(Exception):
            self._run("delete-buffer", "-b", buffer, check=False)

    def send_key(self, name: str, key: str) -> None:
        self._run("send-keys", "-t", f"={name}:", key)

    def capture(self, name: str, lines: int) -> str:
        proc = self._run("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={name}:")
        return proc.stdout.decode("utf-8", "replace").rstrip("\n")

    def kill(self, name: str) -> None:
        self._run("kill-session", "-t", f"={name}", check=False)

    def kill_server(self) -> None:
        self._run("kill-server", check=False)
