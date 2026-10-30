I found no critical flaws. It's good enough to build on as a PoC, as long as it stays "source-only transport" as you've scoped it.

I ran the built-in review on PR 153 at head `e49c548` (`/code-review 153`, CLI effort medium). It covered the five source modules only: `runtime.py`, `spec.py`, `control.py`, `server.py` and `shim.py`. It didn't review `tests/` or the docs and receipts, and it doesn't check your R1/R2 dispositions item by item. It diffed against the PR because local `main` is stale. I then read the code for the top findings myself.

Nothing it found undoes the R2 repairs:
- A request that gets a pending marker stays accepted through shutdown.
- An ID that was already seen comes back "uncertain" when storage fails.
- The same request ID with different bytes is refused.

These are the limitations to keep, ranked:

1. **The feature isn't connected to anything yet (confirmed).** Nothing outside tests calls `own_manager_control`, `close_manager_control`, `observe_session` or `ControlJournal.pending/reply/outcome`. So in a real daemon every control request gets FORBIDDEN. This matches what you disclosed (consumer review pending, separate design for ownership). Just don't describe the PoC as working end to end.
2. **Errors that will never go away are reported as retryable (confirmed, `server.py:254`).** `parse_control` runs inside the `try`. So a bad payload, or a reused request ID with different bytes, gets `uncertain` / `retry-same-id`, and a client following that advice retries forever. It should return MALFORMED or refused. It's worth fixing early, but nothing is lost.
3. **The "journal busy" reply uses `state: "pending"` (confirmed, `server.py:251`).** That's also the word for a stored request, even though this reply comes with `ok: false` and nothing was written. A client that only checks `state` will drop the request.
4. `pending()` sorts by UUID rather than arrival order (`control.py:182`), so dependent requests can be handled out of order and an early one can wait behind later ones.
5. If setting up the control journal fails, the whole manager launch or socket rebind fails (`runtime.py:1093`). Older hook tracking goes down with it, instead of only control being switched off.
6. `close_manager_control` closes and clears every server, worker sessions included, not just the manager's (`runtime.py:347`).
7. `own_manager_control` can throw raw `BlockingIOError` or `RuntimeError` instead of `RuntimeUnavailable` (`runtime.py:342`).
8. Each new request re-reads and re-hashes every unanswered request under the lock, so busy replies get more likely as the backlog grows (`control.py:200`).
9. Per-generation `r{gen}-manager-control` folders are never cleaned up, and unanswered requests aren't carried into the next generation (`spec.py:121`).
10. `shim.py control` rejects input ending in a newline, so `echo` and heredocs fail; only `printf` without one works (`shim.py:409`).

Items 4–10 are the review's unverified candidates; I only checked items 1–3 myself. None of the ten loses accepted data or breaks security.

Before any real consumer is wired in, fix items 2 and 3, since they decide whether clients retry correctly. Your other disclosed caveats still stand: quality check exit 2 with nine major issues disclosed, and no full-suite clearance.

I made no edits and didn't touch services, the queue, publishing or anything live.

I found no critical flaws. It's good enough to build on as a PoC, as long as it stays "source-only transport" as you've scoped it.

I ran the built-in review on PR 153 at head `e49c548` (`/code-review 153`, CLI effort medium). It covered the five source modules only: `runtime.py`, `spec.py`, `control.py`, `server.py` and `shim.py`. It didn't review `tests/` or the docs and receipts, and it doesn't check your R1/R2 dispositions item by item. It diffed against the PR because local `main` is stale. I then read the code for the top findings myself.

Nothing it found undoes the R2 repairs:
- A request that gets a pending marker stays accepted through shutdown.
- An ID that was already seen comes back "uncertain" when storage fails.
- The same request ID with different bytes is refused.

These are the limitations to keep, ranked:

1. **The feature isn't connected to anything yet (confirmed).** Nothing outside tests calls `own_manager_control`, `close_manager_control`, `observe_session` or `ControlJournal.pending/reply/outcome`. So in a real daemon every control request gets FORBIDDEN. This matches what you disclosed (consumer review pending, separate design for ownership). Just don't describe the PoC as working end to end.
2. **Errors that will never go away are reported as retryable (confirmed, `server.py:254`).** `parse_control` runs inside the `try`. So a bad payload, or a reused request ID with different bytes, gets `uncertain` / `retry-same-id`, and a client following that advice retries forever. It should return MALFORMED or refused. It's worth fixing early, but nothing is lost.
3. **The "journal busy" reply uses `state: "pending"` (confirmed, `server.py:251`).** That's also the word for a stored request, even though this reply comes with `ok: false` and nothing was written. A client that only checks `state` will drop the request.
4. `pending()` sorts by UUID rather than arrival order (`control.py:182`), so dependent requests can be handled out of order and an early one can wait behind later ones.
5. If setting up the control journal fails, the whole manager launch or socket rebind fails (`runtime.py:1093`). Older hook tracking goes down with it, instead of only control being switched off.
6. `close_manager_control` closes and clears every server, worker sessions included, not just the manager's (`runtime.py:347`).
7. `own_manager_control` can throw raw `BlockingIOError` or `RuntimeError` instead of `RuntimeUnavailable` (`runtime.py:342`).
8. Each new request re-reads and re-hashes every unanswered request under the lock, so busy replies get more likely as the backlog grows (`control.py:200`).
9. Per-generation `r{gen}-manager-control` folders are never cleaned up, and unanswered requests aren't carried into the next generation (`spec.py:121`).
10. `shim.py control` rejects input ending in a newline, so `echo` and heredocs fail; only `printf` without one works (`shim.py:409`).

Items 4–10 are the review's unverified candidates; I only checked items 1–3 myself. None of the ten loses accepted data or breaks security.

Before any real consumer is wired in, fix items 2 and 3, since they decide whether clients retry correctly. Your other disclosed caveats still stand: quality check exit 2 with nine major issues disclosed, and no full-suite clearance.

I made no edits and didn't touch services, the queue, publishing or anything live.
