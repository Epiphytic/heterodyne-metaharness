# Q0 actual R3: carry-forward admission dispositions

Author: Codex. These are future C1/Q1 obligations, NOT a new Q0 code review or
claims of implemented fixes. Actual reviewer: APPROVE as an offline PoC, ten
retained findings; original ordinal3 stays unchanged. Root supplied the completed
review during the same sole HIGH design incorporation.

Raw SHA256: `318447181e9617b615a594fb5e5b86335bf08dbf3053b57005770f6cf0c154ea`.

## Finding 1 — verbatim

1. `btq_actions/core.py:495`: The 'three readback observations' go to the pinned `report` reducer, not the pinned `reconcile_lookup` reducer (which is absent from the Contracts port). `reconciliation_lookups` therefore never advances, so `settle_dispatch` can never succeed for a consumed claim, and the 1/5/30 lookup schedule is bypassed.

   Failure scenario: Claim consumed, then Report(effect='absent', evidence, cleanup_proven=True), so the dispatch is state=failed and not settled. Even a fully authenticated, exactly scoped DispatchRecovery is refused ('dispatch-recovery-not-bound') because reconciliation_lookups != 3, and no core operation increments it. The same applies to any consumed 'unknown' report. The row holds per-bead (cap 2) and global capacity forever with no source path out, even though a pinned reducer exists. This is a wiring gap, not a missing contract, and it is not disclosed.

**Admission disposition:** Q1 mandatory: expose and wire existing dd38 reconcile_lookup with journal-sampled schedule and durable actual counters; C1 verifies parity. Distinct from approved-undispatched missing settlement. No missing-contract rebuttal.

## Finding 2 — verbatim

2. `btq_actions/core.py:210`: On the post-lost-acknowledgement path, lookup turns a malformed backend reply (non-tuple, or a non-Row entry) into a definite Refused. This contradicts the R2 finding-7 disposition that a malformed post-allocation reply is Uncertain and never a retryable absence.

   Failure scenario: allocate commits, then the backend raises Uncertain. The caller does the mandated lookup and the backend returns None or ({},). Core raises Refused('allocation-lookup-not-unique' / 'allocation-scope-mismatch'), the same code an empty result gets, so the caller sees a definite absence for a row that exists. tests/test_plan5_r2.py:130-133 asserts Refused in exactly this state, after a known committed allocation.

**Admission disposition:** Q1 mandatory: malformed lost-ack lookup is Uncertain, not definite refusal; replace the incorrect oracle and prove no recreate before Q1/P3 acceptance.

## Finding 3 — verbatim

3. `btq_actions/core.py:302`: There is no abandon/withdraw/expiry path for shell, draft or filed rows. Any decision the pinned reducer refuses (for example an execution denial with a newline) leaves the row undecided and holding capacity permanently.

   Failure scenario: The operator denies an execution request with 'Too broad.\nScope to X'. The pinned begin_decision raises 'decision-reason-unsafe', so the row stays 'filed'. Two such rows, or two drafts left by a crashed agent, permanently block allocation for that bead (sum(bead)>=2) with no core operation to release them. This is disclosed only as a 'typed refusal', not as a permanent capacity hold.

**Admission disposition:** Q1 proposed whole-request cleanup interface is defined in designsection3.1, with exact P1 terminal/recovery proof. No timer expiry or fabricated Dispatch.

## Finding 4 — verbatim

4. `btq_actions/core.py:626`: The metadata allowlist admits only U+200D among format characters, so emoji tag sequences (U+E0020-E007F, category Cf) and CRLF line endings still make the whole authenticated decision refuse.

   Failure scenario: Reaction '🏴󠁧󠁢󠁳󠁣󠁴󠁿' (Scotland flag: tag characters) or a design denial 'No.\r\nRescope' from a CRLF client raises Refused('invalid-text'). decide() never commits, the first authenticated decision is lost and the row stays 'filed'. Same class as R2 finding 4/5, only partly repaired.

