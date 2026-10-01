"""admind's built-in `!` commands (ADR 0001 §8). No LLM is involved.

Any text starting with `!` is a command: it is never passed to the agent (plan decision D3), because a
mistyped command must not become a prompt, and a leading `!` switches Claude Code's input to bash mode.
"""

import re
from dataclasses import dataclass
from typing import Literal

HELP = "admind commands: !new · !interrupt · !tail [n] · !restart <unit> · !ps"
FENCE = "`" * 3  # a code block around !tail output (spelled this way so it can't close a Markdown fence)
TAIL_DEFAULT = 40
TAIL_MAX = 500
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

CommandName = Literal["new", "interrupt", "tail", "restart", "ps"]


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: CommandName
    arg: str | None = None
    lines: int = TAIL_DEFAULT


def has_control_chars(text: str) -> bool:
    """True if `text` has a C0 or C1 control character other than tab and newline."""
    return _CONTROL.search(text) is not None


def parse(text: str) -> Command | None:
    if not text.startswith("!"):
        return None
    if text[1:2].isspace() or len(text) == 1:
        raise CommandError(f"Empty command. {HELP}")
    name, *args = text[1:].split()
    if name in ("new", "interrupt", "ps"):
        if args:
            raise CommandError(f"!{name} takes no arguments.")
        if name == "new":
            return Command("new")
        if name == "interrupt":
            return Command("interrupt")
        return Command("ps")
    if name == "tail":
        if len(args) > 1 or (args and not args[0].isdecimal()):
            raise CommandError(f"Usage: !tail [n], with n from 1 to {TAIL_MAX}.")
        lines = int(args[0]) if args else TAIL_DEFAULT
        if not 1 <= lines <= TAIL_MAX:
            raise CommandError(f"Usage: !tail [n], with n from 1 to {TAIL_MAX}.")
        return Command("tail", lines=lines)
    if name == "restart":
        if len(args) != 1:
            raise CommandError("Usage: !restart <unit>")
        return Command("restart", arg=args[0])
    raise CommandError(f"Unknown command !{name}. {HELP}")
