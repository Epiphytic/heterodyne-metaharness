**APPROVE: good enough to implement as a PoC.**

I read the full committed diff for `1fd84b5..5a4d4d0` and both repaired handoff files, then traced the summary-recovery path through the code. I found nothing that blocks a PoC. I didn't edit anything or run tests. The test evidence (184 named tests passing, 2 skipped) comes from the author's handoff, not from me.

What I checked and found correct:
- **Write order.** The summary intent is saved under the store lock before the comment is posted. Re-running the same summary only reads the result back and never posts again. A different summary is refused while one is pending.
- **Interrupted local writes.** If the run stops between pinning the summary and saving the record, the saved intent stays, so the next readback only repeats local writes.
- **Scans while a summary is pending.** Scans still check asks for closure and delivery and still report the bead's class and activity. They skip the record-comment retry and return before any new action. Scans and `summary` share one queue identity, so readback during a scan can match the comment.
- **Abandon.** `summary-abandon` takes the store lock and requires the exact intent digest plus a bounded, secret-free authority reference and reason. It makes no queue calls. The receipts carry over to later ask generations, and sending the same body again is blocked.
- **Records with lost or no local state.** A rebuilt record skips the summary fast path until a new generation starts. A new `{'state': 'new'}` record with no `n` or `transition` passes through `due`, `begin`, `outstanding` and `status` safely.

```json
[
  {
    "file": "bin/btq-supervise",
    "line": 1511,
    "summary": "abandon_summary also clears summary_pending, which may belong to an earlier, already-verified pin rather than the abandoned intent.",
    "failure_scenario": "Summary A is pinned and its summary_pending is set; then summary B's write is interrupted and abandoned. Clearing summary_pending throws away A's fast-path next card, so the bead waits a full stall threshold. It only delays the card; nothing unsafe is written."
  },
  {
    "file": "bin/btq-supervise",
    "line": 1590,
    "summary": "If the process dies after the intent is saved but before `bd comments add` runs, readback can never succeed, so the only exit is abandon, which then forbids that exact summary body forever.",
    "failure_scenario": "The process crashes between store.save(record) and queue.bd('comments','add'), so no comment exists. Re-running gives SummaryPending every time; after abandoning, the operator has to change the summary text to write it at all. This is a deliberate conservative trade-off and is documented in OPS.md."
  },
  {
    "file": "bin/btq-supervise",
    "line": 1085,
    "summary": "In a dry run, finish_summary(persist=False) still changes the in-memory record (clears summary_intent), so the dry-run report shows a normal action instead of 'summary write pending; native actions held'.",
    "failure_scenario": "A pending intent whose comment has landed: a dry run reports what it would do as if the summary were settled, while a real scan saves the pin first. The dry-run output can differ slightly from a real scan; there are no native writes."
  }
]
```

None of these is a critical flaw. All three are minor and don't change the approval.
