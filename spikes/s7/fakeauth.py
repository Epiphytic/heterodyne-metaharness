# Writes a dummy Codex auth.json (unsigned JWTs with made-up claims; nothing here is a credential).
import base64
import json
import sys
import time


def b64(o):
    return base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")


def jwt(claims):
    return b64({"alg": "none", "typ": "JWT"}) + "." + b64(claims) + ".sig"


who = sys.argv[2]
exp = int(time.time()) + int(sys.argv[3] if len(sys.argv) > 3 else 86400)  # argv[3]: seconds to expiry
auth = {
    "https://api.openai.com/auth": {
        "chatgpt_plan_type": "pro",
        "chatgpt_account_id": "acct-" + who,
        "chatgpt_user_id": "user-" + who,
    }
}
json.dump(
    {
        "OPENAI_API_KEY": None,
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": jwt({"email": who + "@example.invalid", "exp": exp, **auth}),
            "access_token": jwt({"exp": exp, **auth}),
            "refresh_token": "dummy-refresh-" + who,
            "account_id": "acct-" + who,
        },
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    },
    open(sys.argv[1], "w"),
)
