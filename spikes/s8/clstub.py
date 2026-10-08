# Scripted stand-in for the Anthropic Messages API (S8). The reply depends on the last user turn:
#   "PICK"           -> an AskUserQuestion tool call (PICKSECRET: one whose question holds a 64-hex value)
#   "BASH"           -> a Bash tool call that touches $S8_OUT/bash-ran (outside the workdir)
#   "EDITOUT"        -> a Write tool call to $S8_OUT/written.txt (outside the workdir)
#   a tool_result    -> text, after the delay in $S8_OUT/delay (seconds, default 0)
#   anything else    -> text "ACK"
# Requests without tools (title and other side calls) always get text. Logs one JSON line per request.
import http.server
import json
import os
import sys
import time
import uuid

LOG = os.environ["STUB_LOG"]
OUT = os.environ["S8_OUT"]
HEX = "ab" * 32   # a 64-hex value, which admind's redaction removes


def last_user(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            return [{"type": "text", "text": c}] if isinstance(c, str) else c
    return []


def summary(blocks):
    out = []
    for b in blocks:
        if b.get("type") == "text":
            out.append({"text": b["text"][:400]})
        elif b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            out.append({"tool_result": str(c)[:600], "is_error": b.get("is_error", False)})
    return out


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def ev(self, name, data):
        self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if not self.path.startswith("/v1/messages"):
            self.send_response(404)
            self.end_headers()
            return
        if "count_tokens" in self.path:
            b = json.dumps({"input_tokens": 1}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        tools = [t.get("name") for t in body.get("tools", [])]
        blocks = last_user(body)
        # script by the newest block: after an interrupt, Claude sends the old tool_result, an
        # "[Request interrupted by user]" text and the next prompt together in one user message
        newest = blocks[-1] if blocks else {}
        texts = newest.get("text", "") if newest.get("type") == "text" else ""
        reply = ("text", "ACK")
        if tools and newest.get("type") == "tool_result":
            try:
                time.sleep(float(open(os.path.join(OUT, "delay")).read().strip() or 0))
            except (OSError, ValueError):
                pass
            reply = ("text", "Noted; ending my turn.")
        elif tools and "PICK" in texts and "AskUserQuestion" in tools:
            q = "Which deploy target should I use?"
            if "PICKSECRET" in texts:
                q = f"Deploy with key {HEX}?"
            reply = ("tool", "AskUserQuestion", {"questions": [{
                "question": q, "header": "Target", "multiSelect": False,
                "options": [{"label": "staging", "description": "the staging host"},
                            {"label": "production", "description": "the production host"}]}]})
        elif tools and "BASH" in texts:
            reply = ("tool", "Bash", {"command": f"touch {OUT}/bash-ran", "description": "touch a file"})
        elif tools and "EDITOUT" in texts:
            reply = ("tool", "Write", {"file_path": f"{OUT}/written.txt", "content": "s8\n"})
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "tools": len(tools),
                                "has_ask_tool": "AskUserQuestion" in tools, "last_user": summary(blocks),
                                "reply": reply[:2]}) + "\n")
        if tools and not os.path.exists(os.path.join(OUT, "tools.json")):
            json.dump(body.get("tools", []), open(os.path.join(OUT, "tools.json"), "w"), indent=1)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.ev("message_start", {"type": "message_start", "message": {
            "id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant", "model": "stub-model",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}})
        if reply[0] == "text":
            self.ev("content_block_start", {"type": "content_block_start", "index": 0,
                                            "content_block": {"type": "text", "text": ""}})
            self.ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": reply[1]}})
            stop = "end_turn"
        else:
            self.ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:20], "name": reply[1], "input": {}}})
            self.ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(reply[2])}})
            stop = "tool_use"
        self.ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        self.ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                  "usage": {"output_tokens": 1}})
        self.ev("message_stop", {"type": "message_stop"})


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
