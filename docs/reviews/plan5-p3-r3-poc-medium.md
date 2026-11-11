**Verdict:** no critical flaw found. The P3 implementation is good enough to build as an offline PoC. Findings 1 and 2 below should still be fixed before the Q1 pair is accepted. Finding 1 fails closed: the request stays held and keeps its capacity, so it is a stuck request after a real effect rather than a safety break. Finding 2 only applies when two journal instances run on separate threads in one process; the single-thread tests do not do this.

What holds up across the whole range:
- No replay of issued or uncertain commands.
- The owner lease covers the real port call in `execute`, the provider `await` in `assess`, and the result commit; another connection cannot normalize a live call on open.
- Provider readback through the generic paths is refused.
- The 161 / 128 / three-attempt arithmetic checks out at the 124 boundary.

Disclosed items for future production work, not PoC blockers:
- All five live adapters are unavailable.
- The refreshed signed Q1 pair is still OPEN. It is required before completion, not before the PoC.
- Quality debt is unwaived.
- History is never evicted.
- A hung owner leaves the journal busy.

This was read-only: I ran no tests or probes, so none of these findings has been reproduced.

8 findings, most severe first:

1. `src/heterodyne/actions/filing.py:394`: A withdrawal is issued to the native port before anyone checks that P1 settle_request can accept it. Only the Cleanup shape is checked first, so a withdrawal the reducer must refuse becomes an uncertain withdraw after the native effect has already happened. The new withdrawal fence then makes that permanent.

   Failure scenario: An allocate is uncertain with allocation_lookups=1 and the request is in allocation-unknown. prepare_cleanup(Cleanup('reconciled-abandoned', abandonment=AbandonmentRecovery(expected_version=v))) counts as recoverable and passes the shape check. execute() calls ports.invoke, which performs the native withdrawal. _reduce then calls settle_request, which raises Refused('request-abandonment-not-authenticated') because lookups != 3, and mark_unknown leaves W1 uncertain. _withdraw_fence now refuses reconcile(allocate), so lookups can never reach 3. W1 cannot be replaced (a pending withdraw blocks prepare) or abandoned (it is not prepared), so the request and its 161 slots plus per-bead slot are held forever. Cleanup('cancelled') from allocation-unknown, or 'denied' without a committed Decision, fails the same way. Fix: dry-run settle_request against the current request (cleaned_shells=shell_ids, blockers_clear/native_terminal=True) in prepare and in _issue before any port call.

2. `src/heterodyne/actions/filing.py:222`: The owner lease opens and os.close()s its own file descriptor on the SQLite database file itself. On POSIX, closing any descriptor to a file drops every fcntl lock the process holds on it, including locks SQLite holds for other connections in the same process.

   Failure scenario: Thread 1 (journal A) is inside BEGIN IMMEDIATE...COMMIT for a result and holds a RESERVED or EXCLUSIVE fcntl lock. Thread 2 calls FilingJournal.open on the same path: ownership() opens a new descriptor, flock fails, and os.close(fd) runs (the same happens on the normal release at line 235). The kernel drops A's SQLite locks mid-commit, so another process can read a half-written rollback-mode database or write at the same time, and the journal can be corrupted (SQLite 'How To Corrupt' §2.2). Use a separate lock file, or keep one descriptor open for the instance's lifetime.

3. `src/heterodyne/actions/filing.py:500`: The public issue() and mark_unknown() calls bypass the in-flight owner lease. issue() commits 'issuing' and then releases the lease, and mark_unknown() has no _live check, so nothing in the API ties the lease to the actual call.

   Failure scenario: A caller does journal.issue(id, current), as several tests do, and then calls the port outside execute(). The command is 'issuing' while no lease is held. A concurrent FilingJournal.open gets the flock and normalizes it to 'uncertain', or same-thread reconcile is allowed because the id is not in _live. prepare_cleanup then treats it as recoverable and issues a withdrawal while the effect is still in flight, which is the R2 #1 scenario again. A port callback in the same thread can also call mark_unknown(cmd) on a live command. Make issue/mark_unknown private (execute and DurableProvider only), or refuse when the id is not owned/live.

4. `src/heterodyne/actions/filing.py:399`: Recoverable withdrawal next to an uncertain blocker/park/land command accepts a Cleanup('cancelled') with no CancellationRecovery or AbandonmentRecovery. Cleanup of an unknown effect in those phases therefore rests only on fixture booleans, not on an authenticated cancellation.

   Failure scenario: test_withdraw_fences_older_reconciliation: the blocker command is uncertain and the request is 'allocated'. prepare_cleanup(Cleanup('cancelled')) succeeds without any recovery, and settle_request for allocated/blocker-pending/parked/landed requires none. An uncertain land whose repository effect may have landed can be settled to 'cancelled' with only blockers_clear/native_terminal. This contradicts the stated goal that orphan cleanup requires an authenticated actual cancellation; only the provider orphan in ask-pinned gets authenticated cleanup. If that goal is intended, require a typed recovery whenever the recoverable branch is taken.

5. `src/heterodyne/actions/filing.py:899`: prepare_groom reads the request, the projection and the receipt outside any transaction or lease, then commits prepare() and the groom_plans insert in two separate transactions. _issue only checks that the plan bytes equal the payload, never that plan.after was derived from command.request.

   Failure scenario: Between the `before` read and prepare()'s transaction, another process advances the request. prepare snapshots the new request, issue passes (the request equals the snapshot and the plan bytes match), and the port relaunches the session with the new source. _reduce's r.groom(old, ...) then no longer equals plan.after, so it raises groom-readback-held and the issued groom stays uncertain. Separately, a crash between the two commits leaves a self-inflicted HOLD that could be avoided by inserting groom_plans in the same transaction.

6. `src/heterodyne/actions/filing.py:957`: The single journal-wide nonblocking lease is held across the whole P2 await (up to the 60 s deadline) and across every ports.invoke/read. Any operation on any other request fails immediately with journal-owner-busy instead of waiting.

   Failure scenario: With 64 active requests, one slow provider.judge or park invoke makes every reserve, prepare, withdrawal and reconcile of other requests in every thread or process raise Held('journal-owner-busy') until it returns. Callers must retry in a busy loop, and cleanup of unrelated requests is blocked. A per-request (creation-key) lease plus a short journal lock for open-normalization would scope in-flight ownership at the right level.

7. `src/heterodyne/actions/filing.py:268`: open() builds the SQLite URI as f'file:{path}?mode=rw' without percent-encoding the path, so a path containing '?', '#' or '%' opens a different file from the one lstat() just validated.

   Failure scenario: A journal at /tmp/run#1/journal.db passes the lstat checks, but SQLite parses '#1/journal.db' as a fragment and opens /tmp/run (or fails). The schema check then runs against the wrong file, and the flock in ownership() (which uses PRAGMA database_list) locks a different inode than the validated one. Use path.as_uri() or urllib.parse.quote.

8. `src/heterodyne/actions/filing.py:836`: The withdraw Cleanup payload is strictly decoded three times (prepare, _issue, _reduce), and the allowed-operation set at line 388 duplicates the keys of the phases dict at line 410.

   Failure scenario: Adding an operation or changing Cleanup validation means editing parallel lists; if they drift, prepare can admit an operation that has no phases entry (KeyError instead of Held). Decode once in prepare, store the typed value, and derive the operation set from phases.keys().
