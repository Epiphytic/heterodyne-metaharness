# Hook command for the S8 probes (Claude Code and Codex). Appends the event's JSON payload, with the
# time it arrived, to $1. With --deny it answers a PreToolUse with a denial and a reason, at once; with
# --allow it answers a PermissionRequest with "allow".
import json
import sys
import time

payload = json.load(sys.stdin)
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"t": round(time.time(), 3), **payload}) + "\n")
if "--deny" in sys.argv[2:] and payload.get("hook_event_name") == "PreToolUse":
    reason = ("Your question was sent to the operators as picker s8p1; end your turn now, and their "
              "answer will arrive as your next message.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
if "--allow" in sys.argv[2:] and payload.get("hook_event_name") == "PermissionRequest":
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                             "decision": {"behavior": "allow"}}}))
