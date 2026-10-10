"""A fake `claude` or `codex` for the sandbox tests, chosen by its wrapper's first argument (`--as=claude`
or `--as=codex`; install_fake_cli writes the wrapper).

The sandbox tests run it on the host through FakeBackend, which exports HZ_FAKE_PATHS (inside path ->
host path) so the hook commands' /run/hz paths can be translated here. Modes:
- `codex app-server` (stdio): answers initialize and hooks/list from $CODEX_HOME's hooks.json and
  config.toml. A hook's hash is "sha256:" + the first 16 hex digits of sha256(command).
- `codex app-server --listen unix://PATH`: binds PATH and stays up until PATH is removed.
- the TUI: prints its prompt marker, fires SessionStart (Claude at start, Codex on the first prompt, as
  the real CLIs do), and for each line typed fires UserPromptSubmit and Stop, keeping a transcript
  (Claude) or a rollout (Codex) where the real CLI keeps it, so resume can find it.
"""

import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path
from typing import Any

ADAPTER = sys.argv[1].removeprefix("--as=")
ARGS = sys.argv[2:]
PATHS: dict[str, str] = json.loads(os.environ.get("HZ_FAKE_PATHS", "{}"))


def host(text: str) -> str:
    for inside in sorted(PATHS, key=len, reverse=True):
        text = text.replace(inside, PATHS[inside])
    return text


def snake(event: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def codex_hooks() -> list[tuple[str, str, str]]:
    """(key, event, command) for each hook in $CODEX_HOME/hooks.json."""
    home = Path(os.environ["CODEX_HOME"])
    hooks: Any = json.loads((home / "hooks.json").read_text())["hooks"]
    return [(f"{home / 'hooks.json'}:{snake(event)}:{i}:{j}", event, hook["command"])
            for event, groups in hooks.items() for i, group in enumerate(groups)
            for j, hook in enumerate(group["hooks"])]


def digest(command: str) -> str:
    return "sha256:" + hashlib.sha256(command.encode()).hexdigest()[:16]


def stdio_server() -> None:
    state: Any = tomllib.loads((Path(os.environ["CODEX_HOME"]) / "config.toml").read_text())
    trusted = state.get("hooks", {}).get("state", {})
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            result: Any = {"userAgent": "fake"}
        elif msg.get("method") == "hooks/list":
            listed = []
            for key, event, command in codex_hooks():
                have = trusted.get(key, {}).get("trusted_hash")
                status = "trusted" if have == digest(command) else "modified" if have else "untrusted"
                listed.append({"key": key, "eventName": event, "trustStatus": status,
                               "currentHash": digest(command), "source": "user"})
            result = {"data": [{"cwd": msg["params"]["cwds"][0], "hooks": listed, "errors": []}]}
        else:
            continue                    # a notification
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)


def listen_server(url: str) -> None:
    path = Path(url.removeprefix("unix://"))
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(path))
    srv.listen(4)
    srv.settimeout(0.2)
    while path.exists():
        with contextlib.suppress(TimeoutError, OSError):
            srv.accept()[0].close()
    srv.close()


def fire(commands: list[str], event: str, session: str | None, **extra: object) -> None:
    payload = json.dumps({"hook_event_name": event, "session_id": session, **extra}).encode()
    for command in commands:
        subprocess.run(host(command), shell=True, input=payload, check=False)  # noqa: S602


def tui() -> None:
    if ADAPTER == "claude":
        settings: Any = json.loads(Path(host(ARGS[ARGS.index("--settings") + 1])).read_text())
        commands = {e: [h["command"] for g in gs for h in g["hooks"]] for e, gs in settings["hooks"].items()}
        session: str | None = ARGS[ARGS.index("--resume" if "--resume" in ARGS else "--session-id") + 1]
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
        log = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / slug / f"{session}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        fire(commands.get("SessionStart", []), "SessionStart", session, source="startup")
    else:
        commands = {}
        for _key, event, command in codex_hooks():
            commands.setdefault(event, []).append(command)
        session = ARGS[ARGS.index("resume") + 1] if "resume" in ARGS else None
        log = None
    marker = "❯" if ADAPTER == "claude" else "›"
    print(f"{marker} ", end="", flush=True)
    started = False
    for raw in sys.stdin:
        line = raw.strip().lstrip("\x1b")
        if ADAPTER == "codex" and not started:
            session = session or str(uuid.uuid4())
            day = Path(os.environ["CODEX_HOME"]) / "sessions" / "2026" / "10" / "09"
            day.mkdir(parents=True, exist_ok=True)
            log = day / f"rollout-2026-10-09T00-00-00-{session}.jsonl"
            log.touch()
            fire(commands.get("SessionStart", []), "SessionStart", session, source="startup")
            started = True
        fire(commands.get("UserPromptSubmit", []), "UserPromptSubmit", session, prompt=line)
        if log is not None:
            with log.open("a") as fh:
                fh.write(json.dumps({"user": line}) + "\n")
        fire(commands.get("Stop", []), "Stop", session, last_assistant_message=f"echo: {line}")
        print(f"echo: {line}\n{marker} ", end="", flush=True)


if ARGS[:1] == ["app-server"]:
    if "--listen" in ARGS:
        listen_server(ARGS[ARGS.index("--listen") + 1])
    else:
        stdio_server()
else:
    tui()
