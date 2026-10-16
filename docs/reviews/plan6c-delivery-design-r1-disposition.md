# Plan6C actual R1 dispositions — d2

Author Codex. All 33 actual JSON entries are retained in order, verbatim below. Actual verdict REVISE; round1/3 used. Addressed means author proposal, not reviewer approval or tests.

## 1. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 97,
  "summary": "The admind bridge must 'revalidate source eligibility through the trusted owner', but §2 says owner ports are in-process wsd interfaces. That needs an undeclared admind→wsd RPC, forming a wsd→admind(submit)→wsd(verify) cycle, alongside Plan5's existing admind→wsd writer channel.",
  "failure_scenario": "Implementation needs a second, unspecified RPC from admind back into wsd. If wsd's reconciler blocks on submit while admind blocks on wsd verify (or the owner lock), sends deadlock or time out. Each timeout after 'issued' becomes a permanent uncertain part. Review was asked to confirm source-owner ports without cycles, and the design does not establish that."
}
```

**Disposition: addressed in proposed d2**, §2. Replace sole-admind bridge with route-partitioned owners. Source checks are local; WSD→admind read is bounded and occurs before the WSD guard. admind never calls back.

## 2. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 99,
  "summary": "'issued' is persisted before the native call, and anything after it is uncertain with no retry. A wn-agent connect refusal or a write before any byte reaches the socket is therefore classed uncertain, though it is provable non-dispatch.",
  "failure_scenario": "ControlClient._open raises ControlError('socket_io', retryable) on connection refused. A brief wn-agent restart makes every part attempted during the restart permanently uncertain, so cards HOLD indefinitely and need operator recovery. Plan6B d4 explicitly permits treating pre-write refusal as proof of no effect; 6C has no such rule."
}
```

**Disposition: addressed in proposed d2**, §4. Native wrapper records pre-writer.write phase separately. _open failure proves unissued; socket_io or retryable flag alone does not. Later failures are uncertain.

## 3. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 183,
  "summary": "The 60s secondary local alert is anchored on the delivery alert's reservation. If the alert is never reserved, the independent local alert never fires.",
  "failure_scenario": "The control binding's trust guard is unavailable or contended (HOLD), capacity caps pause new admission (line 263), or the clock is held. No control alert is reserved, so the 60s timer never starts and no local alert file is written. This defeats ADR §6.2's path, which is supposed to work without wsd or the gateway's Marmot connection. Anchor it on anchor+600s+60s instead."
}
```

**Disposition: addressed in proposed d2**, §6. Independent local alert due is delivery_anchor+660s regardless of control-alert reservation; capacity/trust failure cannot postpone that origin. Newboot clock ambiguity has a separate fixed local health failure.

## 4. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 155,
  "summary": "The resolver returns only current/superseded/uncertain/not_found, with no partial or incomplete state. A reply to part0 of a copy whose later parts never confirmed resolves as 'current'.",
  "failure_scenario": "An approval copy's part0 is confirmed, the source changes before part1, and the remaining parts hold. The operator reacts 👍 to part0, which lacks the effect/digest parts. The owner gets a 'current' hint for a card whose material facts were never delivered."
}
```

**Disposition: addressed in proposed d2**, §5. Resolver includes partial-current; a confirmed prefix never becomes a complete current card or consent. Mirrors accept no answers.

## 5. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 62,
  "summary": "'Results below are closed' but the bridge result enum is never listed. The results appear only scattered: confirmed, proven_unissued, uncertain, HOLD, integrity HOLD, refuse, not_found.",
  "failure_scenario": "The wsd and admind implementers each invent their own result set. For example, inspect 'not_found' or 'refused-conflict' has no defined wsd part transition. The closed attempt/receipt state machine asked for in review does not exist as written."
}
```

**Disposition: addressed in proposed d2**, §4. Enumerate five result variants, inspect-only not_found, closed reasons/proofs/phases and exact handling.

## 6. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 93,
  "summary": "There is no transition table. The wsd and bridge state machines are conflated, and there is no terminal state for 'proven_unissued after 3 attempts'. A copy with an uncertain part has no state other than 'sending'.",
  "failure_scenario": "After the third proven_unissued attempt, the part and copy state is undefined (held? cancelled?). A permanently uncertain copy shows as 'sending' forever. wsd 'reserved' (persisted before submit) and bridge 'reserved' (persisted after submit) share one name but mean different things for crash recovery."
}
```

