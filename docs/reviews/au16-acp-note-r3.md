Reviewer: gpt-6.1-sol. Reviewed commit: 480d772. Verdict: APPROVE.

1. [NON-BLOCKING] **docs/design/acp-adapter-note.md:86–108 — R2 cancellation finding resolved.** The note distinguishes cancellation requested, turn completed and quiescence; waits for the original prompt response before another prompt; and requires confirmed process-tree exit before committing WIP, with bounded termination and fail-closed handling. This matches the [ACP cancellation contract](https://agentclientprotocol.com/protocol/v1/prompt-turn#cancellation). **Fix:** none required.

2. [NON-BLOCKING] **docs/design/acp-adapter-note.md:3,182–200 — Ready for AU-16 acceptance.** The note covers policy integration, headless operation, harnesses and authentication, D1 accounts, and the unchanged outer sandbox. Remaining decisions and spike evidence are appropriately deferred to P2; implementation explicitly requires its own amendment, review and approval. No remaining acceptance blockers found. **Fix:** none required for this deliverable.

APPROVE