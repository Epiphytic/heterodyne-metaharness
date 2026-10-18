**Verdict: REVISE.** The two worst problems are that hard deny loses to a stale receipt, and the groom budget can end in a state that has no exit. Scope was `dd38be7a..b12a171f` (`judgement.py`, `test_actions_p2.py`, the doc), checked against the amendment's P2 scope and D4 §3/§5. This was a static read only: no tests or probes were run, and no verify pass was done, so these are unverified candidates.

10 findings, most severe first:

1. `src/heterodyne/actions/judgement.py:291`: Hard deny does not always win: in current_projection the receipt-binding, kind and elapsed checks (and the catch-all except) run before the policy.widens_permissions -> DenyProposal check.

   Failure scenario: A permission receipt is made under PolicyFacts(rev, False). Host policy then becomes PolicyFacts(rev, True). Because policy is part of bind(), the old receipt's input_digest no longer matches, so current_projection returns EscalateProposal('stale_digest') instead of DenyProposal. The same happens with a malformed receipt, an out-of-range elapsed_ns, or any Refused/Fault raised in bind. An escalation card can then reach a human even though the action is hard-denied. D4 says no gatekeeper or operator-card route can override a hard deny.

2. `src/heterodyne/actions/judgement.py:354`: The P1 groom_revisions counter and GroomState can drift apart. Once they do, record_groom always raises groom-budget-refused and the request can never reach CannotApproveNotice or concern().

   Failure scenario: Revision 0 gets NeedsGrooming, giving state (0,(d0,)). The host runs P1.groom (revisions=1). The revision-1 judgement is unavailable (an outage), and P1.groom still allows a second groom from ask-pinned, so revisions=2. That outage still spent one of the two P1 revisions, which contradicts the doc's claim that outages do not. Revision 2 then gets NeedsGrooming, and the check action.groom_revisions(2) != len(assessed)(1) raises Refused. failures can never reach 2, so concern() can never run and the request is held with no fixed groom/deny route. P1.groom also allows grooming from the 'sufficient' phase without any recorded failure.

3. `src/heterodyne/actions/judgement.py:387`: concern() is not tied to the request whose budget was used up. GroomState has no CreationKey or approval_id, and concern() never checks that action.groom_revisions == 2 or that bind(action) == state.assessed[-1].

   Failure scenario: Request A in session S uses up both groom revisions, giving state (2, 3 digests). A fresh request B in the same logical session (new sequence, groom_revisions=0, never judged) is passed to concern(state, B, ...). It returns a CannotApprove-style Concern with B's digest prefix and recommends deny. D4 requires a fresh request to go through the full pipeline.

4. `src/heterodyne/actions/judgement.py:163`: The evaluate_policy() result is thrown away, and it is called with empty host_overrides/restrict. The P0 PolicyResult (hard_deny or outward-floor escalate) never reaches the projection, which hand-reads widens_permissions in two places instead.

   Failure scenario: For an outward action whose deterministic P0 tier is 'escalate' (the outward floor), current_projection returns a bare AllowProposal. Nothing in the returned value shows the floor. Any consumer that branches on the projection sees 'allow', and workstream restrictions are never checked. The doc's claim that an AllowProposal cannot defeat the P0 floor rests only on adapters being missing.

5. `src/heterodyne/actions/judgement.py:151`: For permission, bind only checks bead, session, workstream, revision and policy. PermissionRequest.intent, invocation and tool_name are never matched against action.envelope or action.intent.

   Failure scenario: Take a grey-zone permission request for a harmless invocation (for example a read-only tool) with the same bead, session and revision as an action Request whose envelope is a deploy or notify. assess and current_projection produce an AllowProposal bound to the deploy Request. The receipt certifies a judgement of a different effect.

6. `tests/test_actions_p2.py:296`: Re-binding after enrichment is not modelled. P1 has no transition that re-pins a new whole ask after an EnrichmentProposal: pin_ask requires phase 'landed', and groom charges a revision. The test fakes the state with structs.replace, and bind does not require ask_bytes/context_digest to change when enrichment is Used.

   Failure scenario: The host rebuilds the SufficiencyRequest with the enriched candidate (enrichment=Used) but keeps the old action.ask_bytes and context_digest, which is all P1 allows without grooming. bind accepts it, and a fresh judgement returns SufficientProposal for a native ask that never contained the enrichment. The 'enrich requires fresh complete ask' rule is not enforced. The only legitimate path, groom, uses up a revision, which contradicts 'enrichment is never charged'.

7. `src/heterodyne/actions/judgement.py:49`: A Receipt carries no proof that a judgement happened. input_digest is a public, deterministic bind() of the caller's inputs, there is no invocation nonce or clock-sample/boot record, and elapsed_ns is self-reported.

   Failure scenario: Any holder of the inputs can build Receipt(bind(...), 'sufficiency', 0, Reply(bind(...), SPEC, wire(sufficient judgment)), None) without calling a provider, and current_projection returns SufficientProposal. A future journal also cannot recheck the 60-second deadline from the stored fields. This needs at least an explicit future-owner contract (nonce, start/end samples, boot id).

8. `src/heterodyne/actions/judgement.py:251`: Timeout reasons are mislabelled. The asyncio timeout path stores elapsed_ns = budget (DEADLINE_NS for sufficiency), which then fails `0 <= elapsed_ns < DEADLINE_NS` and is reported as stale_digest. A clock regression (elapsed < 0) is reported as timeout.

   Failure scenario: A provider takes more than 60s. The receipt has failure='timeout' and elapsed_ns=60e9, and current_projection returns HoldUnavailable('stale_digest'), so an operator investigates digest drift instead of provider latency. A backward clock sample gives HoldUnavailable('timeout') instead of a clock-refusal reason. It still fails closed, but the safe reason codes are wrong.

9. `src/heterodyne/actions/judgement.py:285`: current_projection decides kind/hold with isinstance(request, c.PermissionRequest) before preparing the input. A raw (dict or other object) permission input that fails _prepared therefore returns HoldUnavailable instead of EscalateProposal. groom_route also accepts any object with a failures attribute, with no type or assessed-consistency check.

   Failure scenario: A permission request passed as a raw mapping that fails prepare() returns HoldUnavailable('stale_digest'), which breaks D4's 'permission failures escalate'. A hand-built GroomState(origin, failures=2, assessed=()) passed to groom_route gives CannotApproveNotice with no recorded assessments.

10. `src/heterodyne/actions/judgement.py:88`: There is duplicated and redundant validation work. bind repeats the type(action) is Request and ask_bytes length checks (lines 88-92 and 101-105; the second is dead after msgspec.convert). Every assess/current_projection runs the full prepare() three times (_prepared, bind->_prepared, egress or disposition). record_groom and concern each re-implement the same GroomState validity check.

    Failure scenario: Each projection re-validates and canonicalises requests of up to 128 KiB three times, plus a full Request data()/convert round trip. It runs again on every record_groom and concern call. The duplicated state checks can drift apart, and concern already checks less than record_groom. The simpler form is to prepare once and pass the Ready value through, and to add a shared _valid_groom_state helper.

The finding with the least evidence is that `bind` leaves `shell_ids`, `recovery_operator_event`, `recovery_evidence` and `recovery_closed_orphans` out of the digest. That contradicts the comment at line 167 ("No semantic input is dropped"). It is low risk, because P1 only sets those fields before `allocated`.

No CLAUDE.md rule applies to this Python diff, so there are no conventions findings. The open Quality45 and the five unavailable adapters were taken as given and not re-checked.
