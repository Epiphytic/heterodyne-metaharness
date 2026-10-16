# so7ma actual R1 ordered dispositions

Actual report SHA256 `f167ea7ef46b0f0ff073901d79e6c710d2599bd2a24482c54596debd03d8294d`. Actual verdict REVISE; 42 findings, one of three issued rounds consumed. Quoted summaries normalize host-local checkout prefixes to SOURCE; exact raw report remains privately retained. These are author dispositions, not a review verdict. No implementation/tests performed.

## 1. R1 line 117

> Activation (and revoke at L146) relies on 'the actual stored authenticated ask decision' and 'the real selected activation choice'. admind question asks accumulate up to 50 answers from any confirmed operator and map a thumbs-up to the recommended choice. The design never says which answer is final or that a later or other-operator decline defeats activation, whereas legacy d2 check_ask required every policy approver's answer to be A.

Corrected with source-backed rebuttal of the all-approvers premise. Native Handoff.check_ask accepts at least one policy approver A and rejects any recorded non-A. Section4 preserves that rule, selects the complete serialized eligible-answer set, and makes later contrary answers revoke/tombstone. No new unanimity gate.

## 2. R1 line 146

> Revocation takes effect only when some unnamed party later calls delegation.revoke with an already-stored answer. The operator command only posts the ask (L148-149) and admind's answer handling only stores answers. No component is specified to redeem a revoke answer, so the disclosed five-second bound is measured from a journal commit that may never happen.

Accepted. Section5 daemon intake transaction commits authenticated withdrawal and revocation before acknowledgement; no later client redemption.

## 3. R1 line 119

> Activation verifies the attachment/artifact hash but not that the card body and choices the human saw were rendered from the bound descriptor bytes, and no trusted renderer is specified. A human can therefore consent to a card that understates the descriptor actually activated.

Accepted. Sections2/4 require verified canonical bytes and daemon-only renderer; body/choices/attachment hashes are activation inputs.

## 4. R1 line 117

> Activation waits for deployed hash proof after the human decision (L52), but there is no way to revoke or withdraw before activation. The journal and revoke contract begin at activation (L134-136, L146, L151), the activation checklist has no not-revoked check, and the card disclosure and acceptance rows (L101, L239) cover only revocation after activation.

Accepted. Registration precedes activation and allows permanent preactivation tombstones; activation checks them.

## 5. R1 line 146

> delegation.revoke requires 'fresh operator authority' for the revoke answerer when the revoke is redeemed, but grant holds watch only the activator, the pinned policy fingerprint and unresolved membership (L152-153, L156-158). A non-activator's revoke is therefore permanently refused if they leave the confirmed operator set before redemption, even though admind already acknowledged it as recorded.

Accepted. Authority is checked at intake and transactionally recorded; later sender removal cannot undo withdrawal.

## 6. R1 line 143

> reserve/permit carry no caller identity, and admind's control socket trusts any same-uid process, with the peer PID used only for audit (src/heterodyne/admind/ctl.py:6-7, 60-62). The 'exact subject/deployed executor' check (L53-55, L143) cannot be enforced, and same-uid impersonation or slot burning is neither prevented nor disclosed.

Accepted limitation, not claimed isolation. Section1 discloses existing trusted same-UID assumption and impersonation/slot-consumption exposure. Subject consistency assertions are not OS credentials. Separate-user executor is out of scope.

## 7. R1 line 65

> 'Exact current approver policy fingerprint' and 'fresh policy' do not say whether they mean admind's policy.toml operators/approvers or btq's policy.json approvers (the d2 authority). The two are synchronized by hand (docs/admind.md:288, 330), so both the activation authority set and the changed-policy hold trigger are ambiguous.

Accepted. Section2 binds both policy fingerprints and unique queue-name/current-admind-pubkey mapping; section6 checks them freshly.

## 8. R1 line 148

> The only way to start a revocation is a 'future operator command' on the host that posts the revoke ask. There is no route from Marmot: no '!' command, and the card gives no revoke instructions. This contradicts ADR 0001 section 8.1, under which operators act from Marmot (docs/adr/0001-workstreams-v2.md:770, 774-784).

Accepted. Section5 defines bounded proposed authenticated !grant revoke command and card/help instructions, processed directly.

## 9. R1 line 98

> Delegated reclaim keeps only liveness/cessation evidence. It drops the per-bead worktree/external-effects reconciliation and the per-change reason that every existing reclaim path requires (beads-task-queue@6fe57598 bin/btq-manage.py:83-84, 174-177, 195-196, 404-408; docs/MANAGE.md:32; docs/OPS.md:111-113, 160-163; d2 L34, L52). It also allows bulk reclaim, contrary to OPS.md:169 'One bead at a time, never in bulk', without acknowledging the departure.