**Admission disposition:** C1/Q1 required: preserve exact tag-emoji/CRLF metadata while identifiers remain strict; execution multiline requires actual P1 codec compatibility before supported acceptance, not normalization.

## Finding 5 — verbatim

5. `btq_actions/core.py:614`: The nonblank-denial check uses str.strip(), which does not strip ZWJ, so an invisible denial reason passes, and _metadata_text then explicitly allows it.

   Failure scenario: Event(value='deny', denial_reason='\u200d') passes _event and is stored as the binding first decision event. Audit and native surfaces show a deny with an empty-looking reason, defeating the 'nonblank bounded reason' requirement.

**Admission disposition:** C1/Q1 required: reject wholly invisible denial without rejecting legitimate visible joined emoji; keep approve reason empty.

## Finding 6 — verbatim

6. `btq_actions/core.py:529`: _reducer converts every ValueError raised by a contract port into Refused, including adapter programming errors (unpacking errors, UnicodeDecodeError, int() parse bugs). This contradicts the disposition's claim that programming exceptions are not masked, and it drops the typed reason code.

   Failure scenario: A contracts adapter bug such as 'a, b = facts' on a 3-tuple raises ValueError('too many values to unpack'). The caller gets Refused('too many values to unpack') and treats it as a definite policy refusal of the decision or dispatch, instead of seeing the adapter defect.

**Admission disposition:** Q1 required: narrow real typed reducer exception translation and preserve fixed codes; arbitrary programming ValueError is not proven policy refusal.

## Finding 7 — verbatim

7. `btq_actions/core.py:230`: Public read classifies a malformed (non-Row) backend reply as Refused('invalid-row-readback'), while the allocate and _write readbacks classify the same malformed reply as Uncertain, so the same backend defect gets inconsistent typing.

   Failure scenario: The backend returns a dict for read(). Queue.read and every _observed-based write report a definite refusal, while the identical reply in _write's post-CAS readback is Uncertain('readback-mismatch'). Callers that key retry or inspect behaviour on the type get different answers for one adapter fault.

**Admission disposition:** Q1 required: malformed backend read consistently Uncertain; validate Row type before access.

## Finding 8 — verbatim

8. `docs/plan5-offline-queue.md:115`: The doc is stale after the R2 repair. It still says event strings reject control/format characters (metadata now admits ZWJ, \n and \t), and the documented test command at line 86 omits test_plan5_r2, so 13 of the 63 claimed tests are not run by the documented command.

   Failure scenario: A next-stage author (voe5f) or reviewer runs the documented command and gets 50 tests, silently skipping every R2 regression (terminal guard, success immutability, ZWJ/multiline), and relies on a text contract the code no longer enforces.

**Admission disposition:** Q1 required: accurate metadata contract and exact named current regression command/counts.

## Finding 9 — verbatim

9. `btq_actions/core.py:616`: Dead condition: once approve with a non-empty reason has been refused, 'event.value == "deny" or event.denial_reason' reduces to 'event.value == "deny"'.

   Failure scenario: Maintenance cost: the redundant disjunct suggests a third case (non-deny with a reason) that can no longer occur, which invites misreading of the approve/deny binding.

**Admission disposition:** Q1 bounded cleanup: remove dead branch preserving approve/deny tests.

## Finding 10 — verbatim

10. `btq_actions/core.py:539`: _preserve_success re-implements checks the pinned report reducer already enforces ('retained-report-evidence-conflict', 'settled-report-conflict'), adding a second success-identity rule set that can drift from the pinned source.

    Failure scenario: If the pinned reducer's monotonic-evidence rules change in the voe5f source bump, core's copy may refuse or allow different sequences, so a test passing against one rule set hides a divergence. A single source-owned check plus core's pre-reducer guard on succeeded-to-downgrade would be enough.

**Admission disposition:** C1/Q1 source-owned predicate reuse and parity tests; minimal core downgrade guard only, no diverging success policy.
