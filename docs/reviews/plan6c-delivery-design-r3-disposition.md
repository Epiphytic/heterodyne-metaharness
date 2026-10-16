# Plan6C actual FINAL R3 dispositions — UNREVIEWED d4

Author Codex. All15 actual JSON findings are reproduced verbatim in original order. Actual verdict REVISE; all THREE issued rounds consumed, no fourth invocation or budget reset. These are author proposals, not tested implementation or reviewer approval. Historical d3/R1/R2 files are unchanged.

Raw report SHA-256: `1a9d7816b95fbe67d5fee8cf718a904a808e8ee70e97e6779f6dea09db727f28`; reviewed d3 `4acc2d5a2d6ee3e2266ff465271a72c419493723`. New design: `docs/designs/2026-10-16-plan6c-durable-delivery-d4.md`.

## 1. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 209,
  "summary": "The closed result model doesn't classify a well-formed wn-agent error frame (PeerError: relay_unavailable, rate_limited, unavailable, message_send_failed, often retryable=true) received after the request write. Prewrite-refusal covers only `_open`, and 'after write begins is uncertain' lists timeouts and malformed replies, not definitive peer error replies.",
  "failure_scenario": "The Nostr relay is briefly down. wn-agent accepts the connection and answers each send_final with {type:error, code:relay_unavailable, retryable:true}. That isn't a prewrite proof, so every protected part (canonical cards, the 600s control alert, the digest) becomes uncertain-held. Uncertain never retries automatically, and delivery_recover is barred because the history doesn't prove non-dispatch. Every card then needs its own authenticated marked reissue. The other implementation choice, treating it as proven_unissued, risks duplicates. The 0/5/30 fix for R2#4 covers only socket-down, not this common transient."
}
```

**Disposition: author repair / explicit limitation**, §4, §8, source audit. Classify EVERY unproven post-write PeerError as uncertain, including retryable=true; no error-code proof allowlist is admitted. Exact inspected mdk source invokes runtime send before generic error projection and caches successful outcomes only. Actual deployed binary equivalence is still unverified, so it cannot justify a stronger proof. One finite authenticated recovery manifest can explicitly mark-reissue several affected copies with duplicate warning, not blind retry. The residual need for real Marmot recovery after ambiguous transport failure is disclosed, not claimed automatic relay-outage recovery.

## 2. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 534,
  "summary": "Restoring a lost admind Store requires 'genuine Marmot authority', but the only authenticated Marmot decision route is admind itself. On loss, admind's dispatch is stopped and it may not open its creating Store constructor. So it can't post the decision ask, receive the answer or send replies, and isolated admind Store loss has no recovery path.",
  "failure_scenario": "The admind state DB is deleted while a valid sealed backup exists. On startup the catalog check detects the loss and holds admind, as §7.2 requires. §7.3 needs a separately authorized restore through a genuine Marmot admind decision, and §8 says 'while it is down retain hold'. admind can't create the ask or process the answer without a store, terminal consent is forbidden, and the system stays held forever despite having an intact backup."
}
```

**Disposition: author repair / explicit limitation**, §7.2–7.3, §9. Automatic startup same-identity recovery from a validated checkpoint plus complete data log is authorized by the proposed design/deployment contract, BEFORE Store creating constructor. Exclusive lock, retained damaged bytes, private temporary restoration, exact schema/UUID/frontier validation and fsync precede resuming authenticated ingress. No fresh broken-daemon permission card. Unknown or total anchor loss remains held; automatic restoration and its host-trust residual are explicit human design choices.

## 3. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 77,
  "summary": "commit_approval supersedes EVERY open/answered ask whose bead matches on any new approval post, not only an exact predecessor refresh. The design only defines protected supersession for refresh and forbids inferring lineage from bead ID. A legacy AskPost approval, or a new protected post, for the same bead closes a protected ask outside protected bookkeeping.",
  "failure_scenario": "A protected approval for bead X is open with logical ID L1. The controller (or any same-UID poster through legacy AskPost) posts another approval for X. Existing commit_approval sets L1's ask to 'superseded' in the same transaction, without the protected supersession, reminder/episode transfer or backup seal. The new ask gets either no protection (legacy) or a fresh logical ID with a new 4h reminder and a new 600s/660s episode. That reproduces R1#19/R2#10's multiple reminders and alerts for one bead decision."
}
```

**Disposition: author repair / explicit limitation**, §2.1. Fence BOTH legacy and protected commit_approval against a same-bead active protected ask unless this is the exact protected predecessor refresh. Refuse before superseding rows. Refresh transfers logical identity/reminder/episode state in its transaction. A genuinely terminal prior decision permits a fresh independent producer intent. No title/bead-based implicit lineage or legacy unprotected successor bypass.

## 4. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 190,
  "summary": "The protected current-copy and reply gate covers only ask: and askr: keys. _CARD_KEY and ask_for_message also treat askd:<id>:<mid>:<i> (!details output, and R29 pinned-only updated content posted to the stale card's thread) as answerable card messages. Those rows stay on legacy redact/retry/ID[0]/top-level-fallback policy, and replies to them are neither allowed nor refused by the design.",
  "failure_scenario": "An operator runs !details on a protected approval and replies 'approve' to the details message. ask_for_message resolves it to the ask through the askd: key. One implementation refuses it because it isn't the 'full current copy', breaking the existing answer path. Another accepts it, bypassing the protected current-copy/context gate. Either way, details parts for a protected decision are sent under legacy policy, with ID[0] receipts and fallback threading."
}
```