Accepted. Section3 retains reason, worktree/Git/PR/external effects evidence. Finite sequential one-at-a-time reclaim is an explicitly disclosed amendment, not a bulk native operation.

## 10. R1 line 43

> The descriptor must be committed and linked from GitHub (L43-45, L97-98). It carries host/run/session identities, the holder's host/boot/PID identities, cessation evidence and raw 'old state and endpoint facts'/'endpoint snapshots' (L53-55, L60-61, L74, L77-78). There is no digest-only minimisation and no secret or path screening before publication; the only secret check is on the card (L104), after the commit is bound.

Accepted. Section2 separates sanitized public intent from mode0600 private facts; publication preflight screens before signing/pushing.

## 11. R1 line 177

> Revocation is recorded only in admind's database, and the only restore defence is the executor's revision high-water mark, which detects only revisions the executor itself observed. Restoring admind.db to a point before a revoke therefore silently reinstates the grant, contradicting L162 and L178.

Accepted. Section6 invalidates every prior-epoch grant on every daemon startup and adds deployment-owned external guard; live DB replacement unsupported. Trusted-host full-state hostile rollback is explicitly outside claim.

## 12. R1 line 230

> The required acceptance assertions have no cases for several mandated refusals: bound violations (more than 32 operations or tasks, an interval over 24h, a max_operations mismatch); not_before/expires_at enforcement and the permit expiry clip; unknown versions, NaN and out-of-range integers; the parent-child ban; conflicting activation answers; grant_id reuse; and revoke negatives (wrong hash, unauthorised answerer, arbitrary old decision).

Accepted. Section8 enumerates bounds, malformed values, forbidden deltas, duplicates, ID/revocation cases and zero-effects assertions.

## 13. R1 line 194

> The step order 'obtain a permit, durably record issued, re-read facts/authority and check deadline, then invoke' makes issued durable before the re-read and deadline check, the reverse of legacy (issued recorded after the re-read, bin/btq-manage.py:305-325). There is no 'permitted but not invoked' outcome, so a failed pre-check strands the step permanently.

Accepted. Section7 completes locks/facts/cap preflight before reserve and native-intent; deadline expiry after fsync holds without native write.

## 14. R1 line 144

> Each native step gets one permit of at most five seconds that is never renewed or reissued (L144, L199-200). Any harmless delay over five seconds between permit and native call permanently strands an in-scope operation, and this availability cost is disclosed neither on the card nor in the renewal rules (L216-221).

Accepted with bounded proof. Section5 renew permits only proven no-intent step, same epoch, at most3 attempts; no grant extension/refund or unknown-result retry.

## 15. R1 line 199

> HOLD has no exit, abort or supersession. A held operation leaves its request unfinished; existing Management refuses new requests while one is unfinished and never aborts started management (bin/btq-manage.py:209-210, 413-414). Recovery is left to an unspecified manual edit that leaves no journal entry.

Accepted. Section7 provides read-only inspect/settle and retained-history supersession; genuine unresolved effects need scoped existing reconciliation authority, no blind release.

## 16. R1 line 61

> Operations bind other tasks' live state through endpoint facts, so running one operation changes another's bound before-state. The rule 'each task appears in at most one operation' ignores endpoints, so it does not achieve the claimed avoidance of inter-operation snapshot progression.

Accepted. Section3 predicts the ordered whole-graph state and endpoint reverse-edge/count changes across preceding verified operations.

## 17. R1 line 74

> Delegated operations must satisfy the existing management ownership/quiescence predicates (L81-82, L184), which in legacy refuse an open task unless it is parked. No park/unpark step exists in the delegated scope, and parking would itself change the bound before-state.

Accepted. Section3 binds dependency target already blocked/assigned to its exact own lane. No hidden preparatory park/release mutation.

## 18. R1 line 142

> The proposed RPC table has no activation operation. There is no activate method or daemon hook, no step that fetches, verifies and stores the published descriptor, no one-activation-per-grant_id rule, and no trusted source for 'deployed hash proof' or 'exact subject/deployed executor' (L52, L143).

Accepted. Section5 complete proposed activate RPC recomputes trusted artifacts, receipt, policy, consent and deployed closure; no caller asserted proof.

## 19. R1 line 50

> The descriptor must embed 'genuine native design approval' and 'actual deployment authority' and be committed 'with the design/deployment materials', yet it is attached to the very decision that creates them (L14-15). A descriptor in the same commit would need to contain its own commit hash. The non-circular order (design commit, native approval, deployment, later descriptor commit, separate typed ask) is never stated, and approval asks are approve/deny only (src/heterodyne/admind/verbs.py:14; asks.py:178-183).