**Disposition: addressed in proposed d2**, §4. Explicit local part/copy transition table separates prepared/reserved/dispatch/receipt; exhausted-held and uncertain-held are durable.

## 7. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 62,
  "summary": "submit(attempt_id, binding, parts_hash, part_index, text, reply_target) lacks the copy ID, generation, per-part SHA256, snapshot token and native idempotency_key derivation that the bridge is required to validate, return and use.",
  "failure_scenario": "The bridge cannot recompute parts_hash from a single part's text. It cannot call verify(snapshot_token) without the token, and cannot detect 'duplicate ID bound to another copy' without the copy ID. send_final requires an idempotency key (S4 proves same key returns same IDs), but the derivation is unspecified, so two implementations may pick unstable keys. Plan6B d4 specifies an exact key derivation."
}
```

**Disposition: addressed in proposed d2**, §3–4. Remove bridge submission RPC. The local native intent contains complete source/token/copy/generation/hash/text/reply/key bindings. Native key derivation is exact.

## 8. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 116,
  "summary": "'Never submit under a new ID' conflicts with up to 3 retries after proven_unissued. Same-ID resubmission returns the retained result, so retries need new attempt IDs, but per-attempt ID rules are absent.",
  "failure_scenario": "If retries reuse the attempt_id, the bridge returns the retained proven_unissued and the part never retries. If they use new IDs, a lost-response case could be mis-coded as a retry under a new ID and cause a duplicate send."
}
```

**Disposition: addressed in proposed d2**, §3–4. Only proven_unissued permits next ordinal1..3; inspect/lost response retains original ordinal; ordinary source edits cannot reset exhaustion.

## 9. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 118,
  "summary": "'A missing record alone does not prove unissued' turns a common crash (wsd crashes after its reservation but before submit reaches the bridge) into permanent uncertainty, even though the bridge's write-ahead reservation would make absence proof when the bridge journal is healthy.",
  "failure_scenario": "wsd persists its reservation, crashes, and on restart inspect returns not_found from an intact bridge journal. The part stays uncertain forever and the card HOLDs until operator recovery, for a send that provably never left. Line 119 also contradicts this: a reserved-only bridge record with no issued row is positive proof of non-dispatch."
}
```

**Disposition: addressed in proposed d2**, §4. Healthy intact store absence under fenced admission/dispatch lock is positive proof. Unknown, lost or replaced store is not. Existing row without dispatch requires fenced prior sender.

## 10. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 112,
  "summary": "The design accepts up to 64 IDs per part and picks the first as continuation root. Plan6B d4 treats multiple IDs as uncertain ('do not arbitrarily select a root'), and S4 only observed single-ID receipts.",
  "failure_scenario": "Native returns two IDs for part0 (e.g., an internal split). 6C threads continuations to ID[0] while 6B would stop. The two sibling designs disagree on root semantics for the same native API, and 6C's choice is unproven and not in the human-decision list."
}
```

**Disposition: addressed in proposed d2**, §5. Exactly one validated native ID confirms or establishes root. Multi-ID evidence retained privately but is uncertain; never choose ID[0].

## 11. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 107,
  "summary": "References 'the control protocol's exact validator' for message IDs, but control.py defines FinalSent.message_ids_hex as a plain list[str] with no ID validator.",
  "failure_scenario": "The implementer has no validator to call and either writes an ad hoc one or accepts any string. The design also says IDs are 'not assumed 32-byte', which contradicts Plan6B's pinned 32-byte native IDs."
}
```

**Disposition: addressed in proposed d2**, §5. Define exact64-lowerhex32-byte account/message validator aligned with6B. FinalSent is acknowledged to have plain list[str], not a callable exact validator.

## 12. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 186,
  "summary": "The 10-minute escalation and 60s secondary deadline depend on an unspecified Plan8 invocation cadence. Plan8's proposal has reconcile at 5m and reminder_sweep at 15m, and does not map 6C delivery reconcile to any job.",
  "failure_scenario": "If 6C is driven by the 15m reminder_sweep, the ADR's 10-minute alert can fire at about 25 minutes and the '60s' local alert at about 15 minutes. The approved numbers become fiction while tests on injected clocks pass."
}
```

