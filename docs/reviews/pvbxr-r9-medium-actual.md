I found no critical flaws. As an offline, source-only PoC this is good enough to implement. It is not cleared for production or deployment.

**The narrow fix (a3ad157).** It is correct and doesn't make anything worse.
- **Behaviour:** `poll(POLLIN, 0)` gives the same answer as the old `select` for an idle listener and for one with a queued connection.
- **Error states:** if the listener socket is in an error state (POLLERR/POLLHUP/POLLNVAL), poll still reports it as busy, so dispatch stays held, the same as before.
- **Closed socket:** a closed socket (fileno -1) still raises ValueError, and that is still caught and treated as "held".
- **Interrupted calls:** Python retries an interrupted `poll()` (EINTR) itself, so the existing OSError handling is unchanged.
- **Older behaviour:** before this fix, a listener at descriptor 1024 or above held dispatch forever but never caused a wrong paste. The fix restores normal operation there; it was not a safety bug.

**The whole composition.** Between 02ea7a0 and a3ad157 the only source change is `hook.py` plus one new test file. The rest of the composition is byte-identical to what R8 reviewed, so all R8 findings carry forward unchanged. None of them is critical for an offline PoC. The native V2 lifecycle, reminder, cancellation, recovery, older-writer and deployment holds all remain HOLD.

**Limitations.** This was a read-only inspection: I ran no tests, probes, services or queue, native-DB or network actions. I did not re-verify the reported test counts (141/194/264/413), and no full-suite pass exists for this source. One hook test case is order-dependent (finding 5). I only spot-checked that the R8 findings still apply; I did not re-derive them. Nothing here is an APPROVE token.

10 findings, most severe first:

1. `src/heterodyne/admind/daemon.py:1206`: Carried from R8 #1, still OPEN: a card with an old V1 receipt that later gains V2 metadata stays protected and counted against MAX_OPEN, and no cancel, answer, retire, fulfil or duplicate path can clear it.

   Failure scenario: A card is fulfilled under V1, then a writer sets management='{}'. capacity_count() permanently goes up by 1. cancel_ask refuses because the card is protected, and apply/confirm raise unsupported-management-fulfillment. Twenty such cards make post_ask (daemon.py:1008/1110) refuse every new ask.

2. `src/heterodyne/admind/fulfillment.py:332`: Carried from R8 #2, still OPEN: answered V2 management cards can never be fulfilled, so they stay in the active set until someone cancels them by hand.

   Failure scenario: Answered operation-consent cards pile up in capacity_asks and !asks after their operation completes. Once capacity_count() >= MAX_OPEN, new asks are refused until the operator cancels the cards one by one.

3. `src/heterodyne/admind/fulfillment_projection.py:25`: Carried from R8 #5, still OPEN as a deployment blocker: the refusal of V2 cards exists only in Python. The scheme, DDL and triggers are unchanged, so a pre-repair (8effbd4) process on the same database still validates the projection and will fulfil and suppress V2 rows.

   Failure scenario: During a mixed-version rollout or a rollback, an older daemon or tool applies a receipt to a V2 management card. Its SUPPRESSED has no metadata check, so the card disappears, and nothing in SQLite refuses the old writer.

4. `src/heterodyne/admind/fulfillment_projection.py:215`: Carried from R8 #3, still OPEN: suppression is computed from the current NULL state of the metadata, and the fq2_asks_update trigger does not watch those columns. Setting the metadata back to NULL silently brings back the old 'valid' stamp.

   Failure scenario: A receipt is valid at generation G. A writer sets management=X, which reactivates the card without changing the generation. Setting management=NULL again makes the card suppressed under the stale stamp, and no verify pass runs.

5. `tests/test_admind_hook_high_fd.py:20`: The high-descriptor regression silently skips under the common default soft limit of 1024 and never raises the limit itself. Whether the 1024 and 1100 cases run depends on another test module (test_tmux_launch_lock.py:55 sets the limit to 2048 when it is imported) or on the host's ulimit.

   Failure scenario: Run alone, or in a CI shard without test_tmux_launch_lock, with ulimit -n 1024: both cases skip and the suite passes. Reverting to select.select would then go unnoticed. In this shell the soft limit is 1030, so the 1100 case skips. Fix: raise the soft limit toward the hard limit inside the test, as test_tmux_launch_lock does.

6. `src/heterodyne/harness/fetch.py:421`: The same descriptor-limit defect is still present elsewhere: fetch._stream uses select.select([src], ...), which raises ValueError for descriptors at or above 1024, and nothing catches it.

   Failure scenario: In a busy process whose pipe or file descriptor number reaches 1024 or more, _stream raises an uncaught ValueError instead of streaming or refusing with FetchRefused, the same class of failure a3ad157 fixed only in the hook server.

7. `src/heterodyne/admind/hook.py:543`: The fix treats the select() limit, but not why the process reaches 1024 or more descriptors: the commit itself names a cumulative test process. The full combined run still fails with a timeout, so a possible descriptor leak is unresolved.

   Failure scenario: If the full-suite timeout comes from descriptors leaking across tests or daemon restarts, switching to poll only moves the failure further out. Each process keeps leaking until it hits EMFILE: accept fails and backs off, and dispatch stays held. No test checks that the descriptor count stays bounded.

8. `src/heterodyne/admind/fulfillment.py:898`: Carried from R8 #4, still OPEN: if metadata is written after propose, confirm() raises inside its transaction, the rollback leaves the intent 'pending', and the operator's confirm notice stays live until it expires.

   Failure scenario: Propose on a V1 card, deliver the notice, then write metadata. Every !fulfill confirm returns unsupported-management-fulfillment, and the intent sits in view()['pending'] for 24 hours.

9. `src/heterodyne/admind/store.py:939`: Carried from R8 #9 and #10, still OPEN: remindable_asks excludes cards using a raw receipt check rather than the active projection, and the ask list combines two separate read snapshots.

   Failure scenario: A legacy-receipt card that gained metadata is listed as active but is never reminded. A verify_pass commit between the capacity_asks() and inactive_asks(20) reads can list a card twice or not at all.

10. `src/heterodyne/admind/hook.py:545`: Minor cleanup: the inline poll-with-zero-timeout check repeats the pattern already in sandbox/channel.py:alive() and codex_usage.py:130. A shared readable(fd) helper would remove the copies, and the remaining select.select call sites could use it too.

    Failure scenario: Each copy has to get the descriptor-limit and error-event semantics right separately. fetch.py already diverged and kept select.