**Disposition: author repair / explicit limitation**, §3, §5. Cover all native askd producers (!details and R29 updated content), preserving actual key/ask_details linkage. Protected supplementary copies carry complete immutable parts/current context and use protected dispatch/receipt/thread rules. They are answerable only when their own current-context copy and current canonical card are complete; old/partial contexts get status/pointer, never native decision bypass.

## 5. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 181,
  "summary": "The frozen-copy bounds (1–20 parts, <=64KiB per copy) are smaller than canonical cards that existing admind admits. MAX_APPROVAL_CARD allows a 24,000-character readout (up to about 96KB UTF-8), chunk_chars may be as low as 200 (over 100 parts), and a 16,000-character question body plus title and framing reaches 64KiB. The 6A summary overflow is defined for mirrors, not for the canonical control card.",
  "failure_scenario": "A legitimate approval readout of 20,000 CJK characters (about 60–72KB) passes post_approval today. Under the protected post it exceeds 64KiB. The design gives HOLD/typed unavailable but no canonical overflow, so the approval can never be delivered as a protected card, or the post is refused. Either is a regression from legacy behaviour, and neither is disclosed."
}
```

**Disposition: author repair / explicit limitation**, §3, §9. Split canonical and display profiles. Canonical 1024 parts/512KiB admits actual 24000-character approval and16000-character body at minimum configured chunk size, exact complete text retained; no mirror summary for canonical approval. Mandatory offline worst-case Unicode/packing fixtures over supported chunk_chars block activation if any legacy-admitted canonical input fails. Native card bounds themselves are not silently narrowed.

## 6. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 565,
  "summary": "After the three 0/5/30 attempts, recovery is one authenticated decision per exact part (logical/source/copy/part IDs, phase, proof hash), with no bulk or episode-scoped allowance. Any wn-agent prewrite outage longer than about 30s exhausts every in-flight protected part, including the 600s control alerts and the digest.",
  "failure_scenario": "wn-agent is down for 2 minutes during a deploy while 6 approvals, their 600s alerts and a 5-part digest are pending: about 20 exhausted parts. The operator never received the cards and must find each exact part ID through local health. Each needs a separate authenticated Marmot recovery decision before anything is delivered. For a PoC, the 'brief restart leads to indefinite HOLD' failure (R1#2/R2#4) just moves from 6s to 30s."
}
```

**Disposition: author repair / explicit limitation**, §8. Replace one decision per part with one bounded route/owner episode manifest (up to1000 exact entries, one execution per entry). Proven-unissued entries get a same-key allowance; uncertain entries require explicitly disclosed one marked NEW copy each. Proposed authenticated Marmot prepare/recover command interaction is independent of AskPost MAX_OPEN, avoiding full-active-card deadlock. No terminal consent, unknown replay, automatic extra budget or promised recovery while sole transport is still down.

