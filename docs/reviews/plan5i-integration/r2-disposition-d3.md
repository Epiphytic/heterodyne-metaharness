# Plan5i integration actual R2 — d3 author dispositions

Actual REVISE with 42 findings. d3 is UNREVIEWED; two actual rounds used. These responses are proposals, not acceptance. Full raw report SHA256 `7ede8a7c11f6c88e6e3b3767357aab076a4a45bf283a98d361403609f78e9471` is retained privately at the controller-evidence anchor. Full finding objects and canonical hashes: [JSON](r2-disposition-d3.json).

## 1. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:34

Finding SHA256 `9821a75936699709c4fba5621c154840fd2a983cba26ca1cee51bd8286a5d7aa`.

> Item 2 requires every selected c305 row to resolve its 'native task', and item 4 requires an approval/dependency check for every implementation task. No native Plan5i part 1-3 kind:task exists in any retained snapshot. Closed btq-bswl5 has no ask.targets and zero dependents, and btq-zj027 explicitly left Plan5i out of legacy pin repair. d2 names no path for binding these rows to genuine native tasks and approvals.

> asks.py and notices.py are absent at 0763774 and come only from Plan5i part2 rows, so every selection that satisfies item 2 has a null native_task_binding. Line 86 makes that block selection, so preflight deadlocks into a new amendment cycle, which contradicts lines 23-25. Otherwise the controller creates part tasks with create-time design_approval=btq-bswl5. Create-only meta is allowed, and approval_valid checks neither ask.targets nor created_at, so those tasks pass ready() under an approval that never named them: a fictional history approval.

**Author disposition — §§2:** Create and bind genuine new native P1/P2/P3/I records; stage1 approval names exactly the three ports. bswl5 is historical only; create-time approval borrowing forbidden.

## 2. docs/reviews/plan5i-integration/source-map-d2.json:428

Finding SHA256 `cd227f2153f4a994e839a7bf205ef562145ced11884cac9ebeda46a69e8e5caf`.

> plan4c_approved_plan binds only 4ac749b, and its revision_exclusion rejects any other revision. This omits the genuinely approved Plan4c Amendment 1: btq-ousih, approved by Liam via Marmot at 2026-10-14T07:44:04Z; doc ab17da9:docs/superpowers/specs/2026-10-14-plan4c-amendment1-s9-design.md, sha256 51e15ec9..., r3 APPROVE fd50462. The amendment replaces E9/F4/F9/F10 and the Task 6/8/13 text and adds E25. ousih appears in the map only as snapshot blocking edges.

> A selected T13 offline-rig port or T11 documentation port resolves 'exact approved plan' (d2:35) to the unamended 4ac749b. p4c-t13 was written and reviewed against /tmp/plan4c.md, which is byte-identical to 4ac749b. The port therefore passes plan conformance while missing section 4.4's E25/apply_patch, EROFS, leftover-clear, namespace-split and bind-over-root tests. T11's Amendment-1/E25 claims at 78f0175 also cannot be validated. (b2c9895 is ADR r16 Amendment 1 in hermes-workstreams-v2, not this amendment.)

**Author disposition — §§1:** Pin genuine approved Plan4c Amendment1 and its distinct ADR16 amendment/review; preserve replacement and surviving E25/runtime constraints.

## 3. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:3

Finding SHA256 `b16c7f71c32c3018b71244ee0dc729015e4dc17704d9d9a306ea2fa4ecbb5d59`.

> d2 is filed under btq-fu366 (kind:brainstorm, no edges) and seeks approval. It names no implementation kind:task, no creation step, no ask.targets and no native blocks edges on btq-1wu2i or btq-d1v9d. It also drops d1:33 ('The first approval must name actual implementation tasks'). The card draft says 'for btq-fu366' and lists no targets.

> ask.targets is optional, so Liam's approval succeeds and pins nothing, as happened with bswl5. fu366 cannot be the target, because approve-bead refuses beads that are not kind:task. A later integration task must then do one of three things. It can use create-time design_approval (btq:554/1477; a restriction is pending in btq-f41ih). It can pin another valid approval such as bswl5, since approval_valid checks validity, not scope. Or it can get a fresh approval, contrary to d2:49-51. Without blocks edges it can show as ready before S and T10 exist.

**Author disposition — §§2:** Use the four actual controller-created IDs and predecessor edges; approval target list and readbacks are mandatory before any implementation.

## 4. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:37

Finding SHA256 `d1679ba4ea1d9f7db6840de5e5539feacedd3652a104a5b6d967c1637c044d31`.

> Item 2 is circular. Lines 15-17 and 25-27 require the admitted manifest before work and before source implementation, and map line 23 requires a reviewed current-base resolution before selection. Yet item 2 (37-39) must already contain prepared, actually reviewed current-base ports for rows that lack code acceptance. No stage or separate admission produces those ports; only T10 is 'separately admitted'.

> Selecting any needed consumer row (asks.py 38 rows, notices.py 22, settings.py 6) requires a reviewed port before selection. Writing that port is implementation, which d2 gates behind the manifest and its implementation-task approval checks (44-51). So the controller either fails closed into the unnamed recovery path, or writes and reviews ports before any implementation approval gate exists.

**Author disposition — §§2:** Stage1 genuine approval authorizes bounded offline port preparation before source exists; stage2 requires a separately reviewed exact selection and genuine approval. Actual pinned queue close supports reviewed offline deliverables without merge; incompatible installed gates still hold.

## 5. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:54

Finding SHA256 `d5144d63641c6f4a2de6fb72b67f7cf8f91ab36adf36350d81e48fd6109fd568`.

