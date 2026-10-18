# Sole HIGH review: ordered author incorporation

Author: Codex. Actual reviewer returned REVISE10. This is one author incorporation,
not reviewer approval or a second review. Raw output is preserved byte-exact in
[the companion report](2026-10-18-plan5-next-design-r1-high.md).

Raw SHA256: `ea74a538324171cd47eb2f4035f13d566c7dddfbd5dcd95e319f18701af02662`.

## Finding 1 — verbatim

1. `docs/designs/2026-10-18-plan5-next-finite-stage.md:28`: The design still describes Q0 f804 as 'pending its second full HIGH review', and §3 and the inputs manifest pin f804's core.py interfaces (Queue/Backend/Contracts/typed errors); but that review (9guj8-code-review-r2-high.log) returned REVISE with 10 findings that the design neither records nor binds. §4 keeps a ledger only for P1/P2, so nothing preserves the Q0 R2 ordinals or requires Q1/P3/P4 to re-pin to the eventual accepted repaired Q0 source.

   Failure scenario: Q1 or P3 workers build against f804's Contracts/Queue as the 'conditional boundary'. The R2 #1 fix (a settle path for undispatched rows) and the #8 fix (typed reducer ports) change those signatures. Alternatively, f804 bugs get baked into the composed fixture: rows still writable after terminal (#3), Refused turned into Uncertain (#6), raw AttributeError/ValueError (#7/#8). In both cases the 'accepted Q0 source' is never defined against the R2 dispositions, and the human approves a design whose Q0 pin is already known to be REVISE. P2 has a similar gap: C1 is allowed to stack on 00ff while PR118 CI is pending, with no rule that C1 cannot be accepted or merged before P2 merges with green CI.

**Author disposition:** Corrected stale Q0 facts to actual R2 REVISE10 and repaired 7a631db candidate, with raw/disposition pins and eventual accepted exact pair requirement; P2 green CI+merge is mandatory before C1 acceptance/merge.

## Finding 2 — verbatim

2. `docs/designs/2026-10-18-plan5-next-finite-stage.md:50`: Q1's 'including terminal cleanup' is not defined, and Q0 has no operation that makes a shell, draft or filed row terminal (cancel, close as orphan, reconciled-abandoned, cannot-approve-as-written). Q0 sets terminal only on a finalize 'finish' or on dispatch settle/report. So the capacity release P3 requires has no native contract, and adding one would be a Q0 contract change that this design doesn't specify.

   Failure scenario: In P3-offline, a known unposted shell is cancelled, a twice-groomed request becomes cannot-approve-as-written, or an orphan shell is reconciled. The P1 journal reaches a terminal phase, but the Q0 row stays terminal=False in stage shell/draft/filed and still counts against the per-bead cap of 2 and global_cap. After two such rows the bead can never allocate again, and the 'shell/blocker cleanup' and 'cap exhaustion' tests (lines 203-205) can't be met without inventing a Queue stage mid-implementation. Under §2 line 66, that would need a new design decision.

**Author disposition:** Added proposed Queue.withdraw and Contracts.settle_request whole-row CAS contract, exact existing reducer cleanup predicates, idempotence/capacity rules and explicit approved-undispatched HOLD. No fabricated dispatch/absence.

## Finding 3 — verbatim

3. `docs/designs/2026-10-18-plan5-next-finite-stage.md:112`: The rule 'Only typed pre-effect Refused is a proven refusal' doesn't define which Refused counts. P1 requests.Refused, Q0 core.Refused and P2 judgement refusals are distinct ValueError subclasses. Q0 converts Refused into Uncertain on reads. Reducer refusals pass through Q0 Contracts calls as foreign exception types. And a CAS conflict Refused received after an earlier Uncertain proves nothing about that earlier write.

   Failure scenario: P4 issues decide(), the acknowledgement is lost (Uncertain), and on resume it retries with the stale Expected. Q0 raises Refused('conditional-conflict') because the first write actually landed. Under line 112 this counts as a proven refusal or failed attempt and may lead to HOLD or a re-evaluation as not written. In another case, a P1 reducer raises requests.Refused through Contracts.decide, and a caller keyed on core.Refused/Uncertain sees neither type. No mapping table (P1/P2/Q0 exception mapped to Refused, Uncertain or recovery-needed per call phase) is required of Q1 or C1.

