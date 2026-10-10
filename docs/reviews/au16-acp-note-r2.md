Reviewer: gpt-6.1-sol. Reviewed commit: d8e6da3. Verdict: REVISE.

Both r1 findings are resolved: lines 75–77 reconstruct and normalize partial tool-call input; lines 84–86 cancel denied requests whenever no `reject_once` exists.

1. [BLOCKING] **docs/design/acp-adapter-note.md:93 — Cancellation is incorrectly treated as completed.** The new parenthetical says the turn “ended already if the deny was a cancel.” `session/cancel` is an asynchronous notification; operations and updates can continue until the original `session/prompt` returns. Sending the reason prompt immediately can overlap the unfinished turn, and line 92’s park sequence can commit WIP while tools are still writing. [ACP cancellation contract](https://agentclientprotocol.com/protocol/v1/prompt-turn#cancellation). **Fix:** distinguish cancellation requested from turn completed. Answer pending permissions with `cancelled`, await the original prompt’s completion before sending another prompt, and establish quiescence before committing WIP. Specify a bounded timeout with runtime termination and confirmed process exit before parking if cancellation never completes.

REVISE