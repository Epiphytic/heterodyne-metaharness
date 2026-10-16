# Plan9 d3: actual R2 author dispositions

Actual R2: REVISE, 22 findings, two of three rounds used. These are author repairs, not reviewer clearance. All summaries/scenarios below are verbatim parsed actual report entries; original d1/d2 and prior review evidence remain unchanged.

## R2.1

Gate.authority is `Authority|null` with no per-GateName rule, so the governing_authority gate (genuine a27/btq-ovngv) and a user_schedules ADR deviation are not required to carry a NativeAuthority; 'A null authority never excuses a genuine existing gate' names no gate it applies to.

Failure scenario: An Entry whose governing_authority gate has authority=null, or whose user_schedules gate cites a MarmotAuthority deployment-scope decision as the 'approved deviation' (that variant explicitly infers no native design approval), passes the closed schema. The original user-schedules obligation is then waived without a genuine native ADR amendment, and the governing a27 authority is never machine-checked against ovngv.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:56`; exact finding SHA256 `d528dee37c94eff154aaad6efea543d28c5bdc1554e81b3a63e21a13a30c41d7`.

Author disposition: Every GateName has an explicit mode/authority rule. governing_authority requires exact ovngv/a27 NativeAuthority; user-schedule deviation requires its own genuine native ADR amendment. Technical-only null gates cannot waive either.

Design: §3 gate matrix.

## R2.2

UTC is fixed to `YYYY-MM-DDTHH:MM:SS.ffffffZ`, but NativeAuthority.approved_at must be 'verified against the actual canonical native readback', and btq (pinned bin/btq, `strftime('%Y-%m-%dT%H:%M:%SZ')`) and the pinned ovngv metadata (`2026-10-14T15:57:38Z`) have no fractional seconds.

Failure scenario: The evaluator projects ovngv into NativeAuthority. Either the strict UTC parser rejects `2026-10-14T15:57:38Z` and Entry can never validate (a permanent design_authority/governing_authority hold), or the collector rewrites it to `.000000Z` and the field no longer byte-matches the native readback it must be verified against. Each implementation picks differently.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:33`; exact finding SHA256 `f014383b300f7ed6ffaccc83c86051c4b97f205f75272bbd4e28b40f3db06b50`.

Author disposition: Preserve approved_at NativeTime bytes; separate approved_at_utc derives the normalized instant. The exact native seconds timestamp remains valid and unchanged.

Design: §3 timestamp types.

## R2.3

'Re-capture and compare the full declared loaded inventory after every automatic restart' has no defined comparison: Release.loaded_inventory is an Artifact whose digest hashes exact stored bytes, and the inventory necessarily contains capture-time and process-specific data, so no canonical identity projection exists to compare.

