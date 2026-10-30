I found no critical flaws. It's good enough to implement as a PoC, as long as it stays the source-only transport you've scoped it to.

I ran `/code-review 153` at head `32c54f6`, CLI effort medium. It returned 10 candidates, and none of them were run through a verification pass. I read the source myself for the R3 fixes and the top items.

**The two R3 fixes are in place (`server.py:244-263`):**
- `parse_control` now runs in its own `try` before intake. Malformed input gets MALFORMED before anything is written.
- When the same request ID arrives with different bytes, and those bytes were read successfully, it raises `ControlConflict`. The client gets `refused` / `control-request-identity-conflict`.
- "Journal busy" now replies `uncertain` / `retry-same-id`, not `pending`.
- Other journal `OSError`/`ValueError`/`RuntimeError` still reply `uncertain`, as you described.

Nothing I read undoes the R1/R2 repairs: an admitted request stays accepted through shutdown, and storage failure on a known ID comes back uncertain.

**Limits to keep, ranked:**
1. **Nothing outside tests calls it yet (confirmed).** `own_manager_control` has no production caller (`runtime.py:337`), so in a real daemon every control request gets FORBIDDEN. That matches your disclosure that the consumer review is pending and ownership needs its own design. Just don't describe the PoC as working end to end.
2. **A corrupt journal makes clients retry forever (confirmed, `control.py:172`).** One stray non-`.json` file in `pending/`, or one bad outcome file, makes `_pending` raise `ValueError` on every intake. The server turns that into `uncertain` / `retry-same-id`, so the client retries indefinitely and no operator is alerted. It's the same thing as your disclosed "other ValueError stays uncertain", but it stops the journal from making progress rather than losing data. Fix it, or at least make it visible, before a consumer is wired in.
3. **`shim.py control` refuses input ending in a newline (confirmed, `shim.py:409`).** The code comment shows this is deliberate, but `echo` and heredocs will fail; only `printf` without a newline works. Document it or relax it.
4. **Carried over from R3 and still unverified:**
   - `close_manager_control` also closes worker servers (`runtime.py:349`).
   - A held owner lock escapes as a raw `BlockingIOError` instead of `RuntimeUnavailable` (`runtime.py:342`).
   - Each intake re-hashes the whole pending backlog while holding the lock.
5. **New this round and still unverified:**
   - A control-schema `ws_request` from a session without a journal now returns FORBIDDEN and is no longer written to the events log, so there's no audit record of it (`server.py:241`).
   - `server.py` now imports the wsd stack through `control.py`.
   - Orphaned `.intake-*` temp files in `requests/` and `outcomes/` are never cleaned up.
   - The "handlers still unwinding" check is duplicated in two places.

None of these loses accepted data or breaks security.

These caveats still stand:
- The quality check exits 2 with seven major issues disclosed, including handler complexity rising from 23 to 27.
- There's no full-suite clearance.
- Producing ordinary tasks still needs human design approval (ph58).
- Nothing here claims OpenShell, Marmot enrollment, native ownership or deployment.

I made no edits and didn't touch the queue, services, anything live or publishing, and the earlier review rounds weren't reset.

I found no critical flaws. It's good enough to implement as a PoC, as long as it stays the source-only transport you've scoped it to.

I ran `/code-review 153` at head `32c54f6`, CLI effort medium. It returned 10 candidates, and none of them were run through a verification pass. I read the source myself for the R3 fixes and the top items.

**The two R3 fixes are in place (`server.py:244-263`):**
- `parse_control` now runs in its own `try` before intake. Malformed input gets MALFORMED before anything is written.
- When the same request ID arrives with different bytes, and those bytes were read successfully, it raises `ControlConflict`. The client gets `refused` / `control-request-identity-conflict`.
- "Journal busy" now replies `uncertain` / `retry-same-id`, not `pending`.
- Other journal `OSError`/`ValueError`/`RuntimeError` still reply `uncertain`, as you described.

Nothing I read undoes the R1/R2 repairs: an admitted request stays accepted through shutdown, and storage failure on a known ID comes back uncertain.

**Limits to keep, ranked:**
1. **Nothing outside tests calls it yet (confirmed).** `own_manager_control` has no production caller (`runtime.py:337`), so in a real daemon every control request gets FORBIDDEN. That matches your disclosure that the consumer review is pending and ownership needs its own design. Just don't describe the PoC as working end to end.
2. **A corrupt journal makes clients retry forever (confirmed, `control.py:172`).** One stray non-`.json` file in `pending/`, or one bad outcome file, makes `_pending` raise `ValueError` on every intake. The server turns that into `uncertain` / `retry-same-id`, so the client retries indefinitely and no operator is alerted. It's the same thing as your disclosed "other ValueError stays uncertain", but it stops the journal from making progress rather than losing data. Fix it, or at least make it visible, before a consumer is wired in.
3. **`shim.py control` refuses input ending in a newline (confirmed, `shim.py:409`).** The code comment shows this is deliberate, but `echo` and heredocs will fail; only `printf` without a newline works. Document it or relax it.
4. **Carried over from R3 and still unverified:**
   - `close_manager_control` also closes worker servers (`runtime.py:349`).
   - A held owner lock escapes as a raw `BlockingIOError` instead of `RuntimeUnavailable` (`runtime.py:342`).
   - Each intake re-hashes the whole pending backlog while holding the lock.
5. **New this round and still unverified:**
   - A control-schema `ws_request` from a session without a journal now returns FORBIDDEN and is no longer written to the events log, so there's no audit record of it (`server.py:241`).
   - `server.py` now imports the wsd stack through `control.py`.
   - Orphaned `.intake-*` temp files in `requests/` and `outcomes/` are never cleaned up.
   - The "handlers still unwinding" check is duplicated in two places.

None of these loses accepted data or breaks security.

These caveats still stand:
- The quality check exits 2 with seven major issues disclosed, including handler complexity rising from 23 to 27.
- There's no full-suite clearance.
- Producing ordinary tasks still needs human design approval (ph58).
- Nothing here claims OpenShell, Marmot enrollment, native ownership or deployment.

I made no edits and didn't touch the queue, services, anything live or publishing, and the earlier review rounds weren't reset.