> d2 never ties its non-deployment approvals, authority or preflight-failure questions to an authenticated human Marmot/admind decision. These cover changed design, amendments including live action, implementation-task approvals, review-budget and digest exceptions, and merge prerequisites. 'Marmot' appears only for deployment (130), and 'genuine authority' is undefined. The governing rev16 sections 8.2/5.5 and PICKUP still allow terminal (via=cli) and forge surfaces.

> Liam answers a changed-classification or part4/T13 live-action question in the Codex conversation, or the controller records approve-bead --as Liam --yes (via=cli). approval_valid ignores via, so the gate passes with no digest-bound Marmot card. That violates 'All questions/approvals ONLY Marmot admind'.

**Author disposition — §§1,5,8:** Current operator override sends every question/design/recovery/exception/deployment through authenticated Marmot only, including OPS recovery; no terminal/conversation substitution.

## 6. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:56

Finding SHA256 `4d04f880f6f59614ac3645264bbe60c2931b16af97846e94955a2e0be79554cb`.

> A preflight failure goes to an unnamed 'controller's supported recovery path' with no mechanism, recipient or Marmot channel. Separate supported mechanisms exist, but d2 cites none: manage reclaim/status/dependency repair, approve-bead --pin, release --reason/needs-human, admind ask post --kind question as with xjgx, and waiting on btq-1wu2i. d2 also drops xjgx option A's 'any remaining blocker returns here'.

> Suppose the one xjgx T10 review returns REVISE, a port uses up its three rounds, or T10 stays blocked. The missing-input report has no recipient, so the task sits blocked and unrouted. Or the controller uses the MANAGE.md 114-125/doctor fallback of operator direction 'in the active conversation or terminal', which breaks Marmot-only.

**Author disposition — §§3,5:** Define finite durable hold + bounded admind question + supported native manage/OPS after genuine grant; xjgx remaining blockers return to Liam without automatic retry.

## 7. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:76

Finding SHA256 `de479e4cb317c83d893401a8cd10feb6a3901f2b0d0752ecafb9875ac22fd8d9`.

> d2 treats Plan4c T8/T9 as settled ('do not redispatch') and leaves their native state out of the unresolved inputs (57-59). Native btq-iz0rd (T8) is in_progress under the stopped worker p4b-t2 with needs-human, and btq-heohk (T9) is open. Both block btq-d1v9d (T10), and T11/T13 too. btq claim accepts only ready() tasks.

> Item 4's honest dependency check finds d1v9d blocked. The mandatory T10 prerequisite cannot be lane-claimed, given a worktree, reviewed under xjgx or closed, so integration stalls or the controller improvises queue actions. Supported routes exist outside d2: a Marmot-approved manage reclaim followed by an honest T8/T9 close, or a scoped dependency repair. An honest T8 close may also need a ruling on its unreviewed 5-line test_tiers.py tail, since T8 already used three rounds. d2 names and orders none of this.

**Author disposition — §§3:** Distinguish source already in baseline from unfinished native tasks; authenticated cessation/recovery, tail review/budget accounting or exact authorized dependency disposition precedes T10.

## 8. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:40

Finding SHA256 `bbb787604ef85fccde57c76de7971cfdfd2928421b49981858508f41da01975e`.

> d2 never pins the base for the single xjgx T10 review. 'Current-base' (40, 76) can mean 0763774 (line 72, and the T10 research target) or S (line 101). Corrected T10's published_ghp fixture, added in 451e934, passes only under 0763774's _START boundary. S's frozen contract, under which GitHub prefixes match anywhere, removes that boundary.

> The T10 port is frozen, tested and reviewed on 0763774, which spends xjgx. Then S lands, or the line-92/96 gate runs the harness suites on S. The title 'Add the codex vghp_<36hex> ...' is flagged as a GitHub token, _ask fails with ASK_UNPOSTABLE, and posted() asserts. Fixing it changes the frozen, reviewed T10, which has no review budget left.

**Author disposition — §§3:** Spend the one T10 review only after actual S plus declared prerequisites; resolve the inside-word fixture collision without weakening scanner classification before reserving the exact reviewed candidate.

## 9. docs/reviews/plan5i-integration/source-map-d2.json:692

Finding SHA256 `82a9dc725e33fa3f54ead8453c4f8a73dce4deb9ce6e188ee9463fcd7f7e912d`.

> All 230 ledger rows have identical null namespace, task, plan, review, delta and resolution fields with 'defer-unresolved-binding', set unconditionally by drafting-script.py:18. Yet 200 commit subjects name tasks, 15 Code-Review range statements (10 git-parsed trailers) cover 90 rows, the Plan5i review records are retained, and the map itself names bb389e3 as T10 r1_source. The commit-to-task-to-review map R1 asked for does not exist, but F5/F6 say 'addressed'.

> R2 and the human approve a method whose F5 disposition says the map exists. Instead, every binding is deferred to a later manifest under an unspecified protocol with no further design approval (16-17, 49-51). R1's risk of adopting pre-redo verdicts is guarded only by prose, and no per-commit namespace separates Plan4c T10 from Plan5i part2 Task10. The design fails closed; the defects are the missing map, the overclaimed dispositions and line 86.

**Author disposition — §§4:** Replace uniform null stamping with actual Git metadata, subject discovery labels, 67 retained reports/brief ranges and explicit unknown binding states; every selected port requires exact new task/source/review evidence.

## 10. docs/reviews/plan5i-integration/source-map-d2.json:3819

Finding SHA256 `676e34f1e5c099c619f216f328e8c16506d6f5e4971e2dfc7a33c99e64eaddab`.

