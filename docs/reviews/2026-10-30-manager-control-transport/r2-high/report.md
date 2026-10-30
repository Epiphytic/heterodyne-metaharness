I'll spot-check the top findings before giving a verdict.

**Verdict: changes needed.**

I ran the built-in `code-review` skill with `args="153"` (effort high) against the frozen head `dab2985c` (base `35650c0c`). The review was read-only: no edits, services, queues or publication. The skill has no verify pass at this level, so I read the code for the main findings myself. Where a finding says "confirmed", I read it in the source at that head. I didn't re-run tests.

### Blocking (durability, crash ordering, acknowledgement)

1. **A request that was durably admitted can be told "refused"** (`src/heterodyne/session/control.py:202-205`, `server.py:251`). `intake()` checks `stopped` again after `_write(marker, b"{}")` has already made the pending marker durable, and `_handle` also returns FORBIDDEN if `_closed` is set after intake. If the generation closes in that window, the manager gets `refused`/FORBIDDEN, which reads as a final answer, for a request that the next owner will replay as pending. A manager that reissues with a new `request_id` gets two escalations, both acted on. Confirmed.
2. **A crash mid-write can permanently wedge the journal** (`control.py:41-53` with `_pending` at 156-158). `_write` creates its `mkstemp(".intake-*")` temp file inside `pending/`. If the process is SIGKILLed or loses power before the rename, the temp file stays behind. `_pending()` then raises "unexpected pending control entry" on every intake and every `pending()` call until someone deletes the file by hand. Confirmed.
3. **Every OSError, ValueError and RuntimeError is reported as `refused`** (`server.py:257-258`). Temporary faults (ENOSPC, EIO, fsync failure), corrupt retained records, and the closed-generation RuntimeError from item 1 all come back as the final `refused` state. A retry might succeed, and a corrupt journal looks like a policy decision. Confirmed.
4. **Shutdown can strand live sessions** (`src/heterodyne/sandbox/runtime.py:342-351`). `close_manager_control()` closes every server, worker sessions included. If any handler is still active it raises before `self.servers.clear()`. The closed servers stay in `self.servers`, so `_rebind` skips them and those sessions stay unserved until the process restarts. Confirmed.

### Non-blocking

5. **The client doesn't retry on journal-busy** (`src/heterodyne/session/shim.py:419-428`). The server's retryable `{state:"pending", error:"control-journal-busy-retry"}` reply becomes a final exit 1. `_ask` only retries transport failures, so the lock-contention repair is retryable on the wire but nothing actually retries it.
6. **The framing refusal gives a misleading error.** Refusing padded or multiline input is an approved option, but the trailing newline from `echo`, `printf '\n'` or a heredoc comes back as `operator-route-required`. That points the manager at an operator instead of at its framing.
7. **Too much work under the lock** (`control.py:190`). `_pending(reap=True)` re-reads and SHA-256-hashes up to about 100 × 64 KiB records while holding the 1 s lock window, which makes contention more likely.
8. **Dead branch** (`server.py:219`). `except UnicodeEncodeError` after `json.loads(bytes)` can't be reached, and its comment is misleading. The real guard is the `token.encode()` check at line 231.
9. **Duplicate code.** `_write`/`_sync_dir` repeat the existing atomic-write helpers in `agents/base.py` and `harness/verify.py`. The literal `64 * 1024` appears four times instead of a shared constant. This matters because item 2's temp-file cleanup fix would have to be made in every copy.
10. **Not wired into production** (`runtime.py:336`). `own_manager_control`, `close_manager_control` and `observe_session` are only called from tests, so `SessionServer.control` is always `None` outside tests. This fits the approved source-only scope (no enrollment or deployment), so I'm noting it, not blocking on it. It does mean nothing outside tests exercises the transport.

### Against the R1 dispositions

- **Looks addressed:** legacy hook compatibility (strict parsing only on the control schema), the strict control-only parser, preserving exact raw bytes (or explicitly refusing unsupported framing), and the bounded lock wait (on the server side).
- **Not fully closed:**
  - closed-event checks: item 1, which is now an acknowledgement-ordering bug
  - owner-safe shutdown and rebind: item 4
  - typed failures: item 3
  - crash-safe durable replay: item 2

