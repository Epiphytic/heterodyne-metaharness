#!/usr/bin/env python3
"""Spike S2: minimal Claude Code "channel" MCP server, stdlib-only.

This is a research-preview probe, not shipped code. It implements the MCP
stdio transport (newline-delimited JSON-RPC 2.0) by hand -- no
@modelcontextprotocol/sdk dependency -- and declares the experimental
"claude/channel" capability documented at
https://code.claude.com/docs/en/channels-reference .

Behavior: on receiving the client's "initialize" request, it replies with a
result that declares the claude/channel capability (one-way channel: no
"tools" capability, so no reply tool). After the client's
"notifications/initialized" arrives, it waits a couple of seconds for the
session to settle, then emits exactly one
"notifications/claude/channel" push with:

    content = "Reply only with CHANNEL-OK"

and exits after sending it (or after a timeout with no client contact).

Pass criterion: the text "CHANNEL-OK" appears in the live Claude Code
session's transcript without any send-keys / typed input driving it there.
"""
from __future__ import annotations

import json
import sys
import threading
import time

PROTOCOL_VERSION_FALLBACK = "2025-06-18"
CHANNEL_MESSAGE = "Reply only with CHANNEL-OK"
POST_INIT_DELAY_SECONDS = 2.0
IDLE_EXIT_SECONDS = 30.0

_stdout_lock = threading.Lock()
_initialized_event = threading.Event()


def log(msg: str) -> None:
    print(f"[channel.py] {msg}", file=sys.stderr, flush=True)


def send_message(obj: dict) -> None:
    line = json.dumps(obj)
    with _stdout_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    log(f"-> {line}")


def handle_initialize(msg_id, params: dict) -> None:
    client_protocol = params.get("protocolVersion", PROTOCOL_VERSION_FALLBACK)
    result = {
        "protocolVersion": client_protocol,
        "capabilities": {
            # Presence of this key is what registers the channel listener.
            "experimental": {"claude/channel": {}},
        },
        "serverInfo": {"name": "s2-spike-channel", "version": "0.0.1"},
        "instructions": (
            "Events from the s2-spike-channel arrive as "
            '<channel source="s2-spike-channel" ...>. They are one-way: '
            "read them and act, no reply expected. When you receive the "
            "exact text 'Reply only with CHANNEL-OK', respond with exactly "
            "CHANNEL-OK and nothing else."
        ),
    }
    send_message({"jsonrpc": "2.0", "id": msg_id, "result": result})


def push_channel_message() -> None:
    send_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/claude/channel",
            "params": {
                "content": CHANNEL_MESSAGE,
                "meta": {"probe": "s2"},
            },
        }
    )
    log("pushed channel message; exiting shortly")
    time.sleep(1.0)


def pusher_thread() -> None:
    got_init = _initialized_event.wait(timeout=IDLE_EXIT_SECONDS)
    if not got_init:
        log("no notifications/initialized within timeout; exiting without push")
        sys.exit(1)
    time.sleep(POST_INIT_DELAY_SECONDS)
    push_channel_message()
    sys.exit(0)


def main() -> None:
    threading.Thread(target=pusher_thread, daemon=True).start()

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        log(f"<- {raw_line}")
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            log("failed to parse line as JSON, ignoring")
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            handle_initialize(msg_id, msg.get("params", {}))
        elif method == "notifications/initialized":
            _initialized_event.set()
        elif method == "ping" and msg_id is not None:
            send_message({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif method in ("tools/list", "resources/list", "prompts/list") and msg_id is not None:
            # One-way channel: no tools/resources/prompts declared.
            key = method.split("/")[0]
            send_message({"jsonrpc": "2.0", "id": msg_id, "result": {key: []}})
        elif msg_id is not None:
            send_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )
        # Unhandled notifications are ignored.


if __name__ == "__main__":
    main()
