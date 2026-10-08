# Local stand-in for the ChatGPT backend (S7, Codex). Logs each request; answers usage reads with a
# fixed payload and model requests (POST .../responses) with a usage-limit 429 or an empty 404.
import hashlib
import http.server
import json
import os
import sys
import time

LOG = os.environ["STUB_LOG"]
MODE = os.environ.get("STUB_MODE", "429")
USAGE = {
    "plan_type": "pro",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 42,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 3600,
            "reset_at": int(time.time()) + 3600,
        },
        "secondary_window": {
            "used_percent": 17,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 432000,
            "reset_at": int(time.time()) + 432000,
        },
    },
    "credits": {"has_credits": False, "unlimited": False, "balance": None},
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _log(self):
        a = self.headers.get("authorization") or ""
        with open(LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth_digest": hashlib.sha256(a.encode()).hexdigest()[:8] if a else None,
                        "account_header": "chatgpt-account-id" in {k.lower() for k in self.headers.keys()},
                    }
                )
                + "\n"
            )

    def _json(self, code, obj, extra=()):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._log()
        if "usage" in self.path:
            return self._json(200, USAGE)
        self._json(404, {"detail": "stub"})

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        self._log()
        if self.path.endswith("/responses") and MODE == "429":
            return self._json(
                429,
                {
                    "error": {
                        "type": "usage_limit_reached",
                        "message": "stub: usage limit reached",
                        "plan_type": "pro",
                        "resets_at": int(time.time()) + 3600,
                        "resets_in_seconds": 3600,
                    }
                },
                [
                    ("x-codex-primary-used-percent", "100"),
                    ("x-codex-primary-window-minutes", "300"),
                    ("x-codex-primary-reset-after-seconds", "3600"),
                ],
            )
        self._json(404, {"detail": "stub"})


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
