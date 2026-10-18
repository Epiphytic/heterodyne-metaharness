Verdict: **REVISE**. The repairs since `0b0c394` close the specific failure scenarios that R1 described. But the new recovery paths add their own wedge and race cases. Two are high severity: in-flight effects can be reclassified as uncertain, and a recoverable withdrawal can be stranded.

This was a read-only review of `b436af6..7eed86b`, concentrating on `0b0c394..7eed86b`. I read `filing.py` and `requests.py` at `7eed86b`, the changed part of `judgement.py`, the R1 dispositions and the R1 regression tests. Per the brief, I did not run tests or any probes, so none of these findings has been reproduced.

R1 dispositions #1–#10 are addressed for the scenarios R1 described. The duplicated pending SQL and nonce checks remain OPEN, as R1 #10 already says. The quality gates and the Q1/product pair acceptance remain open, and this review does not change either.

10 findings, most severe first:

1. `src/heterodyne/actions/filing.py:235`: Any open() of the journal, or a reconcile()/observe() of an 'issuing' command in the same process, turns a command whose effect is still in flight into 'uncertain'. That makes withdrawal 'recoverable' during the live effect and makes DurableProvider throw away a real reply.

   Failure scenario: Process A is inside ports.invoke(park) or provider.judge, so the command is 'issuing'. Process B calls FilingJournal.open() on the same file (timeout=1 shows several connections are expected). The open runs an unconditional UPDATE ... SET state='uncertain' WHERE state='issuing', with no transaction or owner/liveness check. The same flip happens if anyone calls reconcile() on the issuing command and the read raises (mark_unknown at line 561), or if observe() gets an 'uncertain' readback. prepare_cleanup then sees only a non-withdraw uncertain row, treats it as recoverable, and issues a withdrawal while the park is still being executed. For a provider command, the result CAS at line 1068 needs state='issuing', so it fails with provider-result-cas-held and the actual reply is lost. Also, if the adapter then raises BeforeIssue, the check at line 505 fails and a proven no-call stays uncertain. The design only treats 'issuing' as uncertain on restart, and open() cannot tell a restart from a concurrent open.

2. `src/heterodyne/actions/filing.py:351`: A withdrawal that is 'recoverable' next to an uncertain command does not lock that command. Reconciling the older command later moves the request to a new version, so an already-issued withdrawal can never be observed and blocks all cleanup permanently.

   Failure scenario: A park command is uncertain. prepare_cleanup creates W1, which is allowed because the only pending row is a non-withdraw uncertain one. execute(W1) issues the withdrawal and the acknowledgement is lost, so W1 is uncertain. Then reconcile(park) returns an 'observed' readback; _reduce/_save moves the request to 'parked' with version+1. reconcile(W1) now fails with observation-snapshot-refused because the request no longer equals W1's snapshot, so W1 stays uncertain forever. Every later prepare sees a pending withdraw and refuses it, abandon_prepared needs 'prepared', and the request with its 161 slots and per-bead slot is held for good. The same applies to an allocate reconcile (lookup consumption bumps the version) when the withdrawal carries AbandonmentRecovery(expected_version=v).

3. `src/heterodyne/actions/filing.py:380`: The four reserved cleanup slots can be used up by a single abandoned or BeforeIssue withdrawal, or by non-withdraw abandon/preissue events (all appended with terminal=True). After that, no later withdrawal can be prepared.

   Failure scenario: Ordinary traffic reaches lifecycle=124, which the docs and the boundary test allow. prepare_cleanup W1 brings it to 125. The adapter raises BeforeIssue (preissue-refusal, terminal, 127 after issuing at 126), or the operator calls abandon_prepared(W1) (126). Any new prepare_cleanup then fails the check used > 124 with terminal-lifecycle-reserved, so the request can never reach a terminal state. The documented 'four reserved slots' covers exactly one successful attempt and no retry.

