# Plan5 final R3 author dispositions for UNREVIEWED d4

All three actual rounds remain used; no clearance or fourth-round authority. Raw report SHA256 `85e5877220ae182f0eec6525392e789ade6d3ea541db04340d210b20f3cb3538`. Public summaries are portable projections where original host literals cannot be published; exact original finding objects are retained privately; canonicalization and full ordered hashes are in the adjacent JSON. Original d3 and earlier reports are unchanged.

1. **Credential fencing of the sole decision writer is unsound: ADR §3.4/§8 runs wsd, admind's unsandboxed superuser agent and the operator terminal as the same service user, and btq is one shared Dolt sql-server database whose decision fields sit in the same bead metadata other queue writers modify.**

   Finding SHA256 `6c4444d1a23544efcdc2d76c347a1e0e9637790628c180592140d48439cd595d`. §6 accepts trusted same-UID administrative bypass explicitly; no credential/key ACL proof or shared-writer credential revocation.

2. **The origin session is parked/stopped (pending blocker, D26 landing) before the ask is built and gated, yet up to two grooming revisions must go back to the originator (ADR §5.9 step 4: the agent session, as steering), and no resume-for-grooming path is defined; resume (§5 l.235) covers only decision outcomes.**

   Finding SHA256 `cf1a31fb411fb5ab6171cedcbb8018022d2daadb174804e261e4bb34779c9ec1`. §5 returns bounded grooming to the SAME parked originating session, relands changed SHA and recomputes revision/digest.

3. **The one-use claim deadline is 5 s, but before claiming the effector must start under sudo, read the bead through the read-only principal, recompute the digest with refs resolved from its private store, and that store needs a bundle import budgeted at up to 30 s (l.366-367).**

   Finding SHA256 `04e77e6297ee139dd163cbf5a1c373d723eb6a90f1e4a5d63b6c35b1825c6868`. §7 completes bounded import/digest preflight before separate effect nonce and five-second claim window; original60s stays fixed.

4. **The design 'explicitly amends ADR §8.1 host exceptions 3 and 6' through a Plan5 design approval, while implementation beads must cite adr_revision a27acec, whose text keeps those exceptions; ADR §5.8/§5.9 makes any departure from the ADR at adr_revision blocking in code review, and §4 of this same design says an ADR conflict 'stops for an ADR amendment/approval'.**

   Finding SHA256 `c8ca8a6b1c00bff4adfddf29a73288d8fa6295f2cab1bb9aebc6edda0921435b`. §10 requires an actual reviewed, human-approved committed ADR amendment and native admission before affected implementation; oldpins cannot override.

5. **Exception 6 (R12 blocked decisions: partial or foreign decision write) is replaced by 'the bounded Marmot reconciliation workflow below', but nothing below defines how a partial or foreign decision write is repaired from Marmot; ADR §8.1 #6's reason (it can't be repaired safely from a chat message) is not answered.**

   Finding SHA256 `e1b41d90ff447f3bb20a6e778773039664da22229cf5c20ef6b314cfb9cb7ba9`. §10 specifies exact-before-state Marmot HOLD/repair question and sole-writer conditional recovery with retained provenance and no foreign overwrite.

6. **The minimal judgement port's closed result pass|groom|deny|unknown differs from ADR §5.9's sufficient/enrich/groom: 'deny' has no defined semantics (an LLM must never decide an approval), enrich (gatekeeper-added context, re-linted) is dropped, and neither deviation appears in the §12 enumeration.**

   Finding SHA256 `23bbf8940fccbabc32bc2b27fc748506e1aa55d4b8df7929c162922ddd73fd45`. §12 sufficiency returns sufficient|enrich|groom|unknown; enrichment rehashes; model output cannot human-deny.

7. **The pre-cutover trigger ('a release that ships both wsd and the gatekeeper MUST enforce judgement on every relayed ask') names no mechanism and doesn't define 'ships'; admind is independent of wsd and gatekeeper (ADR §8) and posts and writes relay approvals itself before cutover. §5 l.230 ties the mandatory gate to 'coordinated cutover' instead.**

   Finding SHA256 `bd2cea03c55d7f1de118a43ed1208bf6d52a101dd53d3011555abd865528a76d`. §6 requires relay judgement enforcement before coupled activation, independent of executor success; no binary-presence exception.

8. **The cannot-approve-as-written flow is a 'bounded control notice' whose denial is 'a real operator event', but r17 notices can't decide anything; the fixed reason code drops the gaps ADR §5.9 step 5 requires to be listed and the operator's 'groom it themselves' option; nothing defines what happens if the operator never acts.**

   Finding SHA256 `230f9451d43df6f872d06281da1ad2a4bd5ec66ba41ebb4f6a12ac377507d2ff`. §5 cannot-approve question exposes safe gaps and genuine groom/deny; silence holds, no auto-denial or fabricated consent.