**Author disposition:** Added command-level durable issuing/uncertain phases and boundary-specific typed exception mapping; a later stale CAS refusal cannot erase prior uncertainty.

## Finding 4 — verbatim

4. `docs/designs/2026-10-18-plan5-next-finite-stage.md:147`: The rule that fixture clock samples come only from the journal's injected sampler is not carried across the composed seams. Q0's Queue.prepare/ready/claim/restart take a caller-supplied `now: bytes`. P2's judgement.assess defaults to the real host CLOCK_BOOTTIME, the unattested 'fake-provider-unattested-boot' label and uuid4 nonces. The design never requires the Q1 bridge or P3 to replace these with the journal sampler and boot anchor.

   Failure scenario: P3/P4 call Queue.ready(now=caller_bytes) or judgement.assess() with default arguments. Readiness or deadlines are then judged on a caller- or host-supplied time with a fixture boot label that never matches the journal's anchor. That reintroduces the P1 R11 #1 forged-clock class at the Q0 and judgement boundaries even after C1 fixes FixtureJournal, and the fixture becomes nondeterministic, so tests for 'forged caller clocks cannot satisfy backoff/deadlines' pass at the journal level while the composed path stays unverified.

**Author disposition:** Required one injected journal sampler/boot/nonce source through every Q0/P2 timed seam; defaults prohibited in composed fixtures.

## Finding 5 — verbatim

5. `docs/designs/2026-10-18-plan5-next-finite-stage.md:109`: P3 must commit intent only before 'potentially mutating' port calls. A sufficiency or permission provider call is not mutating, but P2 judgement.assess says its production owner 'must persist nonce+input+boot+start BEFORE invoking its adapter' and treats serialized receipts as forgeable. P3-offline is that owner, and the design never assigns the obligation.

   Failure scenario: P3 calls assess() with no persisted attempt, crashes after the provider replies, and on restart either re-invokes the provider (a second judgement under a new nonce, which can pick the more favourable result) or accepts a caller-supplied Receipt it never journaled as 'current sufficiency'. That breaks the requirement that sufficiency records are durable and bound to the exact digest, revision, policy, source and model.

**Author disposition:** Assigned provider-intent persistence to P3 before fake provider invocation, exact response adoption and uncertain-provider HOLD without reroll.

## Finding 6 — verbatim

6. `docs/designs/2026-10-18-plan5-next-finite-stage.md:143`: The capacity limits don't reconcile across components. P1 FixtureJournal uses MAX_OUTSTANDING=128 while Q0 Queue defaults to global_cap=64. The per-bead cap of 2 lives only inside the Q0 Backend implementation, not the Queue interface. And invalid or superseded inbox events are 'audited' with no per-approval cap against the shared 16384-event budget.

   Failure scenario: The journal durably admits request intent #65 (under 128), then Q0 allocate refuses with global capacity, leaving a durable intent with no defined disposition. Separately, a flood of distinct invalid or superseded synthetic Marmot events for one approval appends audit events until the fixture-wide event budget triggers 'fixture-event-capacity-needs-recovery'. That HOLDs every other request in the fixture, a denial of service across all requests from one approval's invalid traffic.

**Author disposition:** Reconciled common total64/per-bead2 with backend atomic enforcement, retained unissued-intent disposition and bounded per-approval audit slots/overflow independent of other requests.

## Finding 7 — verbatim

7. `docs/designs/2026-10-18-plan5-next-finite-stage.md:51`: P3-offline composes the native blocker, park/stop, D26 landing, same-session restricted grooming, shell cancellation and filed readback through 'injected ports', but no port protocol, signature, owning repository or typed outcome is defined. Q0's Backend offers only allocate/lookup/read/cas. D4 §5 requires the session seam to be proven 'against actual session APIs' without inventing a resume API.

   Failure scenario: The P3 worker invents BlockerPort/ParkPort/LandingPort/GroomPort shapes inside the product repo. P4 and the later real P3 (bikr3/T10/Plan5i sources) then expose different shapes, so P3-offline's crash matrix (line 202) proves only behaviour against self-defined fakes. Reviewers have no fixed contract to test against, and later integration must rewrite the coordinator, which works against the claim that this batch is finite and reusable.