**Disposition: addressed in proposed d2**, §6. Owner-local nearest-due callbacks and30s recovery sweep service delivery deadlines; do not rely on Plan8 five/fifteen-minute ticks.

## 13. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 123,
  "summary": "The 1s then 5s retry backoff cannot be honored: the library is forbidden to sleep, poll or self-trigger, and only Plan8 ticks invoke it.",
  "failure_scenario": "After proven_unissued, the next attempt happens at the next Plan8 tick (minutes), not at 1s/5s. Clock tests asserting 1s/5s test a schedule that never occurs in production."
}
```

**Disposition: addressed in proposed d2**, §4,§6. Durable due state wakes the async owner at1s/5s. Reducer does not sleep, poll or await native send; explicit callbacks replace d1 self-trigger prohibition.

## 14. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 225,
  "summary": "The 5s reconcile wall budget conflicts with synchronous bridge operations whose native send uses ControlClient's 30s timeout. Nothing says whether hitting the budget abandons an in-flight RPC.",
  "failure_scenario": "wsd stops waiting for submit at 5s while the native send continues. The response is lost and the part becomes uncertain with zero retries. A slow relay turns budget expiry into permanent HOLDs."
}
```

**Disposition: addressed in proposed d2**, §4,§6. Five-second reducer is local-only. Independent async worker owns send with30s TOTAL deadline including cleanup; reducer expiry never abandons it.

## 15. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 217,
  "summary": "More than 1s divergence between UTC and BOOTTIME holds due generation, with no rule for re-anchoring or recovering from the hold.",
  "failure_scenario": "NTP slew (up to 500ppm, about 7s over 4h) or a small step exceeds 1s. All reminders, digests and alerts, including the local alert, stay held indefinitely after normal clock discipline."
}
```

**Disposition: addressed in proposed d2**, §6. Recent elapsed-clock samples avoid cumulativeNTP drift; explicit newboot sync/recovery and30s readiness reestablishment. Preserve due anchors/keys; monotonic fallback remains eligible sameboot.

## 16. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 203,
  "summary": "The 00:00→08:00 change makes 'the next UTC-date digest' ambiguous, and the digest includes only cards past their 4h event, while ADR §9 and Plan8 say the digest covers open approvals.",
  "failure_scenario": "For a 4h event at 03:00 UTC on day D, implementers disagree on D 08:00 versus D+1 08:00 (up to 29h of delay). An approval opened at 05:00 is absent from that day's 08:00 digest although it is open, contrary to ADR §9's 'open approvals'."
}
```

**Disposition: addressed in proposed d2**, §6. UTC08 digest includes ALL current open cards regardless of reminder age, explicit D/D+1 examples and closed-yesterday interval.

## 17. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 204,
  "summary": "The digest time cites 'the proposed Plan8 default', but Plan8 (an untracked file in btq-8d487) is not pinned in the manifest or listed as a prerequisite. Plan8 also specifies a configurable IANA timezone and civil time, while 6C hard-codes UTC dates.",
  "failure_scenario": "A deployment configures a non-UTC timezone in Plan8. Plan8's civil-day occurrence key and 6C's 'one digest intent per UTC date/control binding' disagree, causing duplicate or missed digests. The compatibility manifest cannot catch this because Plan8 is not an input."
}
```

**Disposition: addressed in proposed d2**, §6 and manifest. Pin exact provisional Plan8 design and inputs bytes; adapter only accepts UTC08. NonUTC Plan8 config disables/refuses this adapter; no key reinterpretation.

## 18. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 205,
  "summary": "A 'trusted digest producer' supplying ADR §9 summaries is introduced but is absent from the §2 owner/port table and from the activation prerequisites. The existing 'Plan5/6D' owner refers to a 6D design that does not exist anywhere.",
  "failure_scenario": "The final compatibility manifest has no named owner or port to freeze for the digest summaries or the 6D owner, so the production-activation gate cannot be evaluated."
}
```

**Disposition: addressed in proposed d2**, §2. Name bounded WSD digest projection+admind display facts as proposed producer and actual compatibility prerequisite. Delete nonexistent6D dependency; source unavailable is honest.