> The T3-wiring c1 review (review-5ip2-t3w-c1.md, sha256 04bfd01e..., range 1dc5b92..d20e259) is REVISE, with the blocker 'recover() unguarded at daemon.py:239'. Its fix, b7a7e9b plus 3268766, merged at 8cb6731 and was never reviewed; every review range excludes 3268766. Ledger rows 3750/3802/3819/3836 stay null, recording neither the unreviewed delta nor the used round.

> A notices.py selection must port the daemon wiring, since main constructs no Notices. 8cb6731's subject 'c1 recovery containment' and T3's c3 APPROVE via parent 1dc5b92 read as closure. So either the containment fix is accepted unreviewed, or the port starts a fresh three-round counter that ignores the used t3w-c1 round.

**Author disposition — §§4,5:** Name wiring REVISE range, containment test/fix/merge separately from Notices T3; preserve used round and require applicable actual acceptance under known budget.

## 11. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:107

Finding SHA256 `253fb2f08440f2404bfcd664ac0d8f70c1f3d7ab76087a010ac8f2c52e4c4298`.

> Merge rows list `diff-tree -m` per-parent path concatenations, with no parents or combined-diff resolution content. Twelve merges with real `--cc` resolution hunks lie outside every Code-Review range: 80b5914, 70e4149, e80787c, cd83dee, c80611d, 39d33f0, 0bc7d85, 9a591da, 315b1d8, 2bd53af, 0fc6f99 and bb389e3. Lines 107-109 require mapping only p3-redo merge resolutions, and those are in fact covered by 1f71994..991f257.

> A manifest treats 70e4149's 70 path entries as ordinary deltas and ports the final daemon.py/workstream.py for the Notices contract. That silently carries 70e4149's unreviewed assemble()/Deps resolution, which sits in the baseline of every reviewed range.

**Author disposition — §§4:** Recompute all 21 combined-resolution hunks with actual git show --cc; inventory membership is not review acceptance. Selected unresolved resolutions require exact port review.

## 12. docs/reviews/plan5i-integration/source-map-d2.json:6357

Finding SHA256 `5d2e53e2a75bac5a2c78478e76e05da739953b412dd00fbb62c83a8d34f5fcf7`.

> excluded_plan_branch_extras copies a two-dot tree diff from main to each bridge/close/e2e tip. That gives 130 paths per branch (26 D, 103 M), 129 of which are main's own history since fork 7cacb83, inverted. Each branch's own delta is actually one plan document. d2:82 also calls the deferred, unselected c305 the 'explicitly selected implemented successor', and F4's 'exact ... extra inventories' overclaims.

> An amendment that includes those branch deltas (d2:53/82) reuses changed_paths_from_main as the delta. It deletes 26 current-main files (admind/guard_state.py, sandbox/creates.py, units.py, wsd/unit.py, the guard-migration tests) and reverts 103 others to 7cacb83, instead of adding one plan document per branch. Used as an exclusion filter, the list also covers all 55 base..0763774 paths plus c305's daemon.py and journal.py.

**Author disposition — §§4:** Compute each branch-own delta from its merge-base: three plan-only deltas, not inverse-main deletions; prohibit whole-branch merges/deleting current safeguards.

## 13. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:96

Finding SHA256 `6416953e8c1dafeff410e1a5d3adacd1e46e40fb61f6da69a8cd3038b5b25baf`.

> d2 silently drops d1:66-78's private asks/notices fixtures for the forms S newly recognizes: distinctive prefixes such as nsec/gh*_ inside words, AWS keys without a trailing boundary, and embedded sk-. Those fixtures covered original text, truncation boundaries, escaped controls, titles, choices, refusal reasons, answers and the Notices suffix/restart cases, with real payload inspection. Only the harness triplet keeps 'Required fixtures' (92), no R1 disposition explains the drop, and the c305 relay-consumer suites contain none of these forms.

> c305 asks/notices are ported onto S and the line-96 gate passes on the existing suites. No required test sends an embedded token ('v'+ghp_..., 'x'+nsec1...) through card_choices against admind's redact()-based check, through the shown_text/shown_title cuts, or through refusal reasons, answers or Notices. If wsd's sensitive_kind and admind's redact disagree, admind refuses the card and the question is stranded without anyone noticing.

**Author disposition — §§6:** Explicit full-before-truncation fixture classes include inside-word prefixes, embedded vendor/AWS forms, identifiers and controls; inspect every actual payload field.

## 14. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:96

Finding SHA256 `6320a9b46f943b052f66e5a9d64977bf54996a17b7ee652dd786ebac44f2f58e`.

> Line 96 collapses d1:86-89's scoped install-check superset requirement, and its 4lsqy/xpcnj fence, into a bare 'Retain install-check's semantic-superset obligation' inside the mandatory Plan5i gate. Scanner d3 (1995977:247,252-253) assigns that superset to btq-4lsqy, which is blocked and unapproved, 'once both changes land'.

> With S on main but no 4lsqy, main's install-check flags only word-bounded 58-char lowercase npub/nsec. The scanner already flags embedded, suffixed, short and uppercase forms, and S adds embedded nsec. The mandatory item cannot pass, and skips count as failures. The implementer either blocks indefinitely, reports a narrowed ad hoc test as a pass, or imports 4lsqy's unapproved design.

**Author disposition — §§7:** Stage2 genuinely depends on separately accepted install semantic-superset task; stage1 cannot claim missing tool pass. Filename followup remains independently scoped.

## 15. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:61

