# btq-4kcjg first HIGH author incorporation

Actual first FULL HIGH verdict: **REVISE**, ten findings, no verification pass.
Raw report SHA256: `16905cbbe4f2de6e741e5c523c5ea79646e8b8fea5e7bc0cb4c217ab3ab8e664`.
The adjacent raw report is byte-identical. This is author disposition, not reviewer approval.
No second review or deployment occurred during these repairs.

## Finding 1

1. `src/heterodyne/admind/retirement.py:546`: publish_notice skips republishing whenever a mapping row with the same proof sha256 exists, without checking that the bound outbox row was actually sent with a message_id, so a notice that fails delivery permanently prevents retirement.

   Failure scenario: A suspended hold (e.g. maintenance-held:changed-card) gets its notice queued, but the outbox marks it 'failed' (mark_failed) or 'sent' with message_id=None. Suspended holds never re-invoke the child, so the proof sha never changes. Every later `!asks` refresh hits `row[0] == proof.sha256` and returns. select() needs o.status='sent' AND o.message_id=target, so no reply can ever select the proof and the attempt keeps its slot forever.

**Author disposition:** FIXED. Explicit inspection checks the mapped outbox delivery state. Failed or sent-without-message-ID mappings get a new durable notice identity; pending or successfully delivered mappings remain idempotent. Original delivery rows and selector bindings are retained. Two regression variants exercise selection after replacement.

## Finding 2

2. `src/heterodyne/admind/retirement.py:548`: For retryable pending-maintenance overlays, each ~30s retry re-invokes the child and rewrites the proof (invocations is in the hashed body), but hold_maintenance publishes with refresh=False, so the delivered token goes stale within one retry interval.

   Failure scenario: The fence is held and the attempt retries every MAINTENANCE_RETRY_SECONDS with an exact HOLD each time. The operator runs `!asks` (new notice queued), waits for delivery and replies on a phone. If the reply lands after the next retry, select() finds no matching sha and returns 'Retirement refused'. The design says retryable overlays are eligible, but in practice they need a sub-30-second reply; the test test_no_per_retry_proof_or_notice_growth confirms the original token stops selecting after a retry.

**Author disposition:** RETAINED UNWAIVED LIMITATION. The approved ADR requires the exact current invocation/proof generation. Accepting a stale token across a retry would change that authority contract. Automatic retries still create no per-generation notices; explicit inspection offers the current selector. Original stale-generation security assertions are unchanged. The phone-response window remains a deployment/usability limitation requiring a genuine design change to relax.

## Finding 3

3. `src/heterodyne/admind/retirement.py:617`: Retirement requires the current approve-bead file SHA256 to equal the identity captured at the last HOLD, but suspended holds never invoke the child again, so any legitimate adapter upgrade permanently freezes every suspended attempt.

   Failure scenario: Deploying the planned exjsy/fence adapter repair (or any approve-bead update) changes the file bytes. Every existing maintenance-held:* attempt then permanently gets 'native adapter changed; original retained', with no path to refresh the proof. All 20 slots can stay starved by attempts that do have a full not-issued proof chain.

**Author disposition:** RETAINED UNWAIVED LIMITATION. The approved ADR binds native readback to the captured adapter. An upgrade does not authorize a replacement adapter to reinterpret older evidence. A regression proves changed identity performs no native read or retirement. Future retained-source/upgrade recovery needs separately approved compatibility; no new authority is invented here.

## Finding 4

4. `src/heterodyne/admind/retirement.py:358`: record_hold overwrites adapter_identity on each invocation without requiring it to match the previous proof's identity, and 'consecutive' is always set to count, so the chain does not record that all invocations ran under one adapter.

   Failure scenario: Invocation 1 runs under adapter A and returns HOLD. The approve-bead file is swapped to B, and invocation 2 under B returns HOLD. The proof now holds only B's identity with consecutive=2, and retire() checks only the current identity against B. Invocation 1's evidence under a different adapter is silently merged and lost, contrary to the design's 'captured coordinator/adapter identity' per chain.

**Author disposition:** FIXED. Later HOLD coverage requires exactly the prior adapter identity. Different adapter bytes leave the existing proof untouched and make the uncovered invocation ineligible. The independent not_spawned counter now distinguishes actual HOLD observations from positively known spawn failures.

## Finding 5

5. `src/heterodyne/admind/store.py:1181`: issue_decision increments the never-reset invocations counter before the child is spawned, and a spawn failure with launched=False is not recorded as not-spawned, so one transient fork/exec failure permanently disables retirement for that attempt.

   Failure scenario: A maintenance retry hits EAGAIN or ENOMEM in reap.spawn, which raises BtqError('unavailable') with launched=False. The stop is held as read-unavailable and invocations=n+1 while the proof stays at n. Every later exact HOLD fails `previous.invocations != count-1` in record_hold, so a provably not-spawned generation strands the attempt. The design allows atomic not-spawned marking for exactly this case.

**Author disposition:** FIXED. A positively observed pre-spawn BtqError(unavailable) can extend an existing exact proof chain through a separately counted not_spawned invocation. The invocation counter never rolls back. Post-launch failure cannot gain this evidence; missing first or earlier coverage remains held. Regressions cover subsequent HOLD recovery and post-launch refusal.

## Finding 6

6. `src/heterodyne/admind/retirement.py:585`: Receipt lookup is keyed on (token, notice_id), so 'already retired' is only reported for the same notice and token, and the same-command branch returns with no reply.

   Failure scenario: Two cases. (a) The attempt is retired via notice N2 (after an `!asks` refresh), and another operator replies to the older N1 with its token. receipt() misses and select() fails, so they get 'Retirement refused: reply to the current eligible held notice', not the design's 'reports the existing retirement'. (b) A direct re-invocation with the same command_id returns silently, without the 'returns its existing receipt' behavior the design requires.