## 19. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 195,
  "summary": "The reminder is 'one per logical revision' and the 10m alert is keyed by (source key, revision, digest), so source revisions reset both. ADR §6.2 allows one reminder per card.",
  "failure_scenario": "An approval source revised three times (e.g., enrichment) produces three 4h reminders and up to three delivery alerts, violating 'one reminder after 4h… after that, only the daily digest'. 'Logical revision' is undefined."
}
```

**Disposition: addressed in proposed d2**, §3,§6. Stable card identity owns reminder/alert uniqueness across revisions/renderers/mirrors/recovery; revision changes cannot reset budgets.

## 20. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 196,
  "summary": "Approval and question cards have independent workstream and control copies, but the single reminder's target copy and surface are unspecified.",
  "failure_scenario": "Implementations might send to both (two reminders), to either nondeterministically, or to the workstream copy while the control copy is the operator's surface."
}
```

**Disposition: addressed in proposed d2**, §6. Canonical approval/question reminder owned byadmind and targets only complete current control cardpart0, never both routes.

## 21. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 138,
  "summary": "Supersession cancels only proven-unissued parts, and late full receipt cancels only a 'still proven-unissued' alert. Prepared or never-submitted parts and alerts are not cancelled.",
  "failure_scenario": "A source is superseded after part0 is confirmed. Prepared part1 of the stale copy remains eligible and is sent as a continuation of an obsolete card. A prepared, unsent delivery alert is still sent after a full receipt arrives."
}
```

**Disposition: addressed in proposed d2**, §4,§6. Cancel prepared, never-submitted, held-before-dispatch and positively unissued parts/alerts when condition clears. Preserve issued uncertainty/history.

## 22. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 241,
  "summary": "Journal-loss recovery requires explicit Marmot recovery/deployment authority. ADR §3.3 makes re-posting open cards with a 're-issued after recovery' note the automatic outcome, and this departure is missing from the §7 list of human choices.",
  "failure_scenario": "The human approves the §7 list without seeing that post-loss re-issue now needs a separate approval. After journal loss, open approval cards stay unposted until a manual request, so operators may never see pending approvals."
}
```

**Disposition: addressed in proposed d2**, §7–8. Restore automatic marked current-open-card reissue under approved implementation after loss. No new per-copy permission; live file restoration/deployment still keeps existing authority.

## 23. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 29,
  "summary": "Existing admind asks and interim bumps continue unchanged, but nothing forbids one source being both an admind ask and a 6C approval/question copy, or says how admind autobump is excluded for 6C cards (an open Plan8 compatibility item).",
  "failure_scenario": "During coexistence, the same approval appears as an admind ask and a 6C card, giving two answer surfaces and bump policies for one decision, plus admind autobump repeating reminders. The 'no duplicate decision writer' review focus is unproven."
}
```

**Disposition: addressed in proposed d2**, §2,§6. Canonical ask is sole control decision card; workstream mirror is informational. Protected namespace transaction suppresses managed ask legacy autobump; unrelated asks unchanged.

## 24. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 64,
  "summary": "The bridge 'must use the same registration/trust owner and effect boundary as 6B', but Plan6B d4 places the native send wrapper and the single wn-agent transport owner in wsd and refuses any route matching admind's group, while 6C forbids wsd any control socket.",
  "failure_scenario": "The two contracts cannot both hold: either admind becomes a second wn-agent transport owner for workstream groups, contrary to 6B, or 6C's 'admind sole bridge' fails. The conflict is not named as a required compatibility resolution."
}
```

**Disposition: addressed in proposed d2**, §2. Keep6B registered route ownership and disjoint admind routes. Proposed6C full-card capability stays with WSD owner, not read consumer. No6B source edit.

## 25. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 184,
  "summary": "'Atomically write the local alert' does not define ordering between the alert file write and the journal record, or what happens after a write failure.",
  "failure_scenario": "If the journal marks the alert written and then the process crashes before rename, the alert is never written. Order reversed, a crash duplicates or orphans the file. A failed write (ENOSPC) has no defined retry, so the fallback silently ends."
}
```

**Disposition: addressed in proposed d2**, §7. Persist desired filename/hash, write+fsync+rename+dirfsync, then record written. Crash verifies exact existing file; failure remains durable pending for bounded retry.

## 26. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 190,
  "summary": "Local alert files have no cleanup, cap or namespace. admind never deletes them ('cleanup is the writer's job') and rescans the whole directory every poll. Plan8 caps its own files at 256 but 6C has no cap.",
  "failure_scenario": "Alert files accumulate without bound, slowing every admind alerts_loop scan. Name collisions with Plan8's ids are possible. A 64-hex 'stable opaque id' would be redacted as an identifier in admind's malformed/withheld notices."
}
```