Finding SHA256 `6207f6a82ca6786a370a58664519d6b51d745e646a3a848fd97bcc8d50e3e642`.

> Lines 61-62 pin 'ADR revision16' to 93d0e90b with no repository, ADR number or path. The SHA resolves only in hermes-workstreams-v2 (docs/adr/0001-workstreams-v2.md, blob 4b608411). This repo's docs/adr/0001-workstreams-v2.md is revision 15 (28e3df4) plus local edit 362dd76. ADR r16 Amendment 1 (b2c9895) is neither included nor excluded.

> A reviewer or validator tries to resolve 93d0e90b in this repo, where d2's other 17 pins resolve, and fails. It falls back to the local rev15 copy, which lacks rev16's managed-install, /harness and digest-prefix rules. Line 92's ComputedDigest representation and the harness contract are then judged against superseded text.

**Author disposition — §§1:** Use separate repository-key ADR16 exact revision/path/hash and pin standalone approved amendment; current ADR17 a27 governs accepted later text.

## 16. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:67

Finding SHA256 `66b16095c404072c58dec500672534b9f51b8453d0297e4d94d3fb7a0baf9065`.

> HANDOFF_ROOT is defined only as 'the operator-retained dated handoff root', with no date, parent path or expansion rule, and no supported tool expands '${HANDOFF_ROOT}/...'. The 13 map-hashed raw review logs in plan4c_review_records exist only in /tmp, which is wiped at boot. No retained copy holds their actual_model or T13 R2's certified head.

> After a reboot (tmpfiles clears /tmp at boot), the 13 raw_log locators and hashes dangle and that provenance is lost. Once a second dated root exists, '${HANDOFF_ROOT}' has no declared binding. btq refs and harness retained() reject it, and preflight fails closed with no specified recovery.

**Author disposition — §§9:** Define machine-neutral anchors supplied privately; durably copy all thirteen raw logs byte-for-byte into controller evidence with original hashes. No HANDOFF_ROOT shell assumption or ephemeral-only availability.

## 17. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:49

Finding SHA256 `619804c117a3a0f9a80762b239f3ffbf5aa3cc29b0e3157e8b1e626210b157c5`.

> Lines 49-51 let implementation proceed under the conditional method approval once S and the rows resolve. They rely on an unnamed 'supported source-admission/integration protocol' (26) and 'existing ... validators' (46-47), which no installed component implements: controller-delivery-v1 is PROPOSED and ytmpy is in review. No human authority, ask.targets or drift amendment binds S or the selection. d1:28-33's rules were dropped without explanation.

> Liam approves a card with no targets. The controller writes a manifest and creates kind:task port beads with create-time design_approval. design_allowed and approval_valid pass without checking targets or S, so S-dependent ports reach ready() without any human seeing them before implementation; only deployment approval binds the final source. Read strictly, the required validators do not exist, so there is no executable path.

**Author disposition — §§2,7:** Explicit controller Git/native checklist replaces invented installed admission validator. Exact future integration selection receives its own reviewed design/human approval.

## 18. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:39

Finding SHA256 `48114d7d29e2234469c58f809d8e30f94de550ee0262f7e1d8a1a4b38d3f03c9`.

> d2 caps reviews at three per change with no reset and only the T10 xjgx exception. It defines no exhaustion outcome, counting unit or Marmot exception route for other rows. Several required predecessors already used three or more rounds (5ip1 T4 used 5 and T5 5+; 5ip2 T4/T6/T7 used 4 each), and current-base ports carry new resolution content (runtime.py base line 40, daemon.py) that needs /code-review.

> Required port re-reviews dead-end. Preflight fails into the unnamed recovery path, and each task needs an ad hoc xjgx-style exception that d2 does not anticipate. The xjgx, j90wi and litno admind questions already show a concrete Marmot route that d2 could name.

**Author disposition — §§5:** Preserve exhausted/uncertain old budgets; freeze concrete candidate and seek exact Marmot exception or alternative before another invocation. No automatic reset or silent extra review.

## 19. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:116

Finding SHA256 `3d61dcbc03fcd5fda5e2af371e3ed4cb9e3447eef0630fdd1dee155ddf824eb4`.

> d2 never names, orders, includes or excludes the open retention fixes btq-b7oa3 and btq-oaw8k. These cover raw event text kept unredacted in the inbox and in r<gen>-session-events.jsonl. They target exactly the Plan5i session_events/inbox/event-log rows d2's consumers need (36200c8's inbox_add of msgspec.json.encode(ev); 38ad8c0). d2 drops d1:89's retention exclusion, and of the release chain 1wu2i, 4lsqy, b7oa3, oaw8k, h6y9w, qdyj7 it names only 1wu2i and qdyj7.

> asks.py needs the 38ad8c0/36200c8 lineage, so a port carries c305's raw _take storage. Every named gate passes, because c305's secret test explicitly excludes the inbox. Lines 116-117 stop this only if a reviewer remembers, with no named owner or order. Absorbing the fixes instead is an amendment, and it leaves b7oa3's r5 plan, which requires its files to be identical to c30546b, with no named rebase target.

**Author disposition — §§2,6:** Make accepted retention fixes actual stage2 native prerequisites and require their own source rebase/authority; stage1 may only demonstrate inherited failure privately.

## 20. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:90

Finding SHA256 `ad825f703e30c431d6e5770b0e1f3a76063a5575a9bdfb0eeeaca02527295b15`.