Failure scenario: A same-release automatic wsd restart, which the design explicitly allows, produces a new inventory artifact with new PIDs or start times and a different sha256. A strict evaluator records release_change on every allowed restart, so the 72h segment is unachievable whenever any restart happens. A lenient evaluator compares ad hoc and can miss a real reverted binary swap (R1 #6 reappears).

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:124`; exact finding SHA256 `30111dde23cc9e7dafdf52997b72ad8cb11c5a7906bda3782de13dda6d54645e`.

Author disposition: Stable closed Identity excludes PID/capture metadata; exact observational artifacts retain them. Loaded-object mappings and loss-detecting continuity distinguish permitted restart from content changes.

Design: §§3,7 Identity and observations.

## R2.4

Drill.release_identity 'hashes exact canonical Release JSON' and must equal Entry.release, but drills run before Entry is produced (§4 step 6), runtime_control restarts services, and isolated-scope drills run on a different deployment. Drill also has no typed field for the 'isolated equivalence/declared limitations' that would make reuse legitimate.

Failure scenario: Every release-scope drill hashes a Release whose loaded_inventory artifact predates Entry's recapture, and every isolated drill hashes a different deployment's Release, so release_identity never equals Entry.release and all six families hold. If equivalence is accepted from prose or generic evidence instead, a stale-release receipt is reused (R1 #2). After any release_change, the exact-identity rule forces all six drills to be re-run, contradicting §7's 'actual affected checks'.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:62`; exact finding SHA256 `0bf26d2f87e73d5e88e569bb2136021fdde760fbd6f896e38ad0d6bd1416ed50`.

Author disposition: Drills bind immutable family component projections and explicit isolated scope equivalence, not whole timestamped Release JSON. Reuse requires unchanged complete projection; relevant changes require affected drills.

Design: §3 Drill/Equivalence; §6.

## R2.5

Step (1) requires 'segment_stop ... with reason=none', but the closed Event schema has no reason field (Detail is {code,subject_refs,expected,observed}), and EventKind has no interruption kind, so interrupted and sealed stops and their Reason are not representable on the event that Segment.end_event_sha256 points to.

Failure scenario: An implementation encodes the reason in Detail.code with an ad hoc string, or emits a hold or invariant event as end_event. A sealed Segment (reason=none) and an interrupted Segment (reason=human_repair_required) can point at end events that carry no machine-checkable reason. The evaluator cannot cross-check Segment.reason against the ledger, and the noncircular stop/seal chain rests on unchecked prose.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:134`; exact finding SHA256 `c059890b36857f1a4a1d2a248f4ab081cf6f5bb5f7d933d10cd7013c140f4e23`.

Author disposition: Event.stop has a typed reason/checkpoint/transaction binding, required exactly for segment_stop. Segment end_event/reason must match the unique terminal Stop.

Design: §§3,8 Stop.

## R2.6

The rule requiring a contiguous duration_ns >= 259200000000000 never says whether duration_ns is the monotonic delta or the UTC delta, even though up to 1s of disagreement between the two is explicitly admissible.

Failure scenario: The monotonic delta is 259200.2s and the UTC delta is 259199.4s (|diff| <= 1s, admissible). One evaluator qualifies and another reports not_qualified, so the specified 259199999999999/259200000000000 boundary fixtures have no defined clock.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:116`; exact finding SHA256 `6d403c1da38997a34fb41cb4b80771bc59fbce7b8213b23bd0c68c2b3ce935b3`.

Author disposition: duration_ns is exclusively the same-boot BOOTTIME monotonic endpoint delta; UTC is chronology/cross-check. Exact boundary and discrepant-clock fixture semantics are explicit.

Design: §7 exact duration.

## R2.7

Archive and the final Assessment have no typed field for the final whole-migration disposition ledger (deferred rows resolved) or for the second pre-archive delta audit. Migration.dispositions and delta_audit exist only in the frozen Entry, and generic evidence 'never replaces a mandatory field'.

Failure scenario: Deferred rows are never routed or culled and the post-cutoff delta is never re-audited. Archive result=complete is still schema-valid, because the condition 'no unresolved migration hold' has no artifact to check, so all three repositories are archived with old-open beads silently abandoned (R1 #11 only half-closed).

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:142`; exact finding SHA256 `a6035709ea4e76820d6c523b497419e0e172eb6118aae3a9372d39b6722d225e`.

Author disposition: Archive requires typed final inventory, complete final dispositions and second delta through prearchive capture. Deferred or unexplained rows hold.

Design: §§3,8 FinalMigration.

## R2.8

Delayed (300-600s) intervals and some audit classes depend on 'explicit authenticated operator declarations tied to interval checkpoints'. These are operator actions inside the segment that are neither approvals nor passive observation, so line 126 says they interrupt, and they have no EventKind or typed Observation field.

Failure scenario: The audit profile requires a declaration for a 450s delayed interval. If Liam provides it, that is 'other operational operator involvement' under line 126 and interrupts the clock. If he doesn't, the interval holds. Either way, recurring declarations over 72h contradict the approvals-only segment, and the declaration lands only in generic refs, which cannot fill a mandatory field.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:120`; exact finding SHA256 `c8b0e00e4497d1b303d8f12f7b1b45f6882fe8d153fe3de732d15847b0daccb4`.

Author disposition: Remove recurring operator declarations. Preclock capability/limited trust disclosure is followed by reliable autonomous admitted audit streams; absent proof holds entry rather than demanding in-segment operational testimony.

Design: §7 autonomous audit.

## R2.9

The evaluator checks a prefix 'up to a declared final checkpoint', but segment_stop is appended afterwards and becomes Segment.end_event. It is not defined whether end and duration_ns are measured to the checkpoint or to the stop event, or whether the window between them is evaluated.

Failure scenario: The checkpoint is at 72h+10s, and the evaluator and lock wait take 40s, during which a manual service restart occurs. duration_ns is taken from the stop event, so that 40s window counts toward the sealed interval but was never evaluated, and the qualifying Segment includes an unchecked intervention.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:132`; exact finding SHA256 `aa493918cc237d6ce8d92152868d76bbfe371bcb54d88471dd2b1ebc8289e6ff`.

Author disposition: Elapsed interval ends at the fully audited checkpoint, not later stop append. The evaluation/publication window contributes zero duration; later intervention is a current hold.

Design: §8 checkpoint endpoint.

## R2.10

'Collection gaps' are not defined as gaps between full intervention-audit and inventory checkpoints rather than between any two Events, even though event-driven capture interleaves frequent automatic events.

Failure scenario: Automatic corpus_transition or heartbeat events arrive every 2 minutes while the full host and audit collection stalls for 20 minutes. An evaluator measuring event-to-event gaps sees no gap over 300s and keeps the segment continuous across an interval with no intervention coverage.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:122`; exact finding SHA256 `95d6dceeedf3a7425f52622e88660669360559b721883d0324667f5f57fb928f`.

Author disposition: Only full Checkpoints with complete intervention and identity source continuity reset the 600s gap; frequent corpus/heartbeat events do not mask missing audit coverage.

Design: §7 full checkpoints.

## R2.11

Migration.source_queues is a free self-declared list. Nothing binds it to the complete queue set of the three repositories ArchiveRow requires (hermes-workstream-harness, harness-improvements, harness-dev), so 'complete inventory' is complete only relative to whatever was declared.

Failure scenario: The operator declares only the hermes-workstream-harness queue. The inventory is 'complete' for that list, Entry validates, and harness-dev's open beads are never inventoried, yet all three repositories are archived.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:60`; exact finding SHA256 `7e0f6ec568222af814a415f2c16c0cf1900d38bc42ec8eac123d2499b8c85f17`.

Author disposition: Exactly all three retired repositories map to complete canonical associated queue sets, including shared-queue dedup and supported no-queue proof. Missing/unknown associations hold.

Design: §3 QueueBinding; §4.

## R2.12

The 'complete old-open-bead set' does not define 'open' as every non-closed status (open, in_progress, blocked, deferred, pinned/hooked).

Failure scenario: The collector filters on status=open as Beads does by default. In-progress and blocked old beads, including stopped old holders that §4 says need explicit recovery, are omitted from the inventory and the dispositions, so they are neither migrated nor culled before archive.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:71`; exact finding SHA256 `3ea8cd720c03fe73a3996dbcf0890da5c5965bf6b19f311db9b8d98b567d5040`.

Author disposition: Inventory every status except closed, explicitly including blocked/in_progress/deferred/pinned/hooked, with complete paging and unknown nonclosed statuses retained.

Design: §§3–4 complete inventory.

## R2.13

The preclock operator_surfaces row only requires steering 'for admitted Codex/Claude roles where demonstrated'. That is an escape hatch for a required v1 capability: ADR §12 requires Codex steering via `codex queue` and Claude reviewer steering via send-keys, and codex_managed relies on this row for steering proof.

Failure scenario: Claude send-keys steering is never demonstrated. operator_surfaces still passes 'where demonstrated', claude_managed passes on natural read-only work, and the release qualifies without ever proving the v1 Claude steering path works.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:84`; exact finding SHA256 `be592740334970f744ee944d26241f57d226879e86b015f5600d877928a78e46`.

Author disposition: Both Codex queue steering and Claude reviewer send-keys steering require real preclock functional evidence. Remove where-demonstrated escape hatch.

Design: §§4–5,11 preclock steering.

## R2.14

Corpus rows added after Entry ('append-only hash-verified Corpus/coverage artifacts') have no typed field in Segment or Assessment and resolve only through Assessment.evaluation, so Coverage.beads membership in the corpus cannot be checked by the closed schema.

Failure scenario: A coverage row cites a bead that was never migrated, or a deferred one, as a 'supplemental corpus' row placed in generic evidence. The closed schema cannot reject it, contradicting 'Corpus contains only genuinely migrated surviving beads'.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:75`; exact finding SHA256 `01fc3e9149a2ca52f0c8f7999573291a2644999e262c33868e083184be59eaf6`.

Author disposition: Assessment enumerates typed hash/event-bound corpus additions; Coverage members resolve to exact migrated queue/bead identities and cannot be introduced by generic evaluation refs.

Design: §3 CorpusAddition; §§4–5.

## R2.15

The final release_complete Assessment reuses the sealed Segment's final_inventory but never requires that the release running at archive time is the qualified Release, even though authorized post-qualification config and workstream changes are allowed before archive.

Failure scenario: After qualification, an authorized upgrade changes the interpreter or a managed CLI before archive. release_complete is issued for the old qualified inventory while a different, unqualified release is running, and nothing records whether that change was a current-release hold.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:144`; exact finding SHA256 `8b602315365982ef1461850d1d411ec59fa3c2e4a65a7fe2f8bc56c3f63ae388`.

Author disposition: Final release_complete captures actual current Release and requires Identity equality with the qualified Entry, plus continuity. An authorized changed runtime is still unqualified and holds.

Design: §8 current_release.

## R2.16

The four-step stop, seal, Assessment and qualification sequence defines no recovery for a crash partway through.

Failure scenario: The evaluator crashes after appending segment_stop but before writing the sealed Segment. On restart, one implementation re-runs steps 2-4 against a stale checkpoint, another treats the stopped-but-unsealed segment as interrupted, and a third appends a second segment_stop. The append-only ledger becomes ambiguous.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:134`; exact finding SHA256 `ec489ccb2db153bc02083b2a0833b1535aad866dadf1f69527e8ee4809fb0424`.

Author disposition: Durable exact terminal intent/manifest defines one atomic logical commit and idempotent recovery; partial files are not qualification, conflicting tails hold, and duplicate stops are forbidden.

Design: §8 SealIntent transaction.

## R2.17

The evaluator checks 'all six applicable drill families', and the communications drill applies only 'as applicable'. Both weaken the 'require all six at least once' rule in line 62.

Failure scenario: An implementation marks communications or accounts as not applicable for the selected adapter and qualifies with five drill receipts, citing the 'applicable' wording.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:132`; exact finding SHA256 `469043d4a0e44da7231306157f377a8f61fe9167487a656b14836975c66b2536`.

Author disposition: Remove applicability waivers: all six families and all specified subcases are mandatory; communications fake-forge subcase is distinct from real admitted transport outages.

Design: §§3,6,8,11 six families.

## R2.18

Terminal approval is listed as interrupting operator involvement, although ADR §17 item 7 keeps 'terminal approvals for content that still can't be shown after grooming' as a host-exception approval path. This stricter reclassification of a genuine approval is not disclosed as a §11 choice.

Failure scenario: A corpus bead's approval card can't be groomed for Marmot and needs the ADR's terminal approval. The genuine approval interrupts the segment even though ADR §11 allows approvals, and the approver was never asked to accept that interpretation.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:126`; exact finding SHA256 `4932b0713a9696849596e23e57d62a096497794700d875c582730945d2540e57`.

Author disposition: Retain Marmot-only per latest explicit operator instruction. Disclose stricter selection relative to historical ADR17 terminal exception; no terminal/conversation authority substitution. This is an explicit scope qualification, not denial of the source exception.

Design: §§7,9,11 channel scope.

## R2.19

Disabling the ADR §9 forge poll (60s) is an ADR interpretation, but it is missing from the §11 human-choice table and from the manifest's decisions_requiring_human_design_approval. The communications drill also says forge 'polling behavior is tested without enabling them', which contradicts the gate that requires polling disabled.

Failure scenario: The human approves §11 without seeing that a listed v1 scheduler job is disabled. Implementers then either enable polling to test it in the communications drill, which violates forge_phase2_boundary, or skip it, and the drill PASS cannot be checked.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:112`; exact finding SHA256 `39ccdd5c73535244e2d6b9500a7c0f890f8c7910872e91e6cbb93eec85a5048b`.

Author disposition: Disclose disabled production forge polling as an explicit proposed phase1/phase2 interpretation. Only isolated fake polling/outage tests are required; no actual poll is enabled by a drill.

Design: §§6,11 forge boundary.

## R2.20

A full recapture of the loaded, runtime, source and config inventory at every 300s checkpoint means hashing interpreters, Dolt, the sandbox runtime and the full managed-install trees every 5 minutes.

Failure scenario: Walking and hashing both managed install trees and the runtime dependencies takes longer than the 300s nominal period under load. Collection drifts into the delayed 300-600s band or over 600s, and the segment interrupts because of the observer's own cost. A cheaper design is event-triggered full recapture plus a lightweight per-checkpoint identity check (file-change notifications or recorded executable identities).

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:124`; exact finding SHA256 `995f4a8960b58a0b9ce049413cda1627047b20af27147f20fa9d217ed309a9fc`.

Author disposition: Full capture at entry/event invalidation/final; checkpoints verify a proven immutable/loss-detecting cache and full audit intervals. Explicit invalidations and preclock load/capability tests prevent silent false continuity or cost-driven relaxation.

Design: §7 bounded collector.

## R2.21

NativeAuthority verification is re-specified from scratch (digest equality, approver policy, scope) instead of delegating to btq's existing approval_valid and context-digest recomputation, which already enforce approved_digest, adr_revision matching and content-digest recompute.

Failure scenario: The acceptance evaluator's separate rules diverge from btq's, for example by accepting a projection whose stored context_digest no longer matches the bead content. An approval that btq itself would reject passes the acceptance authority check.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:37`; exact finding SHA256 `5f82ccfbef6ee474faf937e5f7a1b98f18175ab30409d30f854562e499e7131d`.

Author disposition: Delegate native approval validity/context recomputation to pinned Queue.approval_valid and ask_digest with complete bounded source readbacks. Preserve policy/native/Marmot evidence instead of inventing a parallel protocol.

Design: §3 NativeAuthority.

## R2.22

The normative grammar includes changelog prose ('Remove derived duplicate release_sha256/drill_receipts') about d1 fields that do not exist in d2.

Failure scenario: An implementer reading the closed grammar takes this as an instruction to strip fields from receipts, or looks for fields that d2 never defines. The spec mixes the R1 disposition history with the normative contract.

Source: `docs/designs/2026-10-16-plan9-release-corpus-and-acceptance-d2.md:64`; exact finding SHA256 `91f8397b1205c9968c60ff8fd58df6e8a949d758fdd927404a737af245fa0031`.

Author disposition: Remove d1 field-removal changelog instruction from normative d3 grammar. Historical findings and changes remain here, not receipt semantics.

Design: §3 grammar; this historical disposition.
