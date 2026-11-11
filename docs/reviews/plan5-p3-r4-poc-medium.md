**Verdict: good enough to build as an offline PoC.** I found no critical offline blocker in `b436af6f..95a95410`.

Both fixes that R3 required before Q1 pair acceptance look correct in the code:
- **Reducer preflight (R3 #1):** P1 `settle_request` now runs in `prepare` (`filing.py:461`) and again in `_issue` (`filing.py:571`), before any port call.
- **Owner lock (R3 #2):** the owner lock is now a separate `.owner.lock` file that is never unlinked. The code no longer opens or closes a descriptor to the SQLite database file for locking.
- **Other R3 repairs:** the URI path is percent-encoded (`filing.py:299`). The guard that stops a live callback from calling `mark_unknown` holds for same-thread callbacks (`filing.py:600-605`).
- **Test edits:** the three test edits only add the typed `CancellationRecovery` that P1 already requires once the preflight runs. They do not weaken anything.

What still blocks completion is outside the code, as you said: the fresh signed reciprocal product/Q1 pair, the five live ports, and the unwaived Quality11 gates. This was read-only. I ran no tests or probes, so none of the findings below is reproduced.

10 findings, most severe first:

1. `src/heterodyne/actions/filing.py:251`: The lock-file identity check only applies within one FilingJournal instance (_lock_identity starts as None), so a new instance or process accepts whatever inode is at the .owner.lock path, and a replaced sidecar lets two owners hold the lease at the same time.

   Failure scenario: Process A is inside execute() holding flock on .owner.lock inode X. The path is unlinked and recreated as inode Y (operator cleanup or a tmp reaper; same-UID boundary). Process B runs FilingJournal.open(): it locks Y without contention, passes _check_lock, and normalizes A's live 'issuing' command to 'uncertain'. B can then prepare_cleanup and issue a withdrawal while A's effect is still in flight, which is the R2 #1 hazard again. Fix: store the lock inode identity in the journal at create(), or recheck that the path's inode equals the fd's inode on release and refuse if it changed.

2. `src/heterodyne/actions/filing.py:900`: Cleanup.decision and Cleanup.dispatch are supplied by the caller and passed straight into settle_request; P3 never checks them against a committed decision or dispatch record, although P1's comment says the durable journal does that in the same transaction.

   Failure scenario: A caller builds Decision(phase='committed', event=deny, ...) or Dispatch(settled=True, cleanup_proven=True, ...) with matching approval, digest and revision. prepare_cleanup and the preflight accept it, and the request settles to 'denied' or 'completed' with only the fixture port's reducer_request echo behind it. Nothing durable proves the denial was committed or the dispatch settled. This should be disclosed as fixture-only alongside OPEN #4, or checked against a durable record.

3. `src/heterodyne/actions/filing.py:534`: _preflight_cleanup lets r.Refused out of prepare and _issue, while every other refusal in this owner raises Held; callers following the module's Held contract will not catch it.

   Failure scenario: A caller wraps prepare_cleanup or execute in `except Held:` to retry or yield. An ineligible withdrawal (for example allocation-unknown with lookups below 3) raises requests.Refused('request-abandonment-not-authenticated'), which escapes the handler and crashes the caller. The R3 test has to accept both types: `pytest.raises((r.Refused, Held))`. Wrap the call and re-raise as Held('cleanup-reducer-refused').

4. `src/heterodyne/actions/filing.py:547`: Public issue() still commits 'issuing' and releases the lease, so an effect can be in flight with nothing owning it (disclosed PARTIAL_OPEN #3).

   Failure scenario: Code calls journal.issue(id, current) and then ports.invoke itself. During that call another process's open() takes the lease, normalizes the command to 'uncertain', and prepare_cleanup issues a withdrawal while the effect is still in flight. This is acceptable only while no live adapter exists. Making issue() private, or refusing it unless the command is in _live, removes the hazard.

5. `src/heterodyne/actions/filing.py:978`: prepare_groom reads the projection, advice and request outside the transaction, then commits prepare() and the groom_plans insert in two separate transactions (disclosed OPEN #5).

   Failure scenario: If the process crashes between the two commits, a prepared groom has no groom_plans row; _issue refuses it with 'groom-owner-required' and the request stays held. If the request changes between the read and prepare(), r.groom(old) no longer equals plan.after after the effect, and the issued groom stays uncertain. Inserting groom_plans inside prepare's own transaction (pass the plan in) closes the crash gap cheaply.

6. `src/heterodyne/actions/filing.py:260`: flock locks are shared with forked children; O_CLOEXEC only closes the descriptor on exec, so a port or provider that forks without exec keeps the owner lease alive after the parent dies.

   Failure scenario: A fixture or provider uses multiprocessing with the fork start method, or os.fork, during invoke. The parent crashes, but the child still holds the lock description, so every FilingJournal.open() fails with journal-owner-busy until the child exits, and the interrupted 'issuing' command cannot be normalized.

7. `src/heterodyne/actions/filing.py:248`: _check_lock requires the mode to be exactly 0o600, but the sidecar is created with O_CREAT under the process umask, so a restrictive umask creates a lock file that is refused permanently.

   Failure scenario: With umask 0o277 the sidecar is created as 0o400. `st_mode & 0o777 != 0o600` refuses it, and later O_RDWR opens also fail. Every ownership() call raises journal-lock-refused and the journal cannot be used until someone fixes the file by hand. Call fchmod(fd, 0o600) after an exclusive create, or check that mode & 0o077 == 0.

8. `tests/test_actions_filing_r1.py:38`: The same ten-line CancellationRecovery fixture is copied three times (r1:38, r1:166, r2:108).

   Failure scenario: Any change to the CancellationRecovery fields or authentication flags must be made in three places; if they drift, the authenticated-cancellation fixtures stop matching each other. Add one helper next to allocated() in test_actions_filing_r2 or in test_actions_p1.

9. `src/heterodyne/actions/filing.py:478`: The 32-character lowercase hex nonce check is duplicated here and in judgement.assess (judgement.py:372).

   Failure scenario: The P3 and P2 nonce rules can drift: if one side changes, prepare() can persist a command id that j.assess then refuses with judgement-nonce-refused after the provider command already exists. Share one validator in judgement or codec.

10. `src/heterodyne/actions/filing.py:439`: The allowed-operation set at line 419 repeats the keys of the phases dict, and the sidecar path is rebuilt in both _check_lock and _open_lock (OPEN_MAINTENANCE #8 is still there).

    Failure scenario: Adding an operation to one list but not the other gives a KeyError instead of Held. Changing the lock path in only one helper makes every lease attempt fail the identity check. Derive the set from phases.keys() and compute the lock path once in __init__.