9. **The settlement question uses an A/B question card, but r17 (K17q-K19b) maps an approve-set reaction to the recommended choice and every question must have exactly one recommended choice; the design doesn't say which is recommended or that reactions are refused.**

   Finding SHA256 `08b475add4f439ec5ea73c000b21e4b2f06f1c7b3e5da74e758b8c223eb7d8bd`. §7 settlement recommends HOLD; thumbs-up holds; explicit A authorizes only displayed attestation.

10. **Heart proposals are restricted to executable actions, but §3 l.85 puts all registry actions under a deterministic outward-action floor that 'host auto_approve cannot lower', so an action policy rule can never take effect; meanwhile ADR §5.4 files a heart proposal for any approval and the design excludes non-action asks, where a rule could matter.**

   Finding SHA256 `1257191f67a483404cf9466802fe36bf6445259ec878a3f121f7b9ea9462de7d`. §6 heart preserves proposal on every supported form while outward floor cannot be lowered; effect is disclosed.

11. **No purpose value exists for non-action approvals: ADR r17 allows only design|execution, the design requires execution to carry a validated action payload, treats absent purpose as legacy design only, and refuses unknown values; §3's 'escalated request outside the registry is an ask' and ADR §5.4's 'approve, non-action' have no valid encoding.**

   Finding SHA256 `c4168e19bed9c9fb69a1ff2ea96ba9b5f1a59cf3df8c93818a4f2543e7364700`. §6/10 adds explicit permission purpose as a proposed real ADR17 amendment; no missing-purpose guessing.

12. **An approved action whose initial dispatch never consumes a claim (three launches used, the 60 s deadline passed, or a boot/clock change 'holds initial dispatch') has no defined terminal state or notice, and action_state stays 'pending', which src/heterodyne/wsd/runtime.py:21 SETTLED_ACTIONS treats as settled.**

   Finding SHA256 `e2c29d8cd48dfb748fcf680b15421947c952f09a2867bdeee1088cb9189a7dc2`. §7 dispatch-preparing/ready/held remain unsettled; deadline/reboot/exhaustion emits needs-human and never settled pending.

13. **§6 says extending btq's closed ASK_FIELDS (currently effect, excludes, why, risks, refs, targets) with optional purpose 'needs its own genuine existing design/implementation dependency', while §12 l.680 says it 'belongs to the coordinated implementation dependency' and may be implemented and tested offline under this design.**

   Finding SHA256 `5803545aaf592dc4937bc08bb96e842072cbf3ade15098da2f2db1fd178a010f`. §6/10 queue extension is an explicit separately admitted implementation dependency covered by real amended design scope, not an invented currentAPI.

14. **The required approval-request enumeration leaves out deviations the design itself says must be disclosed: the typed public-reference renderer display exception (l.668 'must be disclosed'), approver removed from the hashed payload against ADR §5.3, dropped enrich, narrowed heart semantics and the new !action settle verb. §10, which §1 l.22 calls 'the explicit changes', is a different, shorter list.**

   Finding SHA256 `6f27156dc49c73070f304a49b95341c29a890a470b0682db492a2031930df1ec`. §10 single authoritative checklist covers every proposed ADR departure; no secondary incomplete list claims sufficiency.

15. **The public-reference renderer would let full canonical digests, full commit IDs and 'configured public operator keys' (npubs) pass admind redaction, which conflicts with ADR §8.1 #7/R21 (npub and 64-hex values withheld), §6.2 (only 12-char digest and short SHAs), and this design's own §6 promise of 'without weakening secret checks'.**

   Finding SHA256 `90d259fcff4f3c0fe46539a8df362e6849b04e9921eb2e9bf76161225d407c6c`. §12 removes typed full-hash/key redaction bypass; bounded labels/prefixes plus ordinary approved checks.

16. **Timeout TERM/KILL and 'prove no surviving descendants' are required before a definitive failure or a new attempt, but the launcher's process tree runs as the effector UID, which unprivileged wsd can't signal or inspect beyond sudo; who creates and owns the 'dedicated process group/cgroup' across the UID boundary is unspecified.**

   Finding SHA256 `144af8a2891e07cac55351e2f4ee2853ae659f170e4e8cbeda6c433ee2d4bf3f`. §7 root broker retains actual cross-UID cgroup cleanup authority; wsd cannot signal/inspect those descendants itself.

17. **kind:confirm confirmations are authenticated operator decisions that gate routing, but the single-writer section (§6) covers only approval kinds and doesn't say who commits a confirm answer or how the r17 question-card reaction and reply mapping applies.**

   Finding SHA256 `72140d70b97e94e171b04836a97ef2b5d90b6b7f9e96883f0e3291d714628442`. §6 all supported decision types including confirmation/recovery submit to the same serial writer.

18. **Grey-zone classification 'calls the typed gatekeeper', but the only gatekeeper capability resequenced into Plan5 is the closed sufficiency port (§12: 'no intake routing'), so grey-zone permission judgement (ADR §5.3 allow/deny/escalate) is either an unstated extra Plan7 dependency or always escalates.**

   Finding SHA256 `b0401a349ab80bdf9f25a314048196a4cef93fee563900b297fadf9d0197c6a6`. §3/12 separates permission allow|deny|escalate from sufficiency and explicitly admits both narrow Plan7 prerequisites.