**Disposition: addressed in proposed d2**, §7. Distinctd6c_32hex namespace,256unsettledfiles, existing data caps, explicit local capacity diagnostic and scoped archive/dedup cleanup.

## 27. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 86,
  "summary": "Content that cannot fit is 'visibly held' and returned to the owner, whereas ADR §6.2 says an over-long card shows a summary plus pinned links with full text on the bead. This deviation is not in the §7 human-decision list.",
  "failure_scenario": "A large approval is held instead of rendered as summary plus links, delaying the decision. The operator approves the design without seeing this ADR departure."
}
```

**Disposition: addressed in proposed d2**, §5,§8. Use actual final6A safe summary+pinned-source-link overflow with material facts and durable full source. If no compatible renderer exists, explicit HOLD prerequisite; no silent truncation.

## 28. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 88,
  "summary": "The copy uniqueness key omits renderer identity, so re-rendering after a renderer or redactor upgrade with the same source revision collides as an 'integrity HOLD'.",
  "failure_scenario": "After a 6A renderer version bump, recovery or reissue of an unchanged source produces different bytes under the same key and holds permanently instead of producing a new generation."
}
```

**Disposition: addressed in proposed d2**, §3,§5. Renderer/redactor identity joins copy uniqueness. Upgrade newgeneration cancels unissued oldparts and retains receipts/cardbudget.

## 29. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 10,
  "summary": "The governing ADR is cited as `docs/adr/0001-workstreams-v2.md` at a27acece without naming its repository. In this repo that path resolves to the revision 15 draft, and a27acece is not an object here.",
  "failure_scenario": "A reviewer following the in-repo path reads r15, which lacks the r16/r17 amendments, and checks the design against the wrong ADR. The ADR repo also has no remote, so the pin cannot be reproduced off-host."
}
```

**Disposition: addressed in proposed d2**, §1 and manifest. Name exact separateADR repository, a27Gitblob+SHA, genuineovngv metadata and required accessible exactbyte publication; never pretend productr15 isr17 or inventremote.

## 30. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 69,
  "summary": "The bridge's separate versioned attempt namespace reimplements admind's existing outbox Store: pending rows, attempt counting, idempotency keys, resolve_thread with 'parent-held', and sent_row. Only the retry policy differs.",
  "failure_scenario": "Two parallel send stores in admind, each with its own thread-resolution and receipt logic, doubling the maintenance and divergence risk. Simpler alternative: a per-namespace no-retry policy and multi-ID receipt capture in the existing outbox."
}
```

**Disposition: addressed in proposed d2**, §3–4. Reuse existingadmind Store outbox/pending/attempt/thread/receipt. Versioned protectednamespace extension changes policy; no duplicatebridge DB. WSDonly has itsown route maps/store.

## 31. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 188,
  "summary": "Both 6C and Plan8 independently specify an atomic alert-file writer against admind/alerts.py, which today has only a reader.",
  "failure_scenario": "Two writer implementations with different temp-file, fsync and naming rules. One shared writer in admind/alerts.py would enforce the contract once."
}
```

**Disposition: addressed in proposed d2**, §7. One shared writer added toadmind/alerts.py used by6C/Plan8, with exact ordering/namespace/cap; reader-only currentstatus disclosed.

## 32. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 58,
  "summary": "Source verification is done twice per part across a process boundary: verify() 'before each part' and the bridge's revalidation through the owner.",
  "failure_scenario": "There are two owner round-trips per part within a 5s budget of 20 operations, and it is unclear which check is authoritative. Specify a single check inside the bridge, under the trust guard."
}
```

**Disposition: addressed in proposed d2**, §2,§4. One owner source check perpart; no verification callback and no duplicated roundtrip. Remote snapshot refresh happens outside localguard/transaction.

## 33. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 228,
  "summary": "'Operator direct work and genuine pending approvals do not prevent unrelated ready tasks' is task-pickup policy in a delivery reconciler that excludes pickup.",
  "failure_scenario": "An out-of-scope statement that implies a scheduling obligation 6C cannot implement, confusing the acceptance criteria."
}
```

**Disposition: addressed in proposed d2**, §1,§6. Remove task pickup obligation;6C only delivers and reports, unrelated effects remain their actual owners.
