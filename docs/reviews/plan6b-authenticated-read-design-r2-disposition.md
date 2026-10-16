# Plan6B actual design R2 dispositions

Status: author-proposed d3 corrections for parent inspection; not a reviewer verdict, human approval, implementation admission or test result.

Actual R2 reviewed `07637745fe75dc614109a0b8ba72d3489f262a97..dd58209001061d5228ca9f50ce2ee618e7acf367`. Raw report SHA256 `b4d8c2beef17c0591e5f3bdb6b3f676879325cfa9c015dbb08472e6820374779`. Actual process returned 0, ended `2026-10-16T07:32:04.212740+00:00`. Its fenced JSON has **21 entries**, preserved below in actual order with exact summaries and scenarios. R1 had 27 actual entries despite prose saying 26. R1 and R2 remain rounds 1 and 2 of maximum 3; only R3 remains. Requested model/effort were opus/high; no actual inner reviewer model or separate verification pass is asserted.

All resolutions below are design requirements and proposed acceptance cases. No runtime tests, queue operations, native sends, consumer startup, review invocation, implementation or deployment were performed for this revision. The final compatible 6A pin, actual read-role/effect proof and owner-side recovery proof remain explicit prerequisites, not invented current APIs.

## 1. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:134`.

Exact summary:

> The stable cache key includes the scope digest, but §4.1 (line 47) defines scope to include ProducerBinding(release_commit, run_id, boot_id), so run/boot identity is still in the cache and single-flight key and R1 #9/#10 are not actually fixed.

Exact failure scenario:

> wsd restarts while Beads is unreachable: the new run_id/boot_id changes the scope digest, so no persisted entry has an 'unchanged scope' and the historical-stale fallback can never match. /status returns Unavailable instead of an answer from cache with a warning, which is exactly the R1 #9 scenario. Two concurrent refreshes across a producer change also get different keys, defeating coalescing (R1 #10).

**Disposition:** Accepted; corrected in §§4.1 and 8.

ContentScope includes only surface, native route, registration epoch and sorted exact workstream bindings. It excludes producer release, run, boot and generation. Fresh request authentication separately binds ProducerBinding; historical envelopes retain their original binding. The stable refresh key does not change on an owner restart. Required fixtures cover warm old-run fallback during a native outage and concurrent refreshes across a producer change.

## 2. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:39`.

Exact summary:

> The producer is told to run deferred read transactions on 'that owner's existing initialized connections', but source076 wsd has exactly one shared Journal connection (check_same_thread=False, serialized by a Python RLock) that the scheduler uses for BEGIN IMMEDIATE writes.

Exact failure scenario:

> If the read handler issues BEGIN without Journal.lock while a scheduler thread is inside transaction(), SQLite raises 'cannot start a transaction within a transaction'. If its BEGIN lands first, the scheduler's BEGIN IMMEDIATE fails; that is not a busy error, so _busy() passes it through and pickup fails or the journal is poisoned. Reads can also see uncommitted scheduler writes. If the handler takes the lock instead, each read (up to a 5 s budget, 4 workers) blocks every scheduler journal operation, and lock waits are not bounded by BUSY_TIMEOUT. The design needs a separate owner-opened mode=ro connection.

**Disposition:** Accepted; corrected in §§3–4 and acceptance 4.

The owner opens a separate SQLite mode=ro/query_only connection for bounded deferred SELECT transactions. It never shares Journal.db, Journal.lock, constructor or write helpers. The design explicitly permits owner-local WAL coordination sidecars while prohibiting authoritative row/schema/journal-mode changes. Actual disposable WAL/concurrent-writer fixtures must prove committed-only reads and no scheduler lock interference.

## 3. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:37`.

Exact summary:

> The consumer gets only a 'response-only native send wrapper' (and §2 says 6B posts only read responses), yet §5 line 90 requires 6B to send an unsolicited control alert to a different trusted control route.

Exact failure scenario:

> A membership change holds route A. The alert to control route B is not a reply to any query on B, so a response-only wrapper cannot send it. Either the implementation widens the send capability (breaking §4's closed capability set and the acceptance-12 test), or the alert is never sent and the R1 #12 'nobody alerted' failure remains. Unsolicited control-route posting also overlaps the 6C outbox ownership that §1 reserves.

**Disposition:** Accepted; corrected in §§4–5.

The capability list now names four query capabilities and a separate closed SafetyAlertPort. The latter accepts only fixed local safety reasons/correlation, asks existing admind NoticePost to deliver a fixed notice, and cannot choose text or destination. It is an explicit minimum safety exception, not unsolicited posting through the response wrapper or general 6C cards. Pending/uncertain alert behavior is durable and bounded.

## 4. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:88`.

Exact summary:

> Trust recovery requires the config script to 'validate that native decision binding' from an authenticated Marmot approval, but no verifiable decision record or API exists. §4.2 itself says native approval records are display rows, 'not ... evidence of consent', and executing an approved decision falls under Plan5's sole decision-writer/executor ownership.

Exact failure scenario:

> After any hold, recovery needs a script that reads and verifies an admind or Plan5 decision. The implementer must either invent a decision-verification schema or API (silent schema invention, crossing the Plan5 boundary) or leave recovery unimplementable. Deployment is gated on that script, so 6B either cannot deploy or holds routes permanently, and R1 #12 is only renamed.

**Disposition:** Accepted; corrected in §5 with an explicit compatibility prerequisite.

Recovery is a separate operator configuration workflow using actual AskPost and AskGet DTOs, a durable full request/post receipt and exact digest-bound association. Existing AskView has no body or current sender-key proof. No title or operator-name field is treated as sufficient authority. Apply remains held until a reviewed, narrowly read-only admind-owner compatibility adapter proves the exact stored request/answer provenance and current policy intersected with confirmed keys. Missing/pruned proof requires a real fresh question, not invented consent. This prerequisite is disclosed before deployment; it is not advertised as an installed endpoint or a Plan5 executor.

## 5. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:69`.

Exact summary:

> The §4.3 mapping (explicit 'refreshing', per-source unreachable warnings, per-collection counts) and §7's absolute rendered-at timestamp on every response require fields that provisional 6A lacks. 6A has a single observed_at, cache_state fresh/refresh-required, a fixed '⚠️ beads unreachable' warning and one view-level shown/total. 6B forbids adding 6A fields and falls back to a whole-response 'fixed unavailable'.

Exact failure scenario:

> Under a 6A like 7903439, any stale-cache, single-source-outage or partial view cannot be represented, so the whole /status or /queue is fixed unavailable. This regresses the ADR §6.3 'answer from cache with warning' behaviour that R1 #17/#18 asked to preserve. The needed 6A changes are not filed as an explicit cross-design dependency; the design calls them 'ordinary validation'.

**Disposition:** Accepted; corrected in §4.3.

Final genuinely approved and implemented 6A compatibility is an explicit dependency. Provisional 7903439 lacks the full outage/cache/fixed-notice profile. No new field is silently added, no frozen 6A is edited, and no wholesale unavailable fallback is claimed as cache support. The compatible final pin or a genuinely reviewed presentation amendment must provide the stated semantics before integration. This design remains approvable as a contract with a validated later input.

## 6. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:117`.

Exact summary:

> The query state machine is still incomplete. partial-suppressed and partial-expired appear as targets but have no terminal or recovery row. Authorization going stale or unavailable between parts has no partial state. Exhausting the 'bounded unsent retry' after a no-effect refusal has no terminal state. The 'bounded admission lifetime' for waiting-auth is never given a number.

Exact failure scenario:

> Part 1 is delivered, the 30 s group observation expires, and the recheck before part 2 times out. That is neither 'revocation' nor 'aging budget', so the only available terminals are auth-unavailable (losing the delivered fact) or delivered (false), which is the R1 #14 scenario again. On restart, a row in partial-expired has no defined recovery rule. ControlClient's default 30 s timeout can stretch an undefined waiting-auth window.

**Disposition:** Accepted; corrected in §7 and acceptance 9–10.

Every partial outcome is explicitly terminal, including partial authorization failure and exhausted no-effect retry. Auth checks are five seconds including cleanup, ready first-attempt expiry is 30 seconds, admission lifetime is 60 seconds with at most two pre-attempt rebuilds, and multipart uses the earlier admission/30-second deadline. Restart never resets a clock or resumes a terminal state; clock uncertainty expires rather than prolongs an admission.

## 7. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:122`.

Exact summary:

> The busy/quota refusal is handled 'before ordinary reservation', so the busy-answered message ID gets no replay reservation and can later be dispatched again as a real query.

Exact failure scenario:

> Under load, message M gets the busy reply with no replay row. After reconnect or redelivery, or once load drops, M arrives again, reserves normally, and gets a full /status reply. One native message is dispatched twice under different categories, violating line 106's 'Message IDs cannot dispatch twice', and the operator sees busy followed by a delayed answer.

**Disposition:** Accepted; corrected in §7.

Busy uses the same native message replay key and a durable busy semantic outcome before any reply. A reserved rate slot does not replace the key. If no durable acceptance space exists, there is no reply/dispatch and a later redelivery may be accepted once. This distinction prevents busy followed by a later full answer for one already accepted message.

## 8. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:128`.

Exact summary:

> Replay, reservation and send evidence is never evicted and lives inside a fixed 64 MiB non-cache budget. The only escape is a 'rollover' the design itself calls unproven and approval-gated, so normal use runs into a permanent stop of all authorized reads.

Exact failure scenario:

> Each answered query keeps its reservation, response revision (up to 20 rendered parts), attempts and receipts. Over weeks or months the 64 MiB fills, new ordinary reservations stop, and every /status gets silence until an approval-gated rollover exists. R1 #11's 'authorised /status queries stop being answered' is deferred, not fixed. Root fix: after a terminal state, compact each row to its replay key plus outcome/receipt digest and drop the rendered bytes.

**Disposition:** Accepted; corrected in §7.

Terminal compaction drops rendered payloads/full DTOs while retaining exact replay identity, payload/provenance digest and actual receipt evidence. Explicit capacity thresholds, reserved evidence space and bounded archive-plan/apply preserve keys without an assumed native window. The finite 32-segment/1 GiB archive limit has an honest capacity hold and real admind escalation; ordinary supported compaction/archive movement needs no new design approval. No unlimited-storage or perpetual-availability claim remains.

## 9. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:61`.

Exact summary:

> BeadStatus scoping checks only for 'exactly one valid ws routing label equal to a permitted binding'. It does not bind the agent: label or queue identity, which btq's Queue.matches uses alongside ws:, in the shared Beads database.

Exact failure scenario:

> Another agent's bead labelled agent:codex ws:foo, in the same shared database, passes the ws:foo check in a group bound to wsd's agent:claude ws:foo queue. Its state, branch and launches are shown even though it is outside the bound queue identity. Approvals (line 62) say only 'in registered scopes', with no equivalent exact-label rule.

**Disposition:** Accepted; corrected in §§4.1–4.2 and 6.

Bindings include the exact routing agent, workstream, native queue/database/server and repository identities. Both BeadStatus and Approvals require exactly one matching valid agent label and one ws label; conflicting, absent or other-agent rows are nonselectable with identical public not-found behavior. Display labels alone never establish scope.

## 10. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:63`.

Exact summary:

> Queue requires an 'explicit paused' observation but names no source for it. At source076 the pause flag is the btq state file reached through Queue/ws_queue (forbidden to the read path), and the only owner copy is the derived workstreams.state written in Scheduler.publish().

Exact failure scenario:

> An implementer either builds a Queue or touches the btq state directory (violating §3/§4 and acceptance 4), reads the possibly stale workstreams.state as fresh pause truth, or leaves pause unknown so that /queue is always unavailable(ordering-unproven).

**Disposition:** Accepted; corrected in §4.2.

Pause is read from the fixed trusted parent-state flag resolved for the configured agent/host/session. No Queue construction or mkdir is allowed. An absent flag inside a verified existing safe parent-state directory means unpaused; a missing/unsafe/inaccessible directory or failed read is unknown. Owner-published copies retain their original observation time and are not described as fresh native truth.

## 11. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:63`.

Exact summary:

> Bounded native new candidates have to reproduce btq Queue.ready's post-filter, matches() plus design_allowed(), which runs an extra bd show per design-gated candidate. That is N+1 subprocesses inside the 5 s aggregate budget, plus a hand-copied eligibility filter that will drift from btq.

Exact failure scenario:

> Fifty ready kind:task beads with design_approval metadata need 51 bd calls against Dolt. That cannot finish within 5 s on 4 workers, so eligibility is always 'unknown' or the queue is unavailable. A reimplemented matches() that diverges from btq lists beads btq will never hand out. Cheaper and reusable: a bounded read-only btq subcommand with a limit (make_state=False, read credentials).

**Disposition:** Accepted with a bounded descriptive projection rather than a duplicate scheduler; §§3–4.2.

The adapter has at most four fixed bulk native operations under the aggregate deadline. It cannot run per-row design-approval show loops. Candidate eligibility can be explicit unknown, and the view promises resumable-first/new-ready candidate order with a warning that actual pickup can skip or change. A fully verified native inventory/count/order is still required. A future bounded read-only btq interface is reusable only after actual reviewed implementation and real effect tests; make_state=False alone is explicitly insufficient.

## 12. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:53`.

Exact summary:

> 'Complete is allowed only if every required section is complete', and §4.3 handles 'required' and 'optional' sections differently, but no selector ever says which collections are required. The 100-row descending events window will also almost always mark recent-events incomplete.

Exact failure scenario:

> For /status <bead> on a busy system, the bead's last event is more than 100 global rows back, so recent-events is incomplete. If that collection is required, BeadStatus is never complete or is fixed-unavailable. If it is optional, different implementations will disagree. Altitude: have 6B maintain its own derived (ws, bead) index by tailing the events cursor into the 6B DB, rather than accepting a window that is nearly always incomplete.

**Disposition:** Accepted; corrected in §4.2.

Required and optional collections are now enumerated per selector. Recent events are optional for BeadStatus, so an honest bounded incomplete window cannot erase the scoped native bead facts. Optional failures mark aggregate incomplete and explicit unknown sections. A separate tailing/index producer would expand this read slice and is unnecessary to satisfy this contract; it is not silently added.

## 13. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:98`.

Exact summary:

> The d1→d2 rewrite dropped three invariants without replacing them: the per-surface query table (control /status includes open approvals and needs-human; control /queue is visibly partitioned by workstream), 'Renderer gets a scoped DTO, never an unfiltered global snapshot to redact later', and 'query results never include another requester's source text'.

Exact failure scenario:

> An implementer builds one global projection and filters it at render time, which d1 explicitly forbade. A redaction bug then leaks another workstream's rows into a workstream group. Control /queue output can also be unpartitioned, because nothing in d2 requires partitioning any more.

**Disposition:** Accepted; restored in §6 and the query surface table.

The producer constructs a scoped DTO before renderer entry; no global snapshot is handed over for later redaction. Another requester’s source text is excluded. The table explicitly preserves control Status open-approval/needs-human slots and control Queue partitions by registered workstream. Row scope is independently validated before adoption.

## 14. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:116`.

Exact summary:

> send_final requires an idempotency_key, but d2 never says whether a 'definite no-effect refusal -> bounded unsent retry' reuses the persisted key or mints a new one. The mention of send_final's idempotency key was dropped from d1.

Exact failure scenario:

> A refusal that was misclassified as no-effect is retried under a fresh key, and the native side delivers both, producing a duplicate part. Reusing the key without a known native dedup lifetime has the opposite risk. The design leaves both open.

**Disposition:** Accepted; corrected in §7.

A deterministic persisted send key binds canonical version, exact account/group, registration epoch, native query ID, revision digest and part index. One permitted retry reuses that exact stored key and is allowed only on proven local failure before request dispatch/write. Peer retryable errors or missing results after dispatch are uncertain and never replay. No assumed native dedup lifetime is needed.

## 15. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:41`.

Exact summary:

> d2 pre-authorizes future work ('adding a fixed nonmutating adapter within these bounds is implementation work, not a new design permission cycle'; §10 line 163 'No ordinary unchanged-contract edit needs another design permission'). It also removed d1's gate that the early-live slice stays disabled pending a separate integration design if the source cannot provide isolation.

Exact failure scenario:

> /home/openclaw/.claude/CLAUDE.md: 'Implementation tasks require a design approval per design ... Producing a design is not authorization.' The native operation shapes, read role and owner IPC are unknown today, but the design lets them be added later without design review. A read adapter whose real bd behaviour or credentials differ would then ship without a design gate. <!-- install-agnostic: allow=home-path; exact historical reviewer quotation, not a configured path. -->

**Disposition:** Partly accepted; isolation gate restored, authority inference rebutted in §§1, 4 and 10.

A design is not implementation admission, and deployment remains separate. The early-live path is disabled if real source isolation/privilege behavior cannot fit the approved contract; changed effects require a reviewed amendment. However personal Claude configuration is not governing authority for this Codex session, and the user permits ordinary implementation inside genuinely approved contracts. A new approval per adapter function or unchanged-contract edit would invent an approval cycle. The fixed-operation/read-role/owner-port boundaries are explicit proposed contracts and must be proven, not presumed installed.

## 16. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:51`.

Exact summary:

> The claim that 1024 observations 'accommodate 100 workstreams without conflicting with the 256-KiB envelope cap' only covers observation metadata. Data-bearing control views for many workstreams exceed 256 KiB, so the R1 #7 disposition is overstated.

Exact failure scenario:

> A control /queue or /status across 50–100 workstreams, each with up to 100 rows per collection, makes the envelope plus 6A view far larger than 256 KiB. It always returns source-budget (or NeedsDetails), so large control surfaces are permanently unusable for those views, the same outcome R1 #7 described with a different cause.

**Disposition:** Accepted; corrected in §4.1 and acceptance 6.

The 1024 cap bounds only metadata, not a full 100-workstream dump. Bounded summaries and deterministic row allocation preserve all registered bindings and verified omission counts. If even minimal summaries exceed the frame, the closed NeedsDetails result produces a fixed narrow-query notice. Workstream-specific requests remain available. No arithmetic claim says 100 collections of 100 full rows fit 256 KiB.

## 17. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:106`.

Exact summary:

> The replay key '(account,group,message,event-ID)' has an event-ID component that inbound messages do not have (InboundMessage carries only message.message_id_hex). The state-event hold is keyed by a 'reason' taken from GroupStateChanged.change, which is a free-form native string.

Exact failure scenario:

> Implementations disagree on what fills event-ID: a NULL breaks uniqueness, and a copied message ID makes the column redundant. Unrecognized `change` strings either create unbounded distinct hold keys or are silently ignored, because the design does not say whether they map to a closed reason enum or fail closed.

**Disposition:** Accepted; corrected in §§5 and 7.

The sole query replay key is exact account/group/message.message_id_hex. Optional-ID reactions are ignored; group events coalesce a hold by route epoch plus a closed reason enum. Arbitrary change strings map to unknown-group-change, never an unbounded stored key. No nonexistent event-ID field is consumed.

## 18. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:84`.

Exact summary:

> 'Reject ... any alias of admind's configured own account/group' is ambiguous: it could mean the account alone or the account/group pair. If it means the account, deployment needs a separate native identity already in the groups, which is not listed as a prerequisite, and group creation and invites are excluded.

Exact failure scenario:

> Under the account reading, the operator's plan to reuse the wn-agent account with a different group is refused at startup, with no supported way to provision a second identity. Under the pair reading, an implementer may allow the same account and miss cross-daemon replay overlap. The two readings produce opposite deployments.

**Disposition:** Accepted; clarified in §5.

Same account with a distinct non-admind group is allowed under the one transport owner. The exact admind account/group pair and reuse of the admind group with another account are refused. The native subscription and replay namespaces remain exact account/group bindings. No implicit second identity, MLS-home opener or invitation is required.

## 19. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:122`.

Exact summary:

> 'Native duplicate detection remains authoritative in the retained epoch' contradicts §3's statement that no native deduplication lifetime is established.

Exact failure scenario:

> An implementer reads this as permission to rely on wn-agent or native dedup instead of local replay rows, for example to skip a reservation, and a redelivered message dispatches twice once the unknown native dedup window has passed.

**Disposition:** Accepted; corrected in §§3 and 7.

Local durable replay uniqueness is authoritative for accepted messages. No native dedup horizon is established or used to forget a key. A send key is not a claim of transport retention. Archive lookup failure holds admission instead of skipping a duplicate check.

## 20. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:147`.

Exact summary:

> Acceptance 4 proves side-effect freedom only with fail-fast fakes for Journal and Queue. Nothing requires checking the real bd subprocess, which runs with -C on a repo .beads directory, for local file writes under the read role.

Exact failure scenario:

> The real bd binary writes local .beads state (locks, metadata, auto-migration) or needs write grants at startup. Every fake-based test passes, but the deployed 'read-only' adapter mutates repository state or fails under the read-only Dolt role. Nothing named exercises a disposable Dolt with real read-only grants.

**Disposition:** Accepted; added to acceptance 4 and deployment prerequisites.

Fake constructor guards are necessary but insufficient. The actual pinned bd must run in a disposable copied workspace against a disposable Dolt database with actual read-only grants. Verify denied writes, permitted bounded reads and before/after files/grants/schema, including locks/metadata/migration. These are future isolated offline fixtures; no such process, database or test was run for this design revision. Production state or credentials are prohibited.

## 21. Actual finding

Original location: `docs/designs/2026-10-16-plan6b-authenticated-marmot-read-surface.md:84`.

Exact summary:

> Normative limits are written with words and numbers run together (e.g. 'group evenhex1–256bytes', '64MiB non-cache', 'maximum3', 'separate6Bschema', 'FullPlan5/6/7/9'), here and in the disposition, which makes contract values easy to misread.

Exact failure scenario:

> Implementers and reviewers parse limits like '<=8perworkstream', '16MiB plus64MiB' or 'aggregate5s' inconsistently. This raises the chance of a wrong constant in the closed contract, and makes word-level comparison of the contract hard.

**Disposition:** Accepted; current normative prose and this disposition use ordinary spacing.

Version numbers, byte/time limits and scope labels are written with spaces and units. Exact historical R1/R2 summaries/scenarios and original artifacts remain unchanged as review evidence; they are not silently rewritten to hide the findings. Current normative sections supersede earlier wording.
