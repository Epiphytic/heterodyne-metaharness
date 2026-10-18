**Verdict: REVISE.** Finding 1 is a critical flaw that is not covered by the accepted limitations. As written, the change makes every ordinary approval or denial that is in flight when admind restarts get stuck permanently, not just maintenance holds. Findings 2 and 3 repeat limitations that were already disclosed, but they matter in practice. Findings 4 to 10 are minor or cleanup. Reviewed range `35176c85..17010182`, read-only; no tests were run.

10 findings, most severe first:

1. `src/heterodyne/admind/store.py:1063`: issue_decision stamps outcome='maintenance-issuing' before every decision child, including ordinary decisions that never involve maintenance. reconcile_one (daemon.py:1535) then sends any stranded attempt to reconcile_issuing, which only settles a 'recorded' read-back and holds 'untouched' and 'blocked' forever. This silently replaces the R26 restart reconcile for all decisions.

   Failure scenario: The operator sends !approve. Admind is restarted (deploy or systemd stop) while ab.decide is running; CancelledError leaves the ask 'deciding', as the comment at run_attempt's except says, 'the startup reconcile settles it (R26)'. On restart the read-back is 'untouched', or 'blocked' if a native decision was made meanwhile. Before this change the ask went back to open with RECONCILE_RESTARTED, or settled as blocked. Now reconcile_issuing posts 'issuance remains unverified... recovery is not available' and the ask stays 'deciding' forever. It counts toward MAX_OPEN=20, the bead returns ASK_BEAD_BUSY, cancel_ask refuses it, and every !asks starts another read-back child. The existing restart tests seed 'deciding' rows with no outcome and are needs_tmux, so they don't catch this.

2. `src/heterodyne/admind/daemon.py:1606`: Preflight refusals that cannot change the native decision (changed-card/stale, not-open, not-approval, not-approver, gate) are suspended permanently in MAINTENANCE_SUSPENDED. The normal path already settles these safely through close_refused or refresh(), which rejects nothing natively and posts a fresh card.

   Failure scenario: During a maintenance window the bead's content is edited, or btq's gate policy changes. On retry, preflight returns 'stale' or 'gate', and hold_maintenance marks the ask 'maintenance-held:changed-card' or ':gate'. The ask stays 'deciding' with no recovery command, the bead can never be posted again (ASK_BEAD_BUSY), and about 20 such holds exhaust MAX_OPEN so every new ask gets ASK_TOO_MANY. The non-maintenance path would instead have closed the attempt and refreshed the card.

3. `src/heterodyne/admind/daemon.py:1591`: maintenance_authority_reason treats 'latched' and 'group-unverified' as temporary holds that resume on their own. The normal path drops a decision on a latch through deny_rows ('a latch is final'), so an old consent is re-issued after a rearm with no reconfirmation.

   Failure scenario: The operator approves during maintenance and gets HOLD. A membership anomaly then latches admind. Days later the operator runs rearm, the next 30-second retry finds authority, digest and native state unchanged, and issues the stale approval without anyone confirming it again. The same message arriving fresh during the latch would have been dropped.

4. `src/heterodyne/admind/daemon.py:1360`: The comment on the CancelledError branch ('left deciding; the startup reconcile settles it (R26)') is now false once issue_decision has committed. Settlement depends on whether cancellation came before or after issue_decision, and nothing documents or tests the after case.

   Failure scenario: Cancellation during ab.read (before issue_decision) still settles to open on restart, which test_cancel_during_preflight_left_for_reconcile covers. Cancellation one await later, during ab.decide, is held forever (finding 1). A maintainer who trusts the comment ships a restart regression without noticing.

5. `src/heterodyne/admind/daemon.py:1611`: reconcile_issuing runs again, with a native read-back child, for every stranded issuing ask on every !asks backstop pass and every startup, and has no terminal state, so the work never stops while the hold never resolves.

   Failure scenario: Several ordinary decisions are stranded by restarts (finding 1). Each !asks command starts N approve-bead read-backs under work_lock, holding up the operator's message, and none of them can ever settle the ask unless the native state shows an exact 'recorded'.

6. `src/heterodyne/admind/daemon.py:1351`: On the non-retry path, when issue_decision returns False, the code calls conflict() and returns without finishing the inbound message. The operator's message stays 'executing' with no reply, even though the 'deciding' audit has already been written.

   Failure scenario: The compare-and-set in issue_decision fails because the ask or attempt changed under it, for example a concurrent writer or a store anomaly. The operator gets no reply, and the inbound message is only settled at the next restart's recover().

7. `src/heterodyne/admind/store.py:1018`: The overlay state machine ('maintenance-issuing', 'pending-maintenance', 'maintenance-held:<reason>') is stored in the user-facing asks.outcome column instead of the new ask_maintenance overlay table, and issue_decision does not update updated_at.

   Failure scenario: The poster agent's ask status/view (asks.summary) and the CLI's 'outcome:' line show internal tokens such as 'maintenance-issuing' for an ordinary decision in flight. Retry eligibility also depends on that free-text column, so any later code that writes outcome on a 'deciding' ask silently changes whether the ask is retried or suspended.

8. `src/heterodyne/admind/daemon.py:1660`: A retry does the authority check and preflight twice: retry_maintenance calls maintenance_authority_reason and preflight, then run_attempt(prechecked=back) repeats both, so policy files are reloaded twice for every retry.

   Failure scenario: Every due retry does redundant policy and group I/O under work_lock. The duplicated stop-to-reason mapping in two places also adds to the run_attempt complexity growth (47 to 51) that the quality gate already flags.

9. `src/heterodyne/admind/store.py:1066`: issue_maintenance_retry is a 'compatibility seam' that only tests call; production code always calls issue_decision(maintenance_retry=True) directly.

   Failure scenario: This is dead production API surface whose only purpose is to keep tests passing, and it adds to the dead-code and quality findings that are still unwaived.

10. `src/heterodyne/admind/approvals.py:120`: maintenance_hold reimplements a JSON object_pairs_hook that rejects duplicate keys, which already exists as actions/codec._pairs (together with _reject_constant).

    Failure scenario: There are two strict-JSON helpers that can drift apart, and this one does not reject NaN or Infinity constants the way codec does (harmless here only because the exact dict comparison fails afterwards).
