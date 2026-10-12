# Plan 4 Task 14 code review, cycle 3

Reviewer: gpt-6.1-sol. Reviewed commit: 254ad41a659994859ec3c9bd37c0b94458eb38f5. Verdict: REVISE.

Note: this was the last cycle (the cap is 3). Both findings are fixed in d88bcc3. The controller (Claude) reviewed d88bcc3 against the fix Codex stated, and there was no 4th Codex cycle. In the same check the controller found two more problems, both fixed in 32c0bc7. The image IDs and repo digests at line 371 still broke the 64-hex rule. The cache-hash comparison could pass vacuously when the preflight found no cache. f11d96d moved the codex shape to its own bead, after the live run on 32c0bc7 surfaced a two-sessions-on-one-bead landing conflict (follow-up btq-jjnqm). The controller reviewed it. The live module passed on f11d96d.

1. [BLOCKING] `tests/live/test_live_sandbox.py:296` — Gateway binding still has a fail-open path. The port-only `ss` query counts visible PIDs, ignoring listener addresses and rows without process metadata. A different user’s gateway on `127.0.0.1` plus a visible packaged gateway on another address at the same port can select the wrong process. Per-launch image checks cannot establish which gateway configuration handled creation. **Fix:** match the actual endpoint’s listening socket, reject ambiguous or unidentified listeners, and verify its owning PID before inspecting configuration.

2. [NON-BLOCKING] `tests/live/test_live_sandbox.py:375` — The new evidence prints the complete 64-hex cached-binary SHA256, violating the plan’s Global Constraints. **Fix:** retain the full hash internally for comparisons and print a shortened identifier.

The teardown fix and image erratum are sound. The listener-binding gap prevents approval even with the final live run pending.

REVISE