19. **Disposition 8 claims 'stale origin aborts without affecting a successor', but design §5 only says to revalidate generation before each filing mutation and never defines abort or cleanup of shells already created by a generation that goes stale mid-filing.**

   Finding SHA256 `39b3c34478c1520c317465868e8a06079d62d5fb9037df381f56ee4247d1a861`. §5 stale-origin cleanup is creation-key/expected-state scoped; unknown orphan retains slot until authenticated resolution.

20. **The 32 'Finding SHA256' values bind 'canonical finding objects', but neither the canonicalization nor the report is available, so the R3 reviewer can't check that the dispositions are complete, correctly ordered or faithful to the actual R2 findings.**

   Finding SHA256 `f54a8322a331606fd702da021aac80a72d0a366d9a403135a018eeee4a1a0969`. §12 and new manifest define canonical finding hashes and supply private exact R2/R3 objects; no public-hash-only verification claim.

21. **The limits 'at most 8 outstanding requests per logical session and 2 per bead' are partly dead: a logical session is uuid5(bead, role, profile), and the origin is parked after its first filed request, so the 8-per-session bound can never apply before the 2-per-bead bound.**

   Finding SHA256 `0fd493d9ec4afa06b0ca5fa72a431f06085f47708a71ccd8b895cdeede6e2e60`. §5 simplifies capacity to2/bead plus configured hostcap; removes redundant8/session.

22. **Historical published archive needs retained reachability; clean final landing must exclude incompatible historical archive ancestry.**

   Finding SHA256 `50f6de304c37f1771bf121d50f2ac3768f83743e5a7481a524c36782d3732dac`. §11 requires published-ref retention/readback before asks and clean final doc snapshot landing, not historical forbidden-example ancestry.

23. **This file, still in the d3 tree and not marked historical, says 'The exact approved supervisor archive remains byte-for-byte unchanged', while d3 removed that archive from the tree.**

   Finding SHA256 `69f7490269c9515f1dabb1b20272adebfb96f98a1138617208f6fda8e70e4048`. §2/11 mark old author claims historical; old files preserved, no claim that removed archive exists at currentHEAD.

24. **Host-change convention requires an audited managed installer rather than ad hoc terminal configuration.**

   Finding SHA256 `e3852ef6836159f41487e8c7dfd1ac4faaa31ad555e05d99c94cc8b08f593ffa`. §7 single reviewed managed installer plan/apply/verify/rollback; no ad hoc credential recipe.

25. **An absent ask.purpose is accepted 'as the legacy design path if its complete native design validation succeeds', which infers the purpose from validation success, while ADR r17 item 5 says purpose is never inferred and absent purpose renders 'purpose not set'.**

   Finding SHA256 `996d9e49f382202ad957507f320b299b20afffd602cb2acaaf22c465dec0ff08`. §6 absent purpose remains nonselectable legacy; genuine operator migration/reissue before finalization, never infer design.

26. **The open_pr evidence relies on a 'unique marker bound to approval/revision', but the PR body comes from the hashed, operator-shown body_ref, and the design doesn't say where the marker goes or that it is part of the displayed effect.**

   Finding SHA256 `6090e2e323e6ee346b1c2c0a374f37455ae844a2639d855238eba3cb9323cb0a`. §7 PR marker must be part of approved pinned body before hashing; no postapproval body mutation.

27. **The canonical-effect lock is keyed on the exact effect, so two approvals pushing different SHAs to the same destination ref (or merging the same PR at different heads) are not serialized, despite the shared pre-effect tip evidence.**

   Finding SHA256 `b62d45894cfc306afb6f2129d727b139b0d3a275e8f3c62a162efd8773677f52`. §7 destination-resource locks serialize different approved effects targeting same destination, plus effect/approval locks.

28. **Each dispatch stages a full self-contained bundle (up to 64 MiB / 100000 objects), with no reuse of objects already in the effector store, including for reconciliation dispatches.**

   Finding SHA256 `e9bd013cff268c72812890acda1e107e1d39319c5d0a3b74f6aa444ebd78a017`. §12 accepts bounded full-bundle cost as residual; no performance-driven weakening or unsupported incremental promise.

29. **'!action settle <approval-id>' must be sent 'in reply to the exact recovery card', so the typed ID is redundant with the reply target and is a second correlation input to check for mismatch.**

   Finding SHA256 `d814a80f3f8d72914aa7e6c6e18ae553275cf7e2339c2303c55a8abfd92f46de`. §7 !action settle uses reply card only, no redundant free-text approval ID.

30. **'purpose' is used for both the digest-bound ask.purpose (design|execution) and the dispatch record's purpose (initial effect|read-only reconciliation), and the §12 terminology note doesn't separate them.**

   Finding SHA256 `49d1c3c6f18e4bcbdfd74b501694d29ca7b4fb88f12b9c6796250f3aa3a0c7a3`. §7 dispatch_kind initial|reconcile is distinct from askpurpose design|execution|permission.