Accepted. Sections2/4 allow combined deployment+finite-grant card with reviewed candidate pins and later deployment receipt. Future decision/selfcommit excluded from descriptor. Separate later card optional, not a third mandatory approval stage.

## 20. R1 line 189

> The complete native snapshot, explicitly including comments/content, is bound when the descriptor is authored, before human activation, and there is no refresh path. Harmless unrelated changes during the approval wait or between operations therefore HOLD every affected operation, an availability cost the design does not disclose.

Accepted availability tradeoff. Section3 retains unrelated snapshot/comment drift as HOLD; only deterministic recorded native deltas normalize explicitly.

## 21. R1 line 143

> There is no minimum remaining grant lifetime at reserve and no permit floor, so a slot can be consumed seconds before expires_at and a two-step replacement left half-done.

Accepted. Section5 reserve requires >=60s remaining; per-step deadline still enforced. Section7 discloses partial graph if expiry between replace steps; no false atomicity.

## 22. R1 line 165

> The executor deadline uses monotonic elapsed time, and Linux CLOCK_MONOTONIC does not advance during suspend. With no UTC or CLOCK_BOOTTIME cross-check, a suspend between permit and call lets the native write start long after the five-second deadline.

Accepted. Section6 uses CLOCK_BOOTTIME and UTC, RPC elapsed subtraction and dispatch-time checks, including suspend.

## 23. R1 line 92

> The 'bounded protocol audit notices specified below' are never specified (channel, trigger, bound, retry, behaviour on refusal), leaving an undefined exception to the ban on sending messages.

Accepted. Section5 removes general notice exception; only bounded card/command delivery and existing local/native audit remain.

## 24. R1 line 26

> The design calls the controller-integration evidence contract 'approved' but cites only the short SHA 3158263, which does not resolve in this repository and is not pinned in the manifest. It resolves only in SOURCE, as 3158263d049ea0a6b5cd398b40df95b704836d4d, where the document describes itself as proposed.

Accepted provenance correction. Inputs pin full original315 repository/design plus actual approved ADR0547 and native xu3ax authenticated approval provenance supplied by controller, limited to ytmpy.

## 25. R1 line 41

> Canonicalization is underspecified, with no golden test vector: string escaping/ensure_ascii, surrogates, float/null handling, array ordering and envelope shape are all left open. Python stdlib json (ASCII escapes by default) and msgspec (raw UTF-8) produce different bytes for non-ASCII text.

Accepted. Section2 specifies exact UTF8/scalar/NFC, separators, ordering, integer/time grammar and golden vectors; no floats/null ambiguity.

## 26. R1 line 63

> The payload repeats derivable fields (max_operations equals the list length, the derived class, the per-task lane) and keeps tasks and operations in separate lists. This adds consistency checks and allows a task with no operation.

Accepted. Section2 derives task/class/count/lanes from ordered operations; duplicate task and op rejected.

## 27. R1 line 43

> The 'outer hash/signature envelope', the signer identity/key and the verifier are undefined, and there is no bad-signature acceptance case. 'activation hashes' (L192), 'deployed hash proof' (L52) and 'grant hash' (L98) are never defined.

Accepted. Section2 hashes whole canonical bytes without self-envelope and verifies signed Git against installed allowed-signers; no self-enrolled key.

## 28. R1 line 98

> The card must show the grant hash and executor/policy identities, which are 64-hex SHA256 values, under the existing quoting/secret checks and 'Do not truncate' (L104-106). No display convention is given, and both btq and admind treat runs of 64 or more hex digits as secrets.

Accepted. Section4 defines public nonce, short human digest and full typed digest; structured validation is not blanket secret bypass.

## 29. R1 line 175

> The persisted last-observed-UTC rollback guard has no scope, tolerance or recovery. A brief forward clock step followed by correction holds all work indefinitely, while a lagging clock silently stretches the window. Existing admind code clamps instead (src/heterodyne/admind/daemon.py:1630-1633; store.py:460-461).

Accepted. Section6 fixes1s UTC/boottime divergence bound and stable new-epoch recovery without reviving old grants.

## 30. R1 line 65

> The timestamp format, precision and boundary inclusivity for not_before/expires_at are unspecified. btq writes '...Z', while admind's now() writes second-precision '+00:00' and compares ISO text in SQL (src/heterodyne/admind/store.py:158-159).

Accepted. Section2 requires fixed microsecond UTC syntax and integer comparisons, exclusive expiry.

## 31. R1 line 10