## 7. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 494,
  "summary": "Every send needs a sealed prior record ('Persist dispatch-mark and backup seal before first write'), and the backup is a full consistent copy of the whole admind Store (up to 256MiB), fsynced and renamed, then a catalog seal. Each part dispatch and each legacy ask acknowledgement is therefore serialized behind a full-database backup, with a 30s timeout that leaves the store recovery-required.",
  "failure_scenario": "admind's Store holds 150MiB of legacy outbox/inbound/turn history. A 20-part digest needs at least 20 dispatch-mark seals, each a full online backup plus fsync. That is minutes of serialized latency, and legacy AskPost acks stall under ask_post_lock. A slow disk pushes one backup past 30s, which sets recovery-required and stops all new protected effects for the owner. An incremental seal (for example, sequence plus WAL/frontier) would give the ordering without copying the whole database per effect."
}
```

**Disposition: author repair / explicit limitation**, §7.2. Replace full database per effect copy with explicit proposed bounded DATA-only recovery log and periodic full checkpoint. Complete writer coverage is an activation gate, not an invented existing SQLite extension: finite schema maps/PK/prestate hashes/after-images/sequence, append+fsync/frontier barrier then atomic SQLite apply. Max4MiB delta/64MiB log, checkpoint32MiB or50 records; separate read-only WAL backup pins BEGIN+SELECT snapshot for all steps, without writer/owner locks; pages128,30min deadline,busy5s,WALgrowth256MiB and minimum512MiB free-space checks bound cancellation. Covered records retire only after durable verified publication. Crash-cut fixtures must cover all Store writes, triggers, FK/sequence and lost acks. Per-part performance is bounded bytes, not claimed benchmark.

## 8. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 299,
  "summary": "ADR §3.3 (its recovery table) requires that after journal loss 'replies to old cards get a pointer to the new one'. The design only says unknown/old targets 'remain unknown' with no intake. No pointer reply is specified, and the omission isn't in the §9 disclosed choices.",
  "failure_scenario": "After restore, an operator replies 'approve' to the pre-loss card, whose message ID is only in uncertain or unknown state. The resolver returns not_found or uncertain and nothing answers the operator. They believe they approved while the decision stays open. The ADR-required pointer to the re-issued card never appears, which is an undisclosed ADR departure."
}
```

**Disposition: author repair / explicit limitation**, §5, §7.3, §9. Known old message-to-source mappings produce one deduplicated FYI pointer to the verified complete reissued root (or recovery-pending exact ask). Truly unknown mappings get a fixed unresolved/current-inventory response, not fictitious source-specific mapping or new intake. Qualify ADR3.3 unconditional pointer in this complete-evidence-loss case and explicitly expose that departure for genuine human acceptance.

## 9. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 76,
  "summary": "The protected-post idempotency key is underspecified. AskPost has no request ID, and the logical decision ID is 'owner-assigned at first protected post', so a producer retry can't carry it. 'Repeated identical post intent returns the same ask; conflicting bytes refuse' has to dedupe on source key (producer, kind, canonical_source_id), and that collides with a later, genuinely new decision for the same source.",
  "failure_scenario": "The post now waits for a backup seal before acknowledging, so the ask.sock client times out and retries. If dedup is per source key, the retry is safe, but a later new approval for the same bead, after a denial, is refused as 'conflicting bytes'. If dedup isn't per source key, the timeout retry creates a second ask and a second logical decision for the same intent."
}
```

**Disposition: author repair / explicit limitation**, §2.1, §3. Add proposed protected request producer_intent_id generated/persisted by caller before initial post; exact producer/kind/intent plus input hash dedups lost acknowledgements. Owner returns the same logical/ask IDs; changed bytes conflict. Later independent decision after terminal closure uses a new intent. Tombstones retained with source/ambiguous-ack history; capacity refuses before creation. No invented legacy AskPost request-ID field.

## 10. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 453,
  "summary": "The fixed-name `shared_overflow.json` is relayed at most once in the deployment's lifetime. admind's alerts table records relayed names with INSERT OR IGNORE and is never pruned. The design calls this the 'FIRST state only', but in practice every later overflow episode, weeks later, is never relayed remotely. Separately, the claim 'This matches Plan8's shared cap/observer policy' is false against the pinned Plan8 c9fa input: capacity there 'prevents further scheduled admission', and the later 09411b3 drains per-code summaries after space returns.",
  "failure_scenario": "Capacity overflows once in week 1 and admind relays the file. In week 6 another backlog fills the 255 slots. The writer updates shared_overflow.json, admind's scan sees an already-relayed name, and operators get no remote signal at all. Implementers following the pinned c9fa gate admission on capacity, while 6C forbids that."
}
```

**Disposition: author repair / explicit limitation**, §7.1, §9. State fixed shared_overflow.json relays at most its first state ONCE PER DATABASE LIFETIME, not per episode. Mutable future updates are local-only status; no remote overflow freshness guarantee. Remove false current compatibility claim: Plan8 c9fa/094 differ. Exact jointly compatible reviewed/human-approved helper and fixtures are required for shared activation. Local-only qualification remains explicitly proposed, not an unbounded new-file workaround.

