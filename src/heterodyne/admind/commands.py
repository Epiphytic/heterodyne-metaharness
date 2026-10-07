"""admind's built-in `!` commands (ADR 0001 §8). No LLM is involved.

Any text starting with `!` is a command: it is never passed to the agent (plan decision D3), because a
mistyped command must not become a prompt, and a leading `!` switches Claude Code's input to bash mode.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from heterodyne.admind.redact import redact
from heterodyne.services import ServiceManager, shown

HELP = ("admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps · !details [full] · "
        "!asks [bump|repeat] · !answer <id> <text> · !approve · !deny (as a reply to an approval card)")
ASKS_USAGE = "Usage: !asks [bump|repeat]."
FENCE = "`" * 3  # a code block around !tail output (spelled this way so it can't close a Markdown fence)
TAIL_DEFAULT = 40
TAIL_MAX = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

CommandName = Literal["new", "interrupt", "tail", "restart", "ps", "details", "asks", "answer", "approve",
                      "deny"]
MAX_REASON = 1000


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: CommandName
    arg: str | None = None
    lines: int = TAIL_DEFAULT
    rest: str | None = None     # !answer: the text after the ID; !approve: the digest; !deny: the reason


def has_control_chars(text: str) -> bool:
    """True if `text` has a C0 or C1 control character other than tab and newline."""
    return _CONTROL.search(text) is not None


def parse(text: str) -> Command | None:
    if not text.startswith("!"):
        return None
    if text[1:2].isspace() or len(text) == 1:
        raise CommandError(f"Empty command. {HELP}")
    if text[1:].split(maxsplit=1)[0] == "answer":
        return _answer(text)
    if text[1:].split(maxsplit=1)[0] == "deny":
        return _deny(text)
    name, *args = text[1:].split()
    if name == "asks":      # `!asks`, `!asks bump` or `!asks repeat` (asks bump design B1, B11)
        if args not in ([], ["bump"], ["repeat"]):
            raise CommandError(ASKS_USAGE)
        return Command("asks", arg=args[0] if args else None)
    if name in ("new", "interrupt", "ps"):
        if args:
            raise CommandError(f"!{name} takes no arguments.")
        if name == "new":
            return Command("new")
        if name == "interrupt":
            return Command("interrupt")
        return Command("ps")
    if name == "tail":
        usage = f"Usage: !tail [n], with n from 1 to {TAIL_MAX}."
        if len(args) > 1 or (args and (not args[0].isdecimal() or len(args[0]) > 20)):
            raise CommandError(usage)
        try:
            lines = int(args[0]) if args else TAIL_DEFAULT
        except ValueError:
            raise CommandError(usage) from None
        if not 1 <= lines <= TAIL_MAX:
            raise CommandError(usage)
        return Command("tail", lines=lines)
    if name == "restart":
        if len(args) != 1:
            raise CommandError("Usage: !restart <unit>")
        return Command("restart", arg=args[0])
    if name == "approve":       # the arguments are optional, and checked against the card (relay delta R7)
        return Command("approve", arg=args[0] if args else None,
                       rest=" ".join(args[1:]).lower() if len(args) > 1 else None)
    if name == "details":
        if args not in ([], ["full"]):
            raise CommandError(
                "Usage: !details [full], as a reply to a summary or batch (or alone, for the latest).")
        return Command("details", arg="full" if args else None)
    raise CommandError(f"Unknown command !{name}. {HELP}")


def _answer(text: str) -> Command:
    """`!answer <id> <text>`: the text is everything after the ID, internal newlines kept (spec §6)."""
    parts = text[1:].split(maxsplit=2)
    rest = parts[2].strip() if len(parts) == 3 else ""
    if not rest:
        raise CommandError("Usage: !answer <id> <text>")
    return Command("answer", arg=parts[1], rest=rest)


def _deny(text: str) -> Command:
    """`!deny [<bead> [<reason>]]`: the bead is optional and checked against the card (relay delta R7); the
    reason is everything after it, redacted, at most MAX_REASON characters, and may be empty (R19, R28)."""
    parts = text[1:].split(maxsplit=2)
    reason = redact(parts[2].strip()) if len(parts) == 3 else ""
    if len(reason) > MAX_REASON:
        raise CommandError(f"!deny: the reason is at most {MAX_REASON:,} characters.")
    return Command("deny", arg=parts[1] if len(parts) > 1 else None, rest=reason or None)


class AgentControl(Protocol):
    def new(self) -> str: ...
    def interrupt(self) -> None: ...
    def tail(self, lines: int) -> str: ...
    def alive(self) -> bool: ...


class CommandRunner:
    """Executes parsed commands and returns the reply text. Synchronous: the daemon runs it in a thread."""

    def __init__(self, agent: AgentControl, services: ServiceManager, restart_units: tuple[str, ...],
                 wn_alive: Callable[[], bool]) -> None:
        self.agent = agent
        self.services = services
        self.restart_units = restart_units
        self.wn_alive = wn_alive

    def run(self, cmd: Command) -> str:
        if cmd.name == "new":
            self.agent.new()
            return "Started a fresh admin agent session."
        if cmd.name == "interrupt":
            self.agent.interrupt()
            return "Sent Esc to the admin agent."
        if cmd.name == "tail":
            return f"{FENCE}\n{self.agent.tail(cmd.lines)}\n{FENCE}"
        if cmd.name == "restart":
            unit = cmd.arg or ""
            if unit not in self.restart_units:
                allowed = ", ".join(shown(u) for u in self.restart_units) or "none"
                return f"!restart: that unit is not in [admind] restart_units (allowed: {allowed})"
            ok, detail = self.services.restart(unit)
            return f"Restarted {shown(unit)}." if ok else f"Restart of {shown(unit)} failed: {shown(detail)}"
        if cmd.name in ("details", "asks", "answer", "approve", "deny"):
            raise CommandError("internal")      # the daemon handles these: they need the target or the store
        lines = [self.services.status(unit).line() for unit in self.restart_units]
        lines.append(f"wn-agent (admind): {'running' if self.wn_alive() else 'down'}")
        lines.append(f"admin agent: {'running' if self.agent.alive() else 'not running'}")
        return "\n".join(lines)