4. `src/heterodyne/actions/filing.py:1068`: A provider command that crashed after its result was saved but before its Receipt is 'observed without assessment'. prepare counts it as pending and as not recoverable, and nothing can clear it, so the request can never be withdrawn.

   Failure scenario: The DurableProvider result CAS commits state='observed'. The process dies before assess() inserts the assessment row. After reopen, the pending query matches operation='provider' AND state='observed' AND NOT EXISTS assessment. recoverable needs every pending row to be 'uncertain', so prepare_cleanup raises command-pending-held. abandon_prepared needs 'prepared', reconcile needs issuing/uncertain, and assess cannot rebuild the Receipt. The design's 'explicit HOLD' therefore holds the request's capacity forever, with no authenticated cleanup path, unlike the uncertain case that R1 #3 fixed.

5. `src/heterodyne/actions/filing.py:505`: If anything inside the BeforeIssue handler raises (the state/data CAS, _save, or cancel_unissued_allocation's Refused), the sibling 'except BaseException' does not catch it. The command is left 'issuing' instead of 'uncertain' and blocks withdrawal until the journal is reopened.

   Failure scenario: An allocate is issuing. A reconcile in the same process consumes a lookup, which rewrites the command data and sets allocation_lookups=1. ports.invoke then raises BeforeIssue. The check self.command(id) != (command,'issuing') raises preissue-refusal-state-held, and if it did not, cancel_unissued_allocation would raise Refused because lookups is non-zero. Because the error is raised inside the except clause, mark_unknown never runs and the row stays 'issuing'. prepare_cleanup treats it as non-recoverable (it is not 'uncertain'), so cleanup is blocked until the process restarts and open() normalizes it.

6. `src/heterodyne/actions/filing.py:972`: The global 16384-event bound is checked only in reserve(). record_detail still appends events to terminal or inactive requests whose reservations have already been released, so the total event count can exceed MAX_EVENTS.

   Failure scenario: Many requests settle to a terminal state after using only about 10 events each. _save sets active=0, so reserve() stops counting their remaining reservation, and new requests reserve the freed space up to 16384. record_detail is still accepted for those terminal requests (it only checks that the request exists), adding up to 33 detail/overflow events each. That pushes retained events past MAX_EVENTS and breaks the guarantee that active requests' 161-slot reservations stay inside the finite bound.

7. `src/heterodyne/actions/filing.py:528`: issue/execute refuse provider commands, but the public reconcile() and observe() still accept them. A provider command can be passed to the mutation port's read and have its issuing state or stored result overwritten outside DurableProvider.

   Failure scenario: reconcile(provider_cmd) while it is issuing or uncertain calls ports.read(command) on a provider command. An 'uncertain' readback flips the state and overwrites commands.result with an Observation instead of a ProviderResult. While issuing, that makes the DurableProvider result CAS fail and drops the reply. This contradicts R1 #7's 'only DurableProvider may enter' disposition.

8. `src/heterodyne/actions/filing.py:47`: The new events_request index on events(k,kind) is never read, because counters replaced every per-request events query. Meanwhile reserve() still runs a full SELECT count(*) FROM events inside BEGIN IMMEDIATE.

   Failure scenario: Every append_event pays for an extra index write on a table of up to 16384 rows, and the index serves no query. reserve() still scans the whole events table while holding the writer lock. sum(events) FROM requests already holds the same total and would avoid the scan.

9. `src/heterodyne/actions/filing.py:268`: The lifecycle thresholds 116, 124 and 128 are bare literals duplicated between prepare() and append_event(), and the doc's 'ordinary traffic stops at 124' contradicts the 116 gate in prepare().

   Failure scenario: Changing the reserve means editing three or four literals in two methods, and nothing ties them to RESERVATION (161 = 128 + 33). Changing one without the other quietly breaks the cleanup reserve. Named constants derived from RESERVATION would prevent this.

10. `src/heterodyne/actions/filing.py:821`: The pending-command SQL is still copied verbatim between prepare() and _idle(), and create() still has the unreachable 'if ports is None' after _capability (R1 #10 leftovers, now with a third and fourth fetchall/fetchone variant).

    Failure scenario: The R1 #1 fix changed prepare to fetchall plus an all() predicate, but _idle keeps its own copy of the predicate. A future change to what counts as 'pending' (for example the observed-provider case) must be made twice, or pin/sufficient will disagree with prepare. A shared _pending(k) helper would remove the duplication.
