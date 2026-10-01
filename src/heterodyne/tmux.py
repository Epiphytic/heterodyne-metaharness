"""A small tmux wrapper on a private server socket (`tmux -L <name>`).

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
from pathlib import Path


class TmuxError(RuntimeError):
    pass


class TmuxPasteUncertain(TmuxError):
    """A paste may or may not have been submitted: the Enter step, or something after it, failed or
    timed out. Unlike any earlier TmuxError, retrying could deliver the text twice."""


class Tmux:
    def __init__(self, socket_name: str, binary: str = "tmux") -> None:
        self.socket_name = socket_name
        self.binary = binary

    def _run(self, *args: str, data: bytes | None = None,
             check: bool = True) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run([self.binary, "-L", self.socket_name, *args], input=data,
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
        self._run("start-server", ";", "set-option", "-g", "remain-on-exit", "on", ";",
                  "new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd), "--", *argv)

    def pane_dead(self, name: str) -> bool:
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_dead}")
        return proc.stdout.decode().strip() == "1"

    def paste(self, name: str, text: str) -> None:
        """Paste `text` and submit it. A failure before the Enter step raises a plain TmuxError: the text
        was definitely not submitted. A failure at or after Enter raises TmuxPasteUncertain: it may have
        been, so the caller must not paste it again."""
        buffer = f"hz-{uuid.uuid4().hex}"
        try:
            self._run("load-buffer", "-b", buffer, "-", data=text.encode("utf-8"))
            try:
                self._run("paste-buffer", "-p", "-d", "-b", buffer, "-t", f"={name}:")
                time.sleep(0.3)  # let the application finish reading the paste before Enter arrives
            except BaseException:
                with contextlib.suppress(Exception):
                    self._run("delete-buffer", "-b", buffer, check=False)
                raise
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise TmuxError(f"tmux paste failed ({type(exc).__name__})") from None
        try:
            self._run("send-keys", "-t", f"={name}:", "Enter")
            self._run("delete-buffer", "-b", buffer, check=False)
        except Exception as exc:  # noqa: BLE001 - whatever failed, the text may already be submitted
            raise TmuxPasteUncertain(f"tmux paste may have been submitted ({type(exc).__name__})") from None

    def send_key(self, name: str, key: str) -> None:
        self._run("send-keys", "-t", f"={name}:", key)

    def capture(self, name: str, lines: int) -> str:
        proc = self._run("capture-pane", "-p", "-J", "-S", f"-{lines}", "-t", f"={name}:")
        return proc.stdout.decode("utf-8", "replace").rstrip("\n")

    def kill(self, name: str) -> None:
        self._run("kill-session", "-t", f"={name}", check=False)

    def kill_server(self) -> None:
        self._run("kill-server", check=False)
