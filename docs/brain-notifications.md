# Brain notification deployment

Current contract: [spec](../spec/brain.md). Shared implementation:
[store](../harness/brain.py), [native adapters](../harness/brain_hooks.py),
[generic Hermes plugin](../plugins/session-sync/__init__.py),
[reviewed installer](../install_brain.py). Maintenance guard is unchanged.

The native Hermes caller in `agent/turn_context.py:1600-1659` persists injected
`api_content`. `agent/turn_finalizer.py:647` passes messages and turn_id to
post_llm_call. Its payload has no failed flag; an error explanation can trigger
this callback. Receipt requires marker + subsequent assistant transcript content,
not callback success or assistant_response alone. This corrects stale PluginManager
prose describing context as never persisted.

Provider contracts: [Codex hooks](https://developers.openai.com/codex/hooks),
[Claude hooks](https://code.claude.com/docs/en/hooks). Coding receipt fixtures use
Codex response_item payloads and Claude user/assistant message objects. If a native
version does not persist injected context, receipts remain pending. Deployment
must verify natural-turn receipt before claiming working native delivery; do not
launch a model workload just to test. No physical reboot is claimed by unit tests.

From integrated canonical checkout, use the repaired Hermes venv Python for YAML.
Plans embed full private configuration: write them directly to private files; never
print or publish their contents. The following is operator execution, after review:

```sh
cd /home/operator/repos/hermes-workstream-harness
/home/operator/.hermes/hermes-agent/venv/bin/python install_brain.py --home /home/operator/.hermes --codex-home /home/operator/.codex --claude-home /home/operator/.claude --output /home/operator/.local/state/hermes-quarantine/brain-notifications/plan.json
/home/operator/.hermes/hermes-agent/venv/bin/python install_brain.py --apply /home/operator/.local/state/hermes-quarantine/brain-notifications/plan.json --quarantine /home/operator/.local/state/hermes-quarantine/brain-notifications/files
/home/operator/.hermes/hermes-agent/venv/bin/python install_brain.py --trust-codex /home/operator/.codex --quarantine /home/operator/.local/state/hermes-quarantine/brain-notifications/trust
```

Quiesce config writers for apply. Repeating the same plan verifies backup and is a
no-op. Existing originals and modes are retained by the cleanup manifest. New paths
are recorded separately; rollback removes only unchanged created files and restores
verified originals in a quiet window. No adjacent publishable backup is created.
Trust backup includes pre-change Codex config. Never restore config over later edits.

Run native PluginManager hook smoke with an isolated temporary Hermes home and
unrelated cwd (covered by test_brain_hooks when run using the native venv). Then
reload affected Hermes plugin managers safely, preserving native conversations;
verify enabled plugin and natural-turn receipt. Codex hook discovery/trust uses
app-server hooks/list and starts no model. Existing sessions may need native hook
reload/resume, preserving identity and approvals.

Only after successful enable/invocation, prepare a second reviewed memory plan:

```sh
/home/operator/.hermes/hermes-agent/venv/bin/python install_brain.py --home /home/operator/.hermes --memory-enabled --output /home/operator/.local/state/hermes-quarantine/brain-notifications/memory-plan.json
/home/operator/.hermes/hermes-agent/venv/bin/python install_brain.py --apply /home/operator/.local/state/hermes-quarantine/brain-notifications/memory-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/brain-notifications/memory
```

Sync only reviewed brain mirror paths after installation; no private plans,
configuration values, transcripts, or duplicate normative spec. Task-update routing
and durable addendum notices are the separate dependent Bead, not this deployment.
