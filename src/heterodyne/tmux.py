"""A small tmux wrapper on a private server socket (`tmux -L <name>`).

Targets use exact matching: `=name` for sessions and `=name:` for the window or pane (tmux 3.4 rejects
`=name` as a window target). Text is passed to tmux as UTF-8 bytes, so a C locale under a service
manager can't mangle it. Pastes use bracketed paste (`paste-buffer -p`), so newlines inside the text do
not submit it early. The pane is kept after its process exits (`remain-on-exit`) so its last screen
can still be read.
"""

import subprocess
import time
import uuid
from pathlib import Path


class TmuxError(RuntimeError):
    pass


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
        self._run("new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd), "--", *argv)
        self._run("set-option", "-w", "-t", f"={name}:", "remain-on-exit", "on")

    def pane_dead(self, name: str) -> bool:
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_dead}")
        return proc.stdout.decode().strip() == "1"

    def paste(self, name: str, text: str) -> None:
        buffer = f"hz-{uuid.uuid4().hex}"
        self._run("load-buffer", "-b", buffer, "-", data=text.encode("utf-8"))
        try:
            self._run("paste-buffer", "-p", "-d", "-b", buffer, "-t", f"={name}:")
            time.sleep(0.3)  # let the application finish reading the paste before Enter arrives
            self._run("send-keys", "-t", f"={name}:", "Enter")
        finally:
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
