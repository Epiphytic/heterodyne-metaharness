# Scripted stand-in for the OpenAI Responses API (S8, Codex through a custom model provider). The reply
# depends on the newest input item:
#   user text "PICK"           -> a request_user_input call (PICKSECRET: a question with a 64-hex value)
#   user text "BASH"           -> a shell call that touches $S8_OUT/bash-ran (outside the workdir); if
#                                 $S8_OUT/escalate exists, it asks to run outside Codex's sandbox
#   a function_call_output     -> text, after the delay in $S8_OUT/delay (seconds, default 0)
#   anything else              -> text "ACK"
# Requests without tools (the title request and other side calls) always get text.
# Logs one JSON line per request, and the first request's tool list to $S8_OUT/cx-tools.json.
import http.server
import json
import os
import sys
import time
import uuid

LOG = os.environ["STUB_LOG"]
OUT = os.environ["S8_OUT"]
HEX = "ab" * 32


def text_of(item):
    c = item.get("content")
    if isinstance(c, str):
        return c
    return " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _log(self, rec):
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "path": self.path, **rec}) + "\n")

    def do_GET(self):
        self._log({"method": "GET"})
        b = json.dumps({"models": [], "data": []}).encode()
        self.send_response(200 if "models" in self.path else 404)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def ev(self, data):
        self.wfile.write(f"event: {data['type']}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if not self.path.endswith("/responses"):
            self._log({"method": "POST", "unhandled": True})
            self.send_response(404)
            self.end_headers()
            return
        tools = {t.get("name") or t.get("type"): t for t in body.get("tools", [])}
        if tools and not os.path.exists(os.path.join(OUT, "cx-tools.json")):
            json.dump(body.get("tools", []), open(os.path.join(OUT, "cx-tools.json"), "w"), indent=1)
        items = body.get("input", [])
        last = items[-1] if items else {}
        last_user = next((text_of(i) for i in reversed(items) if i.get("role") == "user"), "")
        reply = ("text", "ACK")
        if not tools:   # the title request and other side calls
            pass
        elif last.get("type") == "function_call_output":
            try:
                time.sleep(float(open(os.path.join(OUT, "delay")).read().strip() or 0))
            except (OSError, ValueError):
                pass
            reply = ("text", "Noted; ending my turn.")
        elif last.get("role") == "user" and "PICK" in last_user:
            q = f"Deploy with key {HEX}?" if "PICKSECRET" in last_user else "Which deploy target should I use?"
            reply = ("call", "request_user_input", {"questions": [{
                "id": "target", "header": "Target", "question": q,
                "options": [{"label": "staging", "description": "the staging host"},
                            {"label": "production", "description": "the production host"}]}]})
        elif last.get("role") == "user" and "BASH" in last_user:
            cmd = f"touch {OUT}/bash-ran"
            if "exec_command" in tools:
                args = {"cmd": cmd}
                if os.path.exists(os.path.join(OUT, "escalate")):   # so that on-request has something to ask
                    args.update(sandbox_permissions="require_escalated", justification="touch a file outside the workdir")
                reply = ("call", "exec_command", args)
            else:
                reply = ("call", "shell", {"command": ["bash", "-lc", cmd]})
        out = last.get("output")
        self._log({"tools": sorted(tools), "last_type": last.get("type") or last.get("role"),
                   "last_text": (text_of(last) if last.get("role") else str(out))[:600], "reply": reply[:2]})
        rid = "resp_" + uuid.uuid4().hex[:12]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        self.ev({"type": "response.created", "response": {"id": rid}})
        if reply[0] == "text":
            item = {"type": "message", "role": "assistant", "id": "msg_" + uuid.uuid4().hex[:8],
                    "content": [{"type": "output_text", "text": reply[1], "annotations": []}]}
        else:
            item = {"type": "function_call", "id": "fc_" + uuid.uuid4().hex[:8],
                    "call_id": "call_" + uuid.uuid4().hex[:8], "name": reply[1], "arguments": json.dumps(reply[2])}
        self.ev({"type": "response.output_item.done", "item": item})
        self.ev({"type": "response.completed", "response": {"id": rid, "usage": {
            "input_tokens": 1, "input_tokens_details": None, "output_tokens": 1,
            "output_tokens_details": None, "total_tokens": 2}}})


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
