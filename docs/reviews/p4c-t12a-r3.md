Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 3ea7d087a217b4671ec72e09fa9ae129dc0310b5 (range dc4b03926129bfb4591793134e514517e5dd12cb..3ea7d087a217b4671ec72e09fa9ae129dc0310b5). Verdict: APPROVE.

Note: code-review cycle 3 of 3. PR #84 merged as a16d86ea6238d0b6faef404e27a5901dd567d530.

No [BLOCKING] or [NON-BLOCKING] findings under the stated PoC threshold.

R2 findings are closed:

- `README.md:161–178`: rollback is one fail-stop root script; failed stop/check prevents removals.
- `README.md:176–177`: `rmdir` preserves non-empty stores and stops before `userdel`.
- `README.md:146–153`: the read-only TOML check uses the pinned config path; config check unsets both overrides. A set fallback requires fresh approval.

Steps 3–6 and Rollback are copy-paste coherent with the named substitutions for this host. The execution ask must carry the concrete config path and `wsd` restart N/A.

Validation: 46 offline tests passed; ruff and diff whitespace checks passed. Repository unchanged; no privileged operations run.

Verdict: APPROVE