> The Notices obligations from d1:75-78/97 survive only in part. Scan-before-truncation remains (90; map notices.py). Nothing requires a fixture with a sensitive suffix beyond the display bounds. Nothing requires journal inspection showing that withheld rows keep no raw text. Nothing requires at-most-once/unknown-outcome behavior for the withheld:<ref> replacement across a restart, and no historical test covers it. Lines 116-117 also narrow d1's 'raw-text retention' stop to 'raw secret retention'.

> A notices.py port posts the replacement outside the sending/recover path, so a crash in the post window replays it after restart, while the selected crash tests, which use non-sensitive text, still pass. A withheld row that keeps an identifier body (npub/64-hex are 'Not secrets, but identifiers' per secret_scan.py:55) never trips the line-117 stop.

**Author disposition — §§6:** Require suffix-before-fit withholding, no raw withheld journal text and at-most-once/unknown restart behavior; uncertain sends may lose a notice but never replay.

## 21. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:96

Finding SHA256 `cd1d519380504867c1707cdf1add33ccf8d104c132ce0c1cb57faf49962b3038`.

> d2 keeps relay-body verification (90), the scanned harness triplet (92) and the offline-only rules. It silently drops four rules from d1:72-73/80-84: inspect actual private journal/bead/relay payloads rather than helper return values; scan before native creation and posting; route through the real private fake queue/relay; never send fixture secrets live.

> A port moves harness's scanner check after queue.bd('create'), or a new nsec test checks only shown_text()/ASK_UNPOSTABLE. d2's fixtures pass while a raw secret sits in a fake_btq bead description or a wsd journal row. Nothing requires world.created == [] or asks.posts == [] (the c305 pattern), or journal assertions for the new forms.

**Author disposition — §§6:** Test real wsd bead through admind approval_card/_checked with fake native queue/relay; inspect private bead/journal/outbox/send bytes and refusal before creation/post.

## 22. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:96

Finding SHA256 `fabf42ae68a7debf78f75dd516a20b80a1993df306764b2c6c1c8bb71f16df8c`.

> The focused gate names suite families, two files and only the two test_wsd_beads BTQ_REPO nodes. Five other BTQ_REPO-gated c305 nodes go unnamed: test_wsd_asks_approvals.py:976, test_wsd_harness.py:525/540 and test_wsd_review.py:490/536. The rule for hosted CI is unstated, and ci.yml never sets BTQ_REPO on any relevant ref.

> A focused asks/harness/review run without BTQ_REPO skips those real-btq contract nodes and is recorded as complete, because only the beads pair is named. Hosted CI skips the beads pair on the final candidate. So line 115 is either unsatisfiable, or an earlier focused run silently stands in for it.

**Author disposition — §§7:** List all seven exact real-BTQ nodes; require immutable safe queue/fake bd provenance and hosted required PASS or separately visible required exact-head contract job.

## 23. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:119

Finding SHA256 `057e50dc5953f67521a8bf47a0602529d52d248a42d9171be734eff9630d5c8b`.

> Lines 119-120 retain only full outputs and author dispositions. They drop d1:100-103's retention of candidate hashes and review provenance, and lines 62-64 omit applicability and ready records. No general rule binds each future review round, including REVISE rounds and fixes outside the ledger, to the exact reviewed SHA or range and the reviewer tool and model. The map's own review records also lack per-record reviewed SHAs (T8 R1/R2, T9 R1, T11 c1, T13 c1).

> A REVISE round on a port, or an integration fix outside the ledger, is recorded with only its output and dispositions. The three-round count and the 'Claude Opus built-in /code-review' requirement then cannot be checked mechanically, so a non-Opus round or a round on the wrong candidate could be counted.

**Author disposition — §§5:** Every invocation retains exact base/head/tree/range, requested/reported identity, raw output/hash/exit/errors and ordered dispositions; historical unknown scope cannot be inherited as acceptance.

## 24. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:80

Finding SHA256 `1e1fb8e43bc72acaa8ed70ff89796bed2d911c75c54ee51bdf699e537c4dff62`.

> R1 said to 'take ... or explicitly exclude each one'. d2 does neither for the p4c-t11 docs or the p4c-t13 offline rig ('if selected'; only live runs are excluded), or for the three c305 follow-ups (a8ebecc, 9dd8ee3, 6237d46), which stay 'deferred and unselected'. F4's 'addressed' therefore overclaims. bswl5's 'then parts 1, 2, 3' order is also not restated, and no native part tasks exist to enforce it.

> R2 or the human approves without knowing whether these are in scope: T11's documentation of the T10 fixes, T13's offline rig, ResumeNotes, the review-notice lint and the review forms. Under 49-51, a later manifest can leave all of them deferred and proceed with no further design approval.

**Author disposition — §§4:** Explicitly exclude T11 documentation/T13 rig/live and part4 execution; explicitly include the three named successor followups in P3 subject to genuine acceptance and original part ordering.

## 25. docs/reviews/plan5i-integration/source-map-d2.json:6822

Finding SHA256 `ba52a377fa22bb6be696c0cd40f9425c246505c578c3d47f6192738a335cd858`.

> required_before_executable_admission (6822-6828) is a verbatim copy of the older bounded-audit remaining_gaps (drafting-script.py:40). It lacks ADR item 3 (the corrected T10 source and its final review). It also lacks most of item 4: old/new tree, overlay proof, pinned named offline tests including the second BTQ_REPO case, dependency checks and validator verification. There are no null slots for any of these.

> A controller or validator built from the map's machine fields checks only S, receiving_merge and the row bindings. It admits a manifest whose part2 rows depend on stale bb389e3 T10 content (merges ef798f6, 6315a05), with no admitted corrected T10, xjgx review or overlay proof. This is avoided only if it follows preflight_contract's pointer back to the ADR.