**Author disposition:** FIXED. Immutable notice history maps each delivered selector to its exact ask/attempt. Existing retirement lookup uses that binding rather than treating old tokens as current authority. Same-command calls return the existing receipt without effects; another authenticated command gets a receipt reference and no native read. Earlier pre-upgrade notices without retained exact mappings are not reconstructed by guessing.

## Finding 7

7. `src/heterodyne/admind/retirement.py:552`: The notice, unavailable-notice and `!asks` texts embed the 64-hex Marmot attempt message_id, which redact()'s _HEX_RUN (>=64 hex) replaces with '<redacted hex key>', so the held attempt identity the design requires to be shown never reaches operators.

   Failure scenario: The notice renders as 'Ask k7m2: held approve attempt <redacted hex key>. Reply directly...'. The same happens in the 'retirement unavailable' notice (line 538) and the daemon `!asks` line (daemon.py:1163, via finish->reply->redact). Operators cannot tell which held attempt a token or diagnostic refers to when one ask has several historical attempts.

**Author disposition:** FIXED. Attempt identities render as hyphen-separated eight-hex chunks, preserving the entire identity through existing secret redaction without adding a redaction exemption. Regression checks the actual outbound rendered text.

## Finding 8

8. `src/heterodyne/admind/retirement.py:509`: The receipt labels every blocked retirement reason 'native-conflict', even when blocked came from explicit not-open or not-approval readback, and it records a synthesized 'ref' ('marmot:id:'+command) rather than the inbound's actual ref.

   Failure scenario: The native card is closed but undecided, or kind_approval=false. settle returns 'blocked' or kind_approval is false, so blocked=True and the immutable receipt says reason='native-conflict'. The design requires distinct provenance for the conflict, not-open and not-approval branches, and the permanent audit record misstates why the local attempt was blocked.

**Author disposition:** FIXED. Receipt reasons distinguish not-approval, not-open and native-conflict. References use the existing canonical_ref transformation from the actual inbound command, matching normal native decision provenance. Separate tests assert both reasons and exact reference.

## Finding 9

9. `src/heterodyne/admind/approvals.py:176`: ApproveBead.identity() synchronously reads and SHA256-hashes up to 8 MiB on the asyncio event loop, twice per decide() and twice per retire.

   Failure scenario: With up to 128 retryable overlays each retrying every 30s, each decide call does two blocking file reads and hashes, and retire adds two more around an awaited read. A large adapter or slow filesystem stalls the daemon loop (membership observation, outbox sends) for every call. Hashing via asyncio.to_thread, or caching keyed on (st_dev, st_ino, st_mtime_ns, st_size), would avoid this.

**Author disposition:** FIXED WITH EXPLICIT BOUNDARY. Await-safe phases hash off-loop; the final no-await launch fence uses only a metadata-validated cache. Cache misses or drift refuse before spawn. Digest bytes are read through a bounded opened FD whose before/after fstat stamps agree, keyed by dev/ino/size/mtime_ns/ctime_ns. Original no-await regression is unchanged. New tests cover off-loop hashing and cold/drifted final-fence refusal. Local filesystem stat latency remains a trusted-host limitation, not a confinement guarantee.

## Finding 10

10. `src/heterodyne/admind/retirement.py:570`: The new code duplicates existing helpers and adds redundant checks: raw `store.db.execute('SELECT reply_to FROM inbound...')` outside the store lock instead of Store.reply_target, lowercase-hex validation written out three times plus the parse regex, the token re-checked after commands.parse already validated it, a duplicate `type(value) is not dict` test, and a 'consecutive' field that always equals 'invocations'.

    Failure scenario: Four copies of the same hex validator and a bypassed @_locked accessor drift independently. The redundant 'consecutive' field implies chain information that is never independently checked, which hides the gap in finding 4 from future readers.

**Author disposition:** PARTLY FIXED / SOURCE-BACKED REBUTTAL. Added locked Store.inbound_thread for the actual inbound parent: Store.reply_target intentionally returns a different outgoing-reply reference and is not interchangeable. Reused DIGEST validation and removed the duplicate dictionary type check. Consecutive HOLD count is now independently related to not_spawned coverage. Direct retirement entrypoint token validation stays defense in depth because callers can bypass commands.parse; removing it would weaken the callable boundary.

## Verification and remaining maintenance debt

Original retirement test bytes are unchanged. Initial repair regressions failed
8 tests before source changes (one passed); the failed intermediate import and
no-await compatibility runs remain retained in private evidence. The final
seven-module affected run passed 382 tests. Configured Pyright analyzed 357
files with zero errors or warnings. Ruff checks pass on all changed Python
files. These are synthetic offline tests, not deployed recovery evidence.

The measured quality delta is **not clean**: 93 reported regressions, 74 gating,
zero acknowledgements. Actual maintenance costs include run_attempt complexity
56 to 60, retire complexity 24 to 30, one extra receipt argument, growth in the
bounded proof/notice implementation and added fixture duplication. Lock-aware
SQLite accessors share a small existing row-reading shape; grouped attempt
identities use a different human representation from fulfillment digest groups.
The full report is retained without baselining or suppressing its findings.
Security state transitions were kept explicit rather than applying unrelated
refactors to make the measurement pass. These costs remain unwaived for review.

No native read, operator message, service action, deployment, new approval or
review invocation occurred during author repair. Existing unknown/legacy proof
and changed-adapter holds remain fail-closed.