**Author disposition:** Defined fixed proposed offline FilingPorts calls/bindings/outcomes with actual Parker and FakeSession source comparison. Real restricted grooming/landing remains an explicit unavailable compatibility gate.

## Finding 8 — verbatim

8. `docs/designs/2026-10-18-plan5-next-finite-stage.md:100`: The source-capsule contract can't be implemented as written. Its closure includes msgspec (a C extension), heterodyne.config.*, gatekeeper.project and contracts/codec, none of which are in the manifest. It sets no manifest format, location or signer. 'Verify then import' from paths leaves a TOCTOU window. And only the queue side verifies product source, while P3/P4 import queue code with no reciprocal pin.

   Failure scenario: Q1 hashes product .py files, then calls importlib on the same paths. A file swapped after hashing, or an unpinned msgspec .so or site-packages shadow, gets loaded anyway. Meanwhile P3 in the product repo imports btq_actions from whatever queue checkout is on sys.path, so the 'exact tested product/queue source pair' (line 51) is enforced in one direction only and acceptance can't prove which queue bytes ran.

**Author disposition:** Specified reciprocal signed-commit manifest, full Python+extension+runtime dependency closure, captured bytes and closed resolver. Disclosed same-UID consistency limit; no isolation claim.

## Finding 9 — verbatim

9. `docs/designs/2026-10-18-plan5-next-finite-stage.md:229`: The design changes the approved cb180 staged target graph (P3 requires btq-bikr3/1wu2i/d1v9d; P4 requires P3 and H1) and D4 §8's rule that 'implementation admission stops if ... queue/decision interfaces remain unresolved'. It does this by admitting P3-offline/P4-offline subsets while Q0 is REVISE. It doesn't surface this as an explicit graph/D4 §8 deviation for the human, or state that offline acceptance satisfies no P3/P4 graph edge.

   Failure scenario: The human approves 'four offline roles' without seeing that the H1 edge is dropped for P4-offline or that D4 §8's stop condition is being read as integration-only. Later the controller, or a reader of the closed P3-offline/P4-offline tasks, treats them as satisfying the graph's P3→P4 requirement and admits real P4 or cutover work without the original H1/scanner edges. That is the 'CLOSED bit as proof' failure the design warns about at line 232.

**Author disposition:** Made the graph/D4section8 deviation an explicit human approval choice, with offline acceptance satisfying NONE of original P3/P4/H1/scanner/T10/cutover edges.

## Finding 10 — verbatim

10. `docs/designs/2026-10-18-plan5-next-finite-stage.md:179`: The §4 ledger conflicts with itself. The table title says every one of the 18 rows is 'Required treatment before the affected consumer is accepted', yet line 179 allows '#4' and 'remaining bounded performance debt' to be recorded as noncritical limitations, and line 181 says 'Safety-affecting #1–3'. None of these references says which report (P1 R11 or P2 R4) is meant.

    Failure scenario: At C1 acceptance, one reviewer reads '#4' as P1 R11 #4 (UTC slew) and lets it be deferred. Another reads it as P2 R4 #4 (an unrelated permission subject getting a cross-request denial, which is a safety binding issue) and requires a fix. P1 R11 #5/#6 and P2 R4 #10 are mandatory per the table but deferrable per the paragraph, so C1's acceptance criteria are undecidable and the author's single incorporation pass can't be checked against a fixed set.

**Author disposition:** Disambiguated every report ordinal and exact permitted offline deferrals; P2R4#4 and all binding/safety findings remain mandatory.

## Additional cleanup

The inputs link now resolves from both the identical design and ADR copies via
`../designs/2026-10-18-plan5-next-finite-stage-inputs.json`. No runtime tests or
second design validation/review were performed. Document/hash checks only.