**Author disposition — §§7:** Provide finite proposed receipt template including exact S/T10/port trees, overlays, native eligibility, selected rows, results and lifecycle/delivery checks; mandatory nulls hold, no installed validator claimed.

## 26. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:129

Finding SHA256 `144423b13c0cbc701c6ad16e6e70420dd139c20584a9ec9d1f68913356e3ca25`.

> Lines 129-130 require deployment approval to bind final source, installed state, this design and the deviations 'through Marmot', but name no mechanism. Question and merge asks carry a null digest. btq REF_KINDS has no installed-state kind. admind redacts runs of 64 hex characters and refuses any approval card that redaction would change.

> The controller posts an xjgx-style question 'deploy Plan5i?' (digest12 null), Liam picks A, and that answer is treated as deployment approval with nothing pinned or re-checked. Or a proper approval card that carries raw SHA-256 installed-state digests is refused ('decide it at the terminal'), which breaks Marmot-only. Or targets that name already-pinned implementation tasks make the decision fail, as ask 9ybk did.

**Author disposition — §§8:** Deployment separately approves immutable real ref file containing installed-state/source/deviations; native ask digest binds bytes, safe card links avoid raw identifiers, exact readback drift holds. No invented ref kind.

## 27. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:76

Finding SHA256 `2008d98633ae77dc2e4e375c71fd158fe7bb27217e5e36a55ec7671def80e486`.

> Line 76 lists part2 Task10's bridge-flow dependencies as T6/T7/T9/T11, copied from merge dccc6ea's subject. Approved plan 99058fc:685 says 'It fails until Tasks 1-9 and 11 are in', and none of part2 T1-T9 is on 0763774. That includes T8 StateNotices (218ed7c/61458df via dd1aed7), which steps 8-9 of c305's test_wsd_bridge_flow.py exercise. So F6's disposition is inaccurate. Also, 'before their implementation' would not reject a strict xfail that persists after them.

> A manifest following line 76 leaves out part2 T8 (and T1-T5). Bridge-flow then fails at step 8 (one stuck notice instead of two) and step 9 (no closed notice), which forces unplanned scope. This is mitigated because d2:90 and the map name StateNotices, and line 96 fails closed.

**Author disposition — §§4:** Disambiguate part2 Task10 from Plan4c T10; enforce part2 Tasks1–9 and11, StateNotices included, and require final bridge flow PASS without inherited xfail/skip.

## 28. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:76

Finding SHA256 `0f65168342f7e1020f05463820b9503a3f70868453260fa0bf55efa04ea54355`.

> d2:76 and the map's authority.t10_exception follow the 'interpretation' the controller wrote into the answer, not the text of xjgx option A. They omit 'any remaining blocker returns here, with no automatic extra review'. The question body (09249fcc...) and the post record (b26d5ab3...), which define what choice A means, are not hash-bound; the answer is bound only indirectly. No step before invocation reserves the named candidate.

> The one T10 review returns REVISE. d2 offers only the generic missing-input report, not a return to Liam on Marmot, so the controller stalls or improvises. An edit to the unbound question text would also go unnoticed.

**Author disposition — §§3:** Hash-bind exact xjgx body/post/answer separately; reserve exactly one final S-based candidate and return remaining blockers to Marmot, not automatic extra review.

## 29. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:72

Finding SHA256 `90f2ed66956131cac21be12fa6b3ca8176c0a9e8cb2e5e02fa2f3d0cfd0ce9da`.

> d2:72, the map's authority.scanner and the card draft all omit btq-1a845. That is the in-progress scanner execution-fd prerequisite design, on which btq-1wu2i was released blocked at 05:51:33Z, four minutes before d2's 05:55:22Z commit. At d2's commit its d1 planned to re-commit that patch; its current d4 instead merges the test-only commit 9a14b3b, which is not an ancestor of 0763774 or c30546b, into S's ancestry under a separate Marmot approval. S's approved base stays 0763774 under pxtig.

> Liam approves a scanner authority statement that cites only pxtig, without seeing that S's route is blocked on a second design whose d4 is itself blocked with its review budget used. Item 1's generic checks and lines 55-57 plausibly catch the extra gate, so this is a disclosure gap.

**Author disposition — §§2:** Explicitly retain scanner hostread amendment/admission gate and rejected three-round status; original pxtig alone is insufficient to define admitted S.

## 30. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:40

Finding SHA256 `4da829012c361245306a5938013a4ed90659d37747af1b773989b1cc870b162b`.

> Corrected T10 (bf5593c harness.py:286-287) puts 'nothing uses it until `wsctl harness use`' on Liam's Marmot install-approval card. admind has no harness route (blocked btq-qrljq). c30546b notices.py:13-14 states 'Operators only use Marmot (btq-qrljq): no text names a command or the host'. d2 never names qrljq/56v6h or records this as a known Marmot-only deviation.

> After a T10 port, Liam approves an install on Marmot whose card points to a terminal command he cannot run. The install stays unselected and launches park on NOT_SELECTED. This is tracked separately, and deployment must list deviations (129-130), hence low. qrljq plan r3's bf5593c ancestry checks also need re-pinning, because d2 ports bf5593c instead of merging it.

**Author disposition — §§3:** Disclose qrljq/56v6h missing real Marmot route; no host shell instructions or usable-install fiction, separate reviewed re-pin/deviation required before activation.

## 31. docs/reviews/plan5i-integration/source-map-d2.json:459

Finding SHA256 `9f2e193965c2b9204f35bcdd27e58ae5168d764a065aae26278701f5b02c82c9`.

