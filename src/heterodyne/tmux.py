"""A small tmux wrapper on a private server socket (`tmux -L <name>`, or `tmux -S <path>` if given).

Targets use exact matching: `=name` for sessions and `=name:` for the window or pane (tmux 3.4 rejects
`=name` as a window target). Text is passed to tmux as UTF-8 bytes, so a C locale under a service
manager can't mangle it. Pastes use bracketed paste (`paste-buffer -p`), so newlines inside the text do
not submit it early. The pane is kept after its process exits (`remain-on-exit`) so its last screen
can still be read.

tmux 3.4 exits 0 when a start can't create its socket (only stderr says so), so a start is confirmed by
the marker line `new-session -P -F` prints for the session it created, never by the exit status alone.
"""

import contextlib
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

LAUNCHER_FAILED = "tmux server could not be started through the launcher"
NO_SESSION = "tmux reported a start through the launcher, but no session exists"


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

    def _exec(self, argv: list[str], *, input: bytes | None,
              timeout: float) -> subprocess.CompletedProcess[bytes]:
        """The one place tmux is run. Callers map its exceptions and exit status."""
        return subprocess.run(argv, input=input, capture_output=True, timeout=timeout, check=False)

    def _run(self, *args: str, data: bytes | None = None,
             check: bool = True) -> subprocess.CompletedProcess[bytes]:
        proc = self._exec([self.binary, *self._selector(), *args], input=data, timeout=15)
        if check and proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()
            raise TmuxError(f"tmux {args[0]} failed: {detail}")
        return proc

    def has_session(self, name: str) -> bool:
        return self._run("has-session", "-t", f"={name}", check=False).returncode == 0

    def new_session(self, name: str, cwd: Path, argv: list[str], set: Mapping[str, str] | None = None,
                    unset: Sequence[str] = ()) -> None:
        """Start `name` running `argv` in `cwd`. It returns only once tmux has confirmed that this call
        created exactly that session; anything else raises TmuxError.

        The pane's environment is the server's global one, which outlives the client that started the
        server: each name in `unset` is removed from it, and each `set` entry is given to the new pane
        only (`new-session -e`), in the same invocation."""
        if not cwd.is_dir():    # tmux would exit 0 and run the pane in the home directory instead
            raise TmuxError(f"tmux session directory is missing: {cwd}")
        clear = [arg for var in unset for arg in ("set-environment", "-g", "-u", var, ";")]
        pane_env = [arg for var, value in (set or {}).items() for arg in ("-e", f"{var}={value}")]
        # One invocation, so retention is set before the process can start (and exit): a separate
        # set-option afterwards races an immediately-exiting process. The marker carries a nonce, so no
        # other output can pass for it, and the new session's id, the only safe target for a cleanup.
        prefix = f"hz-started {uuid.uuid4().hex} "
        args = ("start-server", ";", "set-option", "-g", "remain-on-exit", "on", ";", *clear,
                "new-session", "-d", "-P", "-F", prefix + "#{session_id} #{session_name}",
                "-s", name, "-x", "200", "-y", "50", "-c", str(cwd), *pane_env, "--", *argv)
        if self.launcher is None:
            proc = self._run(*args)
        else:
            # The server is forked by this invocation and stays wherever it starts, so only this call is
            # wrapped, always (no probe, so no race with a server dying between probe and start). If a
            # server is already running the wrapped client just asks it and its scope is collected on exit.
            # A failing launcher is an error: never fall back to starting the server unwrapped.
            try:
                proc = self._exec([*self.launcher(), self.binary, *self._selector(), *args], input=None,
                                  timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise TmuxError(LAUNCHER_FAILED) from None
            if proc.returncode != 0:
                raise TmuxError(LAUNCHER_FAILED)
        self._confirm(name, prefix, proc)

    def _confirm(self, name: str, prefix: str, proc: subprocess.CompletedProcess[bytes]) -> None:
        """Exactly one well-formed marker line naming `name` confirms the start. A renamed session (tmux
        rewrites `:` and `.`) is removed by the id in its marker; no other line is ever a cleanup target."""
        lines = proc.stdout.decode("utf-8", "replace").splitlines()
        markers = [line.removeprefix(prefix) for line in lines if line.startswith(prefix)]
        sid, sep, reported = markers[0].partition(" ") if len(markers) == 1 else ("", "", "")
        if sep and re.fullmatch(r"\$[0-9]+", sid) and reported:
            if reported == name:
                return
            self._run("kill-session", "-t", sid, check=False)
            detail = f"renamed to {reported!r}"
        else:
            detail = proc.stderr.decode("utf-8", "replace").strip() or "no session reported"
        if self.launcher is not None:
            raise TmuxError(NO_SESSION)
        raise TmuxError(f"tmux new-session did not create {name!r}: {detail}")

    def pane_dead(self, name: str) -> bool:
        # Only an explicit 0 is a live pane: tmux prints nothing, and exits 0, for a session that is gone.
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_dead}")
        return proc.stdout.decode().strip() != "0"

    def pane_info(self, name: str) -> tuple[str, int]:
        """The ID (`%N`) and process PID of the session's pane. Raises TmuxError if there is none."""
        proc = self._run("display-message", "-p", "-t", f"={name}:", "#{pane_id} #{pane_pid}")
        pane, _, pid = proc.stdout.decode("utf-8", "replace").strip().partition(" ")
        if not re.fullmatch(r"%[0-9]+", pane) or not pid.isdigit():
            raise TmuxError(f"tmux reported no pane for {name!r}")
        return pane, int(pid)

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