## 11. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 66,
  "summary": "The R29 refresh is admind-internal: refresh() runs in the decision worker with peer_pid=None and row.poster, inside the attempt-closing transaction. It isn't a controller post. Yet the protected-post input requires producer binding and exact source/scope binding from 'the original controller'. The design doesn't say that refresh inherits the predecessor's association, or how the seal-before-ack barrier fits refresh's single no-await transaction, which also queues the reply and the askd rows.",
  "failure_scenario": "An operator approves a protected card whose digest has changed. refresh() builds the fresh ask with no producer request, so an implementer either refuses the protected refresh (no producer binding, and the card silently stays legacy or unrefreshed) or invents a binding. Meanwhile the transaction's reply and askd rows become sendable before any backup seal."
}
```

**Disposition: author repair / explicit limitation**, §2.1, §7.2. Internal R29 refresh inherits predecessor association/binding/logical budgets. Deterministic internal intent derives from owner UUID, old ask, native attempt message and fresh context digest. Existing no-await closing transaction also records protected askd/reply rows with one recovery sequence; send eligibility waits outside the transaction for its durable/applied barrier. No invented controller peer PID or native-action replay.

## 12. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 410,
  "summary": "Every new process starts with the clock unready for at least 30s (two observations 30s apart), but the design doesn't say how protected posts accepted in that window record their UTC intent, opened-time and 600s anchors, or whether they are refused or held.",
  "failure_scenario": "admind restarts and a controller posts an approval 5s later. The intent anchor needs a UTC timestamp that the clock_ready contract forbids using. One implementation refuses the post, another anchors with untrusted UTC, and a third leaves it unanchored, so the 600s/660s escalation and 4h reminder clocks diverge."
}
```

**Disposition: author repair / explicit limitation**, §6, §2.1. New protected posts during clock-unready return typed retryable clock-unready BEFORE any ask/source/UTC anchor. Caller retains same durable producer intent for retry; acknowledged old anchors and valid same-boot monotonic deadlines persist. Explicit finite startup availability qualification, no invented untrusted UTC and no per-retry human approval.

## 13. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 364,
  "summary": "'At most ONE reminder' covers autobump and protected !asks repeat, but not the operator `!asks bump` command. That command creates asknote:<id>:bump:<mid> reminder rows threaded to bump_target under legacy policy.",
  "failure_scenario": "An operator runs !asks bump on a protected approval. A legacy reminder is enqueued with legacy retry and ID[0] behaviour and the bump_target from _first_full_repeat, alongside the protected 4h reminder. The single-reminder mutation gate doesn't cover this path."
}
```

**Disposition: author repair / explicit limitation**, §2.1, §6, §9. Route protected !asks bump to the SAME one-reminder reservation. Before4h status only; after4h one eligible reserve or existing status. No legacy asknote bump, earliest-repeat root or extra reminder. Explicit requested marked full copy is distinct display policy preserving logical budget.

## 14. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 428,
  "summary": "§7.1 names only 'WSD/Plan8 writers and admind's existing alerts reader', but §6 makes admind the owner of the 660s local alert for canonical cards. So admind must also be a shared-writer client of the `<state>/alerts` directory, which is outside `state/admind`. Its namespace, owner-store UUID and lock participation aren't specified.",
  "failure_scenario": "An implementer gives admind no writer path, or writes outside the shared lock and cap accounting. Then the 660s fallback for canonical cards is missing, or the 256-slot accounting and coalescing is wrong when WSD and admind write at the same time."
}
```

**Disposition: author repair / explicit limitation**, §7.1. admind is explicitly a d6c shared writer for canonical660s alerts, using initialized admind Store UUID/logical episode and configured shared-directory capability under same directory lock/cap/settlement rules. WSD/Plan8 remain separate owner UUIDs. No inferred sibling path or source import boundary.

## 15. Actual finding

```json
{
  "file": "docs/designs/2026-10-16-plan6c-durable-delivery.md",
  "line": 260,
  "summary": "The design says protected rows need new durable due selection and suggests there are no existing durable timers. But admind's Store already has durable per-delivery-group `delayed` status with `not_before`, hold_group and release_delayed (used for notice cadence and parent-held). Extending that mechanism would avoid a parallel scheduler.",
  "failure_scenario": "Two durable delay mechanisms coexist in one outbox, with different release semantics: release_delayed runs inside outbox_pass, while the new next_due index is separate. That duplicates logic and risks a protected row being released or held by the wrong one."
}
```

**Disposition: author repair / explicit limitation**, §4, §9. Reuse actual Store delayed/not_before, hold_group/release_delayed and outbox_pass. New phase/index metadata projects the same group state, not a second timer engine. Protected release checks eligible phase/due/parent/source/trust; quarantined/uncertain/exhausted never release by timestamp alone. Preserve legacy cadence and parent-held deadlines, skip held head fairly; actual retry timer ownership is explicit.
