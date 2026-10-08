# S7 spike review r1 (cycle 1 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Reviewed commit: 5a0aabd.

1. [BLOCKING] `spikes/s7/run-f.sh:11` (also `run-a.sh`–`run-g.sh`): the scripts claim “No network” but never establish `--unshare-net`. Running the documented expired-token scenario directly permits requests to the real OAuth endpoint. Fix: enforce network isolation around both stub and CLI, or provide a mandatory wrapper that fails closed outside the isolated namespace.

2. [BLOCKING] `docs/spikes/s7-accounts-and-usage.md:88`: `bwrap --dev-bind / /` exposes the entire host filesystem writable. The CLI can reach both scratch accounts, the default real login, and other agents’ homes; the selected file’s read-only bind does not protect its original path. This contradicts L2’s “see only account A’s login file” claim and D7’s isolation requirement. Fix every L2–L6 sandbox launch to expose only required runtime files, scratch state, and the selected credential, hiding other homes and credential paths.

3. [BLOCKING] `docs/spikes/s7-accounts-and-usage.md:72` and `:89`: L1 retains the caller’s real `HOME`, and subsequent commands retain inherited authentication and endpoint variables. For example, an existing `CLAUDE_CODE_OAUTH_TOKEN` can select a real login instead of the scratch credential. Fix: use a clean environment with an explicit allowlist and scratch home for every login and test command; explicitly control authentication and provider endpoints. Apply the same environment isolation to the Codex scripts.

4. [BLOCKING] `docs/spikes/s7-accounts-and-usage.md:51` and `:53`: the write-up advances from an alleged failed refresh attempt to “refreshes tokens” and “writes the home’s own `auth.json`.” A blocked request cannot demonstrate successful rotation or persistence. The supplied `/tmp/s7/runF` evidence contains the valid-token run with unchanged hashes; no expired-run output supporting the quoted refresh errors is present. Fix: retain the expired-run evidence and describe only the attempted refresh; leave successful rotation, file writes, and required writability unknown until L6 demonstrates them.

5. [NON-BLOCKING] `docs/spikes/s7-accounts-and-usage.md:117` and `:165`: L4’s “Handoff: reply OK” tests a fresh launch, but does not test continuation from §4.1’s deterministic handoff. Nevertheless, passing L4 is the stated condition for enabling capability (f). Fix: give account B an actual deterministic handoff containing task state from account A and verify continuation before enabling (f).

REVISE