As you said, the evidence on file (448 + 92 qualified tests, the earlier full suite with 5 failures, quality exit 2) isn't full-suite or quality clearance. Items 1–4 need source repairs and a round 3.

**Verdict: changes needed.**

I ran the built-in `code-review` skill with `args="153"` (effort high) against the frozen head `dab2985c` (base `35650c0c`). The review was read-only: no edits, services, queues or publication. The skill has no verify pass at this level, so I read the code for the main findings myself. Where a finding says "confirmed", I read it in the source at that head. I didn't re-run tests.

### Blocking (durability, crash ordering, acknowledgement)

1. **A request that was durably admitted can be told "refused"** (`src/heterodyne/session/control.py:202-205`, `server.py:251`). `intake()` checks `stopped` again after `_write(marker, b"{}")` has already made the pending marker durable, and `_handle` also returns FORBIDDEN if `_closed` is set after intake. If the generation closes in that window, the manager gets `refused`/FORBIDDEN, which reads as a final answer, for a request that the next owner will replay as pending. A manager that reissues with a new `request_id` gets two escalations, both acted on. Confirmed.
2. **A crash mid-write can permanently wedge the journal** (`control.py:41-53` with `_pending` at 156-158). `_write` creates its `mkstemp(".intake-*")` temp file inside `pending/`. If the process is SIGKILLed or loses power before the rename, the temp file stays behind. `_pending()` then raises "unexpected pending control entry" on every intake and every `pending()` call until someone deletes the file by hand. Confirmed.
3. **Every OSError, ValueError and RuntimeError is reported as `refused`** (`server.py:257-258`). Temporary faults (ENOSPC, EIO, fsync failure), corrupt retained records, and the closed-generation RuntimeError from item 1 all come back as the final `refused` state. A retry might succeed, and a corrupt journal looks like a policy decision. Confirmed.
4. **Shutdown can strand live sessions** (`src/heterodyne/sandbox/runtime.py:342-351`). `close_manager_control()` closes every server, worker sessions included. If any handler is still active it raises before `self.servers.clear()`. The closed servers stay in `self.servers`, so `_rebind` skips them and those sessions stay unserved until the process restarts. Confirmed.

### Non-blocking

5. **The client doesn't retry on journal-busy** (`src/heterodyne/session/shim.py:419-428`). The server's retryable `{state:"pending", error:"control-journal-busy-retry"}` reply becomes a final exit 1. `_ask` only retries transport failures, so the lock-contention repair is retryable on the wire but nothing actually retries it.
6. **The framing refusal gives a misleading error.** Refusing padded or multiline input is an approved option, but the trailing newline from `echo`, `printf '\n'` or a heredoc comes back as `operator-route-required`. That points the manager at an operator instead of at its framing.
7. **Too much work under the lock** (`control.py:190`). `_pending(reap=True)` re-reads and SHA-256-hashes up to about 100 × 64 KiB records while holding the 1 s lock window, which makes contention more likely.
8. **Dead branch** (`server.py:219`). `except UnicodeEncodeError` after `json.loads(bytes)` can't be reached, and its comment is misleading. The real guard is the `token.encode()` check at line 231.
9. **Duplicate code.** `_write`/`_sync_dir` repeat the existing atomic-write helpers in `agents/base.py` and `harness/verify.py`. The literal `64 * 1024` appears four times instead of a shared constant. This matters because item 2's temp-file cleanup fix would have to be made in every copy.
10. **Not wired into production** (`runtime.py:336`). `own_manager_control`, `close_manager_control` and `observe_session` are only called from tests, so `SessionServer.control` is always `None` outside tests. This fits the approved source-only scope (no enrollment or deployment), so I'm noting it, not blocking on it. It does mean nothing outside tests exercises the transport.

### Against the R1 dispositions

- **Looks addressed:** legacy hook compatibility (strict parsing only on the control schema), the strict control-only parser, preserving exact raw bytes (or explicitly refusing unsupported framing), and the bounded lock wait (on the server side).
- **Not fully closed:**
  - closed-event checks: item 1, which is now an acknowledgement-ordering bug
  - owner-safe shutdown and rebind: item 4
  - typed failures: item 3
  - crash-safe durable replay: item 2

As you said, the evidence on file (448 + 92 qualified tests, the earlier full suite with 5 failures, quality exit 2) isn't full-suite or quality clearance. Items 1–4 need source repairs and a round 3.
