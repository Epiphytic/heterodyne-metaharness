"""A fake interactive `claude` for integration tests: it fires the hooks from --settings.

On start it runs the SessionStart hooks. For each line typed into its terminal it runs the
UserPromptSubmit hooks (with the line as `prompt`), then the Stop hooks with
`last_assistant_message = "echo: <line>"`. It also keeps a transcript `<session>.jsonl` next to the log
(user and assistant records, like Claude Code's); UserPromptSubmit carries its path. Special lines:
- `__silent__`: the turn ends with no text; its Stop carries only `transcript_path`, so admind's fallback
  reads a transcript whose earlier turns do have text;
- `__hang__`: the turn doesn't end; the fake blocks until it reads an Escape byte (`!interrupt`), then
  ends the turn without a Stop, as Claude Code does for a user interrupt;
- `__noprompt__`: no UserPromptSubmit is fired (as if that hook's delivery was lost);
- `__nostop__`: the turn ends with no Stop (as if that hook's delivery was lost).
The markers combine (`__noprompt__ __hang__` is a long turn whose prompt hook was lost). It appends its
argv to $FAKE_CLAUDE_LOG.
"""

import json
import os
import subprocess
import sys
import termios
import tty
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_CLAUDE_LOG"]).open("a") as log:
    log.write(json.dumps(args) + "\n")
settings = json.loads(Path(args[args.index("--settings") + 1]).read_text())
flag = "--session-id" if "--session-id" in args else "--resume"
session = args[args.index(flag) + 1]


def fire(event: str, **extra: object) -> None:
    payload = json.dumps({"hook_event_name": event, "session_id": session, **extra}).encode()
    for group in settings["hooks"].get(event, []):
        for hook in group["hooks"]:
            # Claude Code runs hook commands through a shell; so does the fake, so quoting bugs in
            # hook_command() fail here. The command comes from admind's own generated settings file.
            subprocess.run(hook["command"], shell=True, input=payload, check=False)  # noqa: S602


transcript = Path(os.environ["FAKE_CLAUDE_LOG"]).with_name(f"{session}.jsonl")
transcript.touch()      # like Claude Code's: it exists before the first prompt, so admind can measure it


def record(kind: str, content: object) -> None:
    with transcript.open("a") as fh:
        fh.write(json.dumps({"type": kind, "message": {"content": content}}) + "\n")


# The pane's tty stays in cbreak mode for the fake's whole life and lines are assembled here. Switching
# between cbreak and canonical mode around a hang raced with a paste arriving right after the Escape:
# bytes queued while non-canonical could leave readline() blocked on a line that had already arrived.
FD = sys.stdin.fileno()
if os.isatty(FD):
    tty.setcbreak(FD)
    # Python 3.12.0-3.12.1's setcbreak() also clears ICRNL; keep it so Enter (CR) still arrives as LF.
    attrs = termios.tcgetattr(FD)
    attrs[0] |= termios.ICRNL
    termios.tcsetattr(FD, termios.TCSANOW, attrs)


def read_line() -> str | None:
    buf = bytearray()
    while (b := os.read(FD, 1)) != b"":
        if b == b"\n":   # ICRNL stays on in cbreak mode, so Enter (CR) arrives as LF
            return buf.decode(errors="replace")
        buf += b
    return buf.decode(errors="replace") if buf else None


def wait_for_escape() -> None:
    while os.read(FD, 1) not in (b"\x1b", b""):
        pass


fire("SessionStart", source="startup")
while (raw := read_line()) is not None:
    line = raw.lstrip("\x1b")
    if "__noprompt__" not in line:
        fire("UserPromptSubmit", prompt=line, transcript_path=str(transcript))
    record("user", line)
    if "__hang__" in line:
        wait_for_escape()
    elif "__nostop__" in line:
        record("assistant", [{"type": "text", "text": f"echo: {line}"}])
    elif line == "__silent__":
        record("assistant", [{"type": "tool_use", "name": "Bash"}])
        fire("Stop", transcript_path=str(transcript), stop_hook_active=False)
    else:
        record("assistant", [{"type": "text", "text": f"echo: {line}"}])
        fire("Stop", last_assistant_message=f"echo: {line}", stop_hook_active=False)
