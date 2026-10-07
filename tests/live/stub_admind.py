"""`admind run` for the live harness, with the admin agent and the service manager stubbed out.

Everything else is the real daemon: its own `wn-agent` child, the real relays, `ask.sock`, `ctl.sock`,
the store, the audit log and the real `approve-bead`. Only two seams of `heterodyne.admind.cli` are
replaced, both module globals that `_run_with_child` and `run` look up at call time:

- `Tmux`: an in-memory pane, so no tmux server and no `claude` ever start. The pane is "alive" from
  launch, never reports SessionStart, and drops whatever is pasted into it (the count goes to a file
  under the temp root so a test can see that something was pasted).
- `for_backend`: a service manager that never runs `systemctl`; `!restart` and `!ps` report a stub.

Run it with the harness's environment: `python stub_admind.py` (no arguments; it is `admind run`).
"""

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from heterodyne.admind import cli
from heterodyne.services import UnitStatus


class StubTmux:
    """The `TmuxLike` surface AdminAgent uses, without a tmux server."""

    def __init__(self, socket_name: str, binary: str = "tmux",
                 launcher: Callable[[], Sequence[str]] | None = None) -> None:
        self.sessions: set[str] = set()
        self.pastes = Path(os.environ["HZ_LIVE_ROOT"]) / "stub-pastes"

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def new_session(self, name: str, cwd: Path, argv: list[str]) -> None:
        self.sessions.add(name)

    def pane_dead(self, name: str) -> bool:
        return False

    def paste(self, name: str, text: str) -> None:
        with self.pastes.open("a") as fh:      # a count only; never the text
            fh.write("paste\n")

    def send_key(self, name: str, key: str) -> None:
        pass

    def capture(self, name: str, lines: int) -> str:
        return "(stub admin agent: no pane)"

    def kill(self, name: str) -> None:
        self.sessions.discard(name)

    def kill_server(self) -> None:
        self.sessions.clear()


class StubServices:
    def restart(self, unit: str) -> tuple[bool, str]:
        return False, "stub service manager: nothing restarted"

    def status(self, unit: str) -> UnitStatus:
        return UnitStatus(unit, "inactive", "dead", "stub")


def main() -> int:
    if not os.environ.get("HZ_LIVE_ROOT"):
        print("stub_admind: HZ_LIVE_ROOT is not set; refusing to run", file=sys.stderr)
        return 2
    setattr(cli, "Tmux", StubTmux)  # noqa: B010 - replacing a module global is the point
    setattr(cli, "for_backend", lambda name: StubServices())  # noqa: B010
    return cli.main(["run"])


if __name__ == "__main__":
    sys.exit(main())
