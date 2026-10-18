**Verdict: good enough for a PoC, with no blocking flaw while executor activation stays separately gated.** Two findings should still be fixed before any executor is wired in: the first one below, and the ignored group-membership check in the second.

What checks out:
- **Fresh proposals:** each operation keeps an immutable history plus a pointer to its current proposal. An old settle only records history, and an old permit request is refused as superseded.
- **Legacy migration:** it is transactional and refuses any unexpected schema, index or trigger.
- **Revoke:** it gets its own reserved slot and works even when the clock or expiry check fails.
- **Source guard:** it now covers approval posts only. Question and merge posts work again.
- **Conflict fence:** permit checks it before issuing or repairing.
- **Publication:** files are written after the database commit.
- **Cancellation:** the worker's own error no longer replaces the cancellation.
- **Foreign-group forensics:** guard drop/ignore auditing runs before the recovery check.
- **Nullable-root certificate:** `effects_root=null` must match exactly in `certificate()`.

This is a single-context review with no verify pass. Some findings are uncertain and flagged that way.

10 findings, most severe first:

1. `src/heterodyne/admind/recovery.py:467`: supersede() lets a fresh proposal replace one whose permit has an unknown consumption state, if the 30-second window has passed or the boot changed. It never checks for the consumed/issuing/receipt files that certificate() checks, which goes against ADR §6: 'A new human proposal cannot bypass unknown consumption or replay any begun transaction' and 'Boot change before consumption also holds'.

   Failure scenario: Proposal P1 gets permit generation 1. The executor writes P1.consumed.json and starts recovery, then crashes or the host reboots. After 30 seconds, or right away on the new boot, the controller registers P2 for the same operation. supersede() only compares boot_id and deadline, so it passes. The operator settles P2 and permit() issues a fresh usable permit for an operation with a begun transaction. Only the unbuilt executor's before-hash check would stop a replay.

2. `src/heterodyne/admind/daemon.py:812`: recovery_message() calls await self.check_group() and throws away the result. Every other authority path (on_recovery at 767, and on_message's ordinary paths at 2520/2561) refuses when it returns False. ADR §6 requires a check against CURRENT group membership.

   Failure scenario: check_group() returns False because the subscription generation or membership epoch changed mid-check ('group-check-superseded'). That path leaves group_ok True and observing unchanged, so recovery_authority(sender) still returns a digest and a '!maintenance settle' is recorded as 'consented' against a membership observation that was rejected.

3. `src/heterodyne/admind/recovery.py:758`: The conflict fence is written only inside refusal(), after authority() returns non-None. A conflicting replay that arrives while the sender is not a current operator, or while the policy can't be read, is never fenced. Guard drops (stranger or latched sender) also never reach refusal().

   Failure scenario: Consent message M is recorded. The transport re-delivers M with a different payload while the policy file is briefly unreadable (refresh_authority returns False). message() raises replay-mismatch, refusal() sees policy None and returns, and no fence row is written. Once the policy is readable again, permit() finds no conflict and issues a permit, so the 'recorded conflicts refuse permit' rule fails.

4. `src/heterodyne/admind/recovery.py:879`: The renewal certificate only repeats descriptor fields plus observed_boottime_ns. It binds no current target snapshot, which ADR §6 requires ('bound to permit/generation/proposal and current exact target snapshot'). For finish-health with a null effects_root, nothing ties it to observed state at all. The dict comparison is also loose: generation true or 1.0 equals 1.

   Failure scenario: Between generation 1 and the certificate, the backup or failure target changes state. The queue publishes a nonconsumption certificate that copies the descriptor's fields, and permit() issues generation 2 with nothing showing the snapshot is unchanged. Separately, a certificate with "generation": true passes the value != expected check.

5. `src/heterodyne/admind/recovery.py:149`: descriptor() never cross-checks fields against each other: mode against local_records[].action, effects_root nullability against mode or effect_count, or backup/failure-before hashes against finish-health.

   Failure scenario: A mode=settle descriptor with effects_root=null, effect_count=4096 and an action='finish-health' record is accepted. The notice tells the operator 'Proof-covered effects: 4096' with no effects root, and the operator consents to a scope that no adapter should accept. Rejection is deferred to the executor, which does not exist yet.

6. `src/heterodyne/admind/recovery.py:390`: recovery.Service is built unconditionally in Admind.__init__. If upgrade_proposals() raises Invalid('proposal-schema'), or a store error happens during migration, the whole daemon fails to start, not just the recovery feature. The recovery socket also starts with no enablement flag (fulfillment.ENABLED does have one).

   Failure scenario: A database has a hand-added index on maintenance_recovery_proposals, or a schema from an intermediate build that differs in whitespace or columns. Admind construction raises, and all ask, approval and control service stops for a feature that has no live authority.

7. `src/heterodyne/admind/daemon.py:745`: permit() and register() run in asyncio.to_thread and call recovery_authority, which calls authorised() and then refresh_authority(). That reassigns self.operators and self.policy_operators and writes audit records from a worker thread while the event loop keeps running code that is not under work_lock.

   Failure scenario: During a permit request, the outbox sender or check_group on the loop reads self.operators while the worker thread swaps or clears it (self.operators = {} on a policy-read failure). Ownership and authority decisions on the loop see a state that was never checked under work_lock. This is a low-probability race.

8. `src/heterodyne/admind/ctl.py:183`: For the ask socket, every request line is fully JSON-decoded into an untyped envelope just to read 'type', before the max_request length check, and then decoded again with the typed decoder.

   Failure scenario: Every ask, fulfillment or operation request costs two full JSON parses, and an oversized line (up to the reader limit) is parsed before being rejected. A cheaper option is to check the length first and decode once into a tagged union (or msgspec.json.decode with a small Struct holding only `type`).

9. `src/heterodyne/admind/recovery.py:459`: The boot_id UUID check and BOOTTIME integer check are written out three times (supersede, permit, boot_id()), and message() decodes the stored descriptor twice in a row (lines 689-690).

   Failure scenario: Three copies of the boot/clock validity rule can drift. For example, if permit() is tightened but supersede() is not, supersede's same-boot overlap check could accept a boot value that permit rejects. One shared helper like `current_boot() -> tuple[str, int]` would fix it, and the descriptor should be decoded once into a local.

10. `src/heterodyne/admind/daemon.py:1501`: The 'source withdrew → cancelled' mapping and its wording is still repeated in close_refused, the issue_decision failure path, the stale-settle branch, settlement() and the maintenance-retry path (the retained OPEN duplication finding 9). There are now at least five copies of the literal text.

    Failure scenario: Changing the withdrawn semantics or wording means editing five places. If one is missed, the ask status and the reply disagree, for example status 'cancelled' with 'no native decision was issued' in one path and 'was recorded' in another, as already happens between close_refused callers.
