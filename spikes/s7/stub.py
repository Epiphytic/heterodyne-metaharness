# Local stand-in for the model API (S7). Answers every POST /v1/messages with the configured status.
import hashlib
import http.server
import json
import os
import sys
import time

MODE = os.environ.get("STUB_MODE", "429")
LOG = os.environ["STUB_LOG"]


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _log(self, body):
        with open(LOG, "a") as f:
            hdrs = sorted(k.lower() for k in self.headers.keys())
            f.write(
                json.dumps(
                    {
                        "method": self.command,
                        "path": self.path,
                        "header_names": hdrs,
                        "auth_kind": (
                            "x-api-key"
                            if "x-api-key" in hdrs
                            else "bearer"
                            if "authorization" in hdrs
                            else "none"
                        ),
                        # the dummy tokens are not secrets; a short digest tells them apart
                        "auth_digest": hashlib.sha256(
                            (
                                self.headers.get("authorization") or self.headers.get("x-api-key") or ""
                            ).encode()
                        ).hexdigest()[:8],
                        "body_has_marker": b"FLAMINGO" in body,
                    }
                )
                + "\n"
            )

    def do_GET(self):
        self._log(b"")
        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        self._log(b"")
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        self._log(body)
        reset = str(int(time.time()) + 3600)
        if not self.path.startswith("/v1/messages"):
            self.send_response(404)
            self.end_headers()
            return
        if MODE == "429":
            self.send_response(429)
            self.send_header("content-type", "application/json")
            self.send_header("anthropic-ratelimit-unified-status", "rejected")
            self.send_header("anthropic-ratelimit-unified-reset", reset)
            self.send_header("anthropic-ratelimit-unified-representative-claim", "five_hour")
            self.send_header("anthropic-ratelimit-unified-5h-status", "rejected")
            self.send_header("anthropic-ratelimit-unified-5h-reset", reset)
            self.send_header("anthropic-ratelimit-unified-5h-utilization", "1.0")
            self.send_header("retry-after", "3600")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"type": "error", "error": {"type": "rate_limit_error", "message": "stub: limit reached"}}
                ).encode()
            )
            return
        # MODE == "ok": a minimal streamed reply, with usage headers below the limit
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("anthropic-ratelimit-unified-status", "allowed")
        self.send_header("anthropic-ratelimit-unified-reset", reset)
        self.send_header("anthropic-ratelimit-unified-5h-utilization", "0.42")
        self.send_header("anthropic-ratelimit-unified-5h-reset", reset)
        self.send_header("anthropic-ratelimit-unified-7d-utilization", "0.17")
        self.send_header("anthropic-ratelimit-unified-7d-reset", str(int(time.time()) + 5 * 86400))
        self.end_headers()

        def ev(name, data):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        ev(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_stub",
                    "type": "message",
                    "role": "assistant",
                    "model": "stub-model",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        )
        ev(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        )
        ev(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
        )
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        )
        ev("message_stop", {"type": "message_stop"})


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