> The goal is blocking approvals only for new or changed designs and deployments, but delegation covers only operations enumerated in advance. Reactive routine management still needs blocking per-card approvals, and the design never discloses that limit.

Accepted limitation. Section1 discloses exact nonreactive finite scope; unlisted work retains existing perrequest authority or HOLD.

## 32. R1 line 104

> The card path (question or approval) and size budget are unspecified. The full disclosure for the 32-operation bound (IDs, kinds, deltas, endpoint facts, holder evidence) likely cannot fit supported multipart posting; the verifier estimates roughly 15-20 simple operations or about 5 reclaims. This is discovered only after the descriptor is signed and published.

Accepted. Section4 one-card existing bounds, all mandatory facts preflighted before signing; reduce scope mechanically, no silent truncation or extra model rounds.

## 33. R1 line 120

> No delivery-before-answer gate is specified for activation (question asks lack the approval path's card_delivered gate). An answer to part 1 of a multipart card can therefore activate before the later parts carrying safety facts are delivered.

Accepted. Section4 delivery event must precede eligible answer in Store sequence order; early answers require fresh postdelivery consent.

## 34. R1 line 126

> Delegated reclaim must reuse the shared Handoff path, whose reservation requires a non-empty 'ask' (bin/btq:557, 1032-1034), while L126 forbids putting a grant ID in an ask field. The delegated record shape and emitter/resume routing are unspecified.

Accepted. Section7 preserves actual activation ask ID in native Handoff metadata, introduces explicit management-only mode/version resume adapter and rejects mixed/old reader interpretation.

## 35. R1 line 195

> The five-second initiation check comes before 'invoke existing native adapter', but Handoff.move does multi-second pre-write work (locks, liveness, git checks) inside the adapter. The actual assignee write can therefore begin well after the deadline.

Accepted. Section7 scoped Queue.bd_op subclass intercepts exact mutation at subprocess boundary after native setup; immediate deadline checks, durable intent, no arbitrary argv.

## 36. R1 line 106

> 'Unanswered asks remain durable', but stale or duplicate activation asks are never retired or superseded, and question asks never expire. An old activation card can therefore be answered and activated later.

Accepted. Section4 registration generations, tombstones and supersession checks prevent late answers resurrecting stale asks.

## 37. R1 line 74

> Dependency 'replace' admits a type-only change on the same endpoints, but native Beads stores one edge per (issue, target) and a different-type add raises DependencyTypeConflictError, so add-before-remove can never succeed.

Accepted. Section3 excludes same-endpoint type-only replacement when native add-first cannot be safe.

## 38. R1 line 107

> 'Terminal diagnostics/repair use only already granted scope' conflicts with documented terminal recovery routes that act beyond any grant, without saying which governs. Those routes are the broken-transport escalation in the Z design (docs/designs/2026-10-16-management-design-task-recovery.md@e73eab04), beads-task-queue docs/MANAGE.md:114-125 and docs/admind-guard-recovery.md:79-89.

Accepted current instruction. Section1/4 permits terminal diagnostics/already-authorized repair only; every new consent/question uses Marmot. Historical fallback does not supply active authority.

## 39. R1 line 192

> No write-ahead or deterministic request ID is persisted before reserve, and 'inactive pre-crash reservation' is undefined. A lost reply or a crash between reserve and persistence leaves a consumed slot the restarted run cannot claim, and cap/git refusals after reserve also burn slots.

Accepted. Section7 persists request ID before reserve, same-binding retries after lost reply; locks/cap/facts validated first.

## 40. R1 line 156

> The design does not specify who may settle after a policy change, how an uncertain result is upgraded, or what happens when settling with a different outcome_hash. Meanwhile L156-158 hold all reserve/permit/settle calls on changed policy.

Accepted. Section5 observational settlement works after authority loss; append-only upgrades preserve unknown evidence; conflicting same-event hashes hold.

## 41. R1 line 176

> 'Unissued' and 'daemon boot identity' are undefined ('issued' also names the executor's durable record), and no API exposes the executor-side issued state. After an admind restart, implementations will diverge between continuing and holding permanently.

Accepted. Section7 explicitly distinguishes permit issuance, native_intent and dispatched states; restart invalidates permits, no automatic replay after intent.

## 42. R1 line 223

> d2's separate gate, 'Liam's explicit btq master go before landing queue changes' (d2 L21, L23), is not carried forward; L223-226 list only code reviews and approved deployments.

Rebutted as superseded instruction, not removed provenance. Current explicit operator merge authorization overrides historical separate master-go. Section1 preserves genuine code review, design/deployment approval and native gates without inventing permerge approval.