> plan4c_review_binding.t10.conclusion repeats audit text from before xjgx word for word: 'Requires explicit honest review-policy/human disposition path', from plan4c-release-audit.json written at 04:54:34Z, before the 04:55:22Z xjgx post. It does not point to authority.t10_exception, and the verified-map link that reserved xjgx was dropped.

> A controller or validator that reads only plan4c_review_binding.t10 sees an open human-disposition requirement. It posts a redundant T10 budget ask, which could draw a second, conflicting answer. Regenerating the map from drafting-script.py:35 brings the stale text back.

**Author disposition — §§3,4:** Exclude stale aggregate T10 lineage; separately admit corrected bf5593c under actual xjgx scope, preserving old full/narrow REVISE history rather than asking from a uniform null ledger.

## 32. docs/reviews/plan5i-integration/r1-disposition-d2.md:45

Finding SHA256 `1db708abc269d2a8c4d26cf24d92e3a3de2024aa7c841a903b46917d260558ca`.

> Disposition 9 claims an 'exact queue pin', but d2:96 only says 'supply the exact reviewed queue source via BTQ_REPO'. Neither d2 nor the map names the queue repo or commit, or says what 'reviewed' means. The queue source is missing from the unresolved inputs (57-59), and the map has no BTQ_REPO content even though the disposition cites it.

> BTQ_REPO points at the beads-task-queue checkout, which fast-forwarded six times on Oct 13-15, and the tests run its working-tree bin/btq with no commit check. Two 'mandatory' runs can therefore pass against different btq code, with nothing to validate them against.

**Author disposition — §§7:** Pin queue repository revision 6fe5759 plus actual btq/manage/protocol blob hashes; source drift requires exact compatibility disposition, not mutable BTQ_REPO HEAD.

## 33. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:14

Finding SHA256 `52a521ff086520ab502724dfa7a3cae465083ea1c3f5ca18bd303735080f7f57`.

> d2 was frozen at 05:55:38Z and sent to R2 with S absent: 1995977 has no src change, and t2s2 was on HOLD at 05:06:49Z. That silently reverses two d1:28-33 rules, 'pin S before freezing d2' and 'first approval names tasks/refs'. Neither d2 nor the dispositions say they supersede d1:28-33, or why; R1 gated only executable approval.

> An approver comparing d1 and d2 sees d2 break d1's own precondition without a stated reason, and cannot tell whether the method-level approval is intentional or the S pin was simply omitted.

**Author disposition — §§1,2:** Disclose intentional staging amendment: design review may precede S, but executable stage1 human ask waits actual S/T10 refs. No silent source freeze claim or premature implementation.

## 34. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:90

Finding SHA256 `6ba28c5a98db339825bfd666f4b5855fc36deba1c2762a83d0c4dbaff203015c`.

> d2:90 and the map's consumer_contracts.direct call wsd/settings.py and wsd/accounts.py 'deferred' consumers. Their scanner call sites are byte-identical at 2c0c5b5, 0763774 and 1995977, and accounts.py has zero c305 rows. sandbox/settings and harness/settings, which are in the same position, are listed as 'already present'. No gate targets their show()/is_reference output.

> S widens SECRET_VALUES, so show() redacts more in ConfigError text. Read per consumer, item 2 has no admissible accounts row and stalls; read collectively, it is vacuous. No targeted regression asserts show() output. Impact is low because B1 leaves is_reference and the call sites unchanged.

**Author disposition — §§6:** Pin current settings/accounts unchanged callsites and test is_reference/show error behavior; distinguish already-present consumers from historical future imports.

## 35. docs/reviews/plan5i-integration/source-map-d2.json:182

Finding SHA256 `f344a1a1f0694a784ed21fe6736b7824f715ecd6d00d10f20eecbca5851fee86`.

> The 'Complete scanner consumer contract' (d2:90) and the map's shared_current omit current scanner/redactor consumers: units.py, config/accounts.py, harness/selection.py, admind/redact.py with asks.approval_card, agents/exec_dirs.py's shown_name (used by scheduler) and the main-only sandbox/creates.py. No offline test sends a wsd-composed approval bead through admind's approval_card/_checked. F8's 'addressed' overstates.

> A harness or agent approval bead passes wsd's sensitive_kind pre-check but fails admind's _checked redact(). The offline fakes report it as posted. Live, it is refused as permanent and ends in ASK_UNPOSTABLE/needs-human.

**Author disposition — §§6:** Expand source-specific matrix to current config/harness/admind/units/exec dirs/create consumers and test actual approval_card/_checked bridge, not helper-only scans.

## 36. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:32

Finding SHA256 `f355e24486a0b22342d2a30cad3c684c4cb431c58db3cd65d00b4aa6e0ab2144`.

> Item 2 asks for a c305 selection 'for the five direct deferred consumers', which include harness.py. But c305's wsd/harness.py is byte-identical to the stale pre-fix T10 (blob c7744c88), is absent on main, and is touched only by 13 rows that are Plan4c T10 commits or merges carrying bb389e3. d2 never says that item 3 alone supplies harness.py. All 21 T10-lineage rows carry the generic 'defer-unresolved-binding' rather than 'superseded by item 3'.

> A literal per-consumer reader either selects stale T10 harness.py rows or declares harness.py unsatisfiable. Severity is low: item 2's gates and line 76 make the natural reading safe, and accounts.py, with zero rows, shows the selection is collective. The overlap between Plan5i rows and corrected T10 (btq.py, daemon.py, scheduler.py) is also never computed.

