"""A fake interactive `claude` for integration tests: it fires the hooks from --settings.

On start it runs the SessionStart hooks. For each line typed into its terminal it runs the
UserPromptSubmit hooks (with the line as `prompt`), then the Stop hooks with
`last_assistant_message = "echo: <line>"`. It also keeps a transcript `<session>.jsonl` next to the log
(user and assistant records, like Claude Code's). Special lines:
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


def record(kind: str, content: object) -> None:
    with transcript.open("a") as fh:
        fh.write(json.dumps({"type": kind, "message": {"content": content}}) + "\n")


def wait_for_escape() -> None:
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        while os.read(fd, 1) != b"\x1b":
            pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


fire("SessionStart", source="startup")
while raw := sys.stdin.readline():
    line = raw.rstrip("\n").lstrip("\x1b")
    if "__noprompt__" not in line:
        fire("UserPromptSubmit", prompt=line)
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