**Author disposition — §§4:** Corrected T10 is the sole harness/approvals/digests source; inventory stale copies excluded, overlap path discovery retained for later exact hunk resolution.

## 37. docs/reviews/plan5i-integration/r1-disposition-d2.md:4

Finding SHA256 `a3323c5ada450844069241aaccb21a74231a1052c4aa236fa5dc825886b5cbc5`.

> R1's text exists only on this host. It is bound by SHA256 fa821758..., but it is not committed in the range, not quoted, and not linked from the card draft. Every disposition is the same 'addressed in d2 ADR and source-map-d2.json' stamp, written by drafting-script.py:148.

> Off-host, R1 survives only as a hash and one-line stamps. If R2/R3 miss an overclaim (F4/F5/F6/F8/F9 above), no off-host reader can compare it against R1, and the hash cannot be verified if the handoff log is lost.

**Author disposition — §§9:** Publish full verbatim R1 and R2 findings with ordered distinct d3 responses, preserving rejected old author stamps and actual raw report hashes.

## 38. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:92

Finding SHA256 `13d38c64abdddadd7ffa1e5ac651fa1273c45cf9656d536f464187be887c46fd`.

> 'ComputedDigest.display' is not an API: display() and parse_display() are module functions in harness/digests.py (sole commit 42b26cd). ComputedDigest is a public, unvalidated frozen dataclass, so 'does not transform arbitrary caller values' is a call-site convention, not an enforced property, even though it holds at every historical call site.

> A port wraps a value that was not computed by wsd (an upstream checksum, a ghp_ token) in ComputedDigest. display() groups it, and it passes sensitive_kind across the scanned triplet. Only line 53's amendment rule guards against this; there is no structural guard and no named test_harness_digests.py regression.

**Author disposition — §§6:** Correct module function API and unvalidated dataclass trust; verify computed-only callsites and actual full serialized triplet, forbid arbitrary-data digest laundering.

## 39. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:92

Finding SHA256 `5e40c5f52823d1a97e2202775e4ee22122e8445aaf65d0d5078977e24f70bf2d`.

> Line 92's 'specify and review its exact representation or refusal behavior before selection' names no authority. Lines 16-18 and 52-55 route representation changes to a reviewed amendment and genuine design approval, so the clauses add up, but line 92 read alone suggests code review is enough.

> Preflight finds a legitimate serialized field that collides with the scanner: a SHA-256 git commit ID hitting the pre-existing 64-hex rule, or a kebab-case repo name hitting S's inside-word sk-. A controller reading line 92 alone adopts a new grouped representation approved only through code review.

**Author disposition — §§6,8:** Any new legitimate-field representation/refusal departure needs concrete reviewed amendment and genuine Marmot human design approval; code review alone cannot waive scanner contract.

## 40. docs/reviews/plan5i-integration/source-map-d2.json:2

Finding SHA256 `2581191976c60dab43330c9756889578f1da3bcfde9ffe147a3b77d2d995e72e`.

> The schema 'fu366-source-map-d2' is an undefined label, written only by drafting-script.py:24, with no validator anywhere. The uniform null bindings also lump together 'review exists but is unbound' (row 23, 2b6cd92, which has an actual review-5ip1-t1-c1 APPROVE), 'no review' (rows 226-228) and T10's exhausted budget.

> A preflight tool that reads only the ledger rows cannot compute remaining review budgets (d2:38-39, 119-123). It fails closed, but it may plan a redundant review or one that exceeds the budget.

**Author disposition — §§4,7:** Explicitly describe inventory/checklist as evidence rather than executable validation; use distinct binding states and require actual controller/native proof for each mandatory future field.

## 41. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:80

Finding SHA256 `f3e63d2326273425fd38f12bf362e0bcbdbeb520ca5411becb1b37a9efc66b3e`.

> Abbreviated refs remain in d2: '076' and '50fc' at 80, 'c305' at 32/86/102, '076' at 103, b9f8fd1/bf5593 at 76 and bb389e3 at 80. Map prose also has cad581a (never expanded), short current_tail values and 'than4ac749b'. '076' is too short for git rev-parse and is also a prefix of the unreachable 076efdf4. The load-bearing pins are full SHAs, and F10's specific disposition is accurate.

> A mechanical ref extractor or linter cannot resolve '076' or the glued token, and cad581a cannot be expanded from the package. No admission gate depends on these refs.

**Author disposition — §§1,9:** Resolve full source revisions and exact Git blobs/hashes in map; machine-neutral repository/retained-evidence anchors have privately supplied host mapping, no guessed abbreviation expansion.

## 42. docs/adr/2026-10-16-plan5i-current-main-integration-d2.md:76

Finding SHA256 `ac9b06c94686a0575b6e17397eaf2d936a4386352bd4d130dd6fe0978ce497f5`.

> Hygiene. d2 restates T8/T9 but drops R1's 'T12a already in the base' (btq-29lrr, merge a16d86e). Map line 428 reads 'other than4ac749b'. The d1 ADR and source-map-d1.json at HEAD carry no supersession marker, and the d2 map's supersedes_design names only the d1 ADR. The native snapshot (map 231-417) has no capture time or source key.

> A reader opening d1 at HEAD sees 'The integration map is source-map-d1.json' and historical_source 50fc42f, with nothing saying d2 replaced them. The snapshot's age can only be judged through the hash of the off-repo verified-map.json. The T12a gap is an incomplete restatement, not a lost gate.

**Author disposition — §§1,3,9:** Keep d1/d2 immutable rejected history; disclose T12a landed provenance and scoped historical snapshot capture versus current readback, without redispatching source already present.

