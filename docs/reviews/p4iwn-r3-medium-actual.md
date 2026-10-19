Short answer: there's one real regression to fix before this goes ahead as a dormant PoC. Beyond that I found nothing critical, but the cross-boot rate quota and the asymmetric readback of renewed permits (findings 2 and 3) need a decision. This is not an approval. I made no edits and ran no tests, probes or verify pass, so treat every finding as plausible rather than confirmed. The blockers you already listed (production source/transport, native ACK, queue settlement, physical reserve, failed-notice recovery, intake redelivery, quality debt) stay open and aren't repeated below.

What looks fixed in R2:
- **Conflicting replay:** now needs current authority and the right notice thread.
- **QueueFull:** the direct fallback path now writes the `command-capacity` audit.
- **Wall clock:** registration rate limiting no longer depends on it.
- **Duplicate conflict path:** `_block_conflict` is gone.
- **Command storms:** an 8KiB diagnostics bound now stops them from crowding out the three permits.

The regression is finding 1. The new "skip if already published" check reads files directly. A crash at one specific point during publication leaves a file the read refuses, and the existing repair helper never gets to run. Before R2 that crash was repairable; now it fails on every retry.

No CLAUDE.md applies (its only rule is a Rust clippy gate and this code is Python), so there are no conventions findings.

10 findings, most severe first:

1. `src/heterodyne/admind/gateway_custody.py:614`: The new _published() pre-check calls recovery.private_read(), which refuses a file with st_nlink != 1. That bypasses recovery.publish()'s FileExistsError -> repair_alias path, so the exact link-before-unlink crash that the shared publisher was built to repair can no longer be repaired.

   Failure scenario: Issuance commits generation N to SQLite. recovery.publish(immutable) runs os.link(temporary, path), then the process dies before temporary.unlink(). The immutable file now has nlink=2 with a .recovery-<hex> alias. On the exact retry, the `complete` calculation (line 529) and the pre-publish check `if not self._published(immutable, encoded)` (line 599) both call private_read, which raises Invalid('unsafe-file'). The daemon replies 'refused unsafe-file' on every retry. Before R2, publish() was called unconditionally, hit FileExistsError, ran repair_alias and finished the publication. The retained permit is now stuck partly published for good. The tombstone and current-pointer checks have the same problem.

2. `src/heterodyne/admind/gateway_custody.py:793`: The notice rate counts every row from a different boot, or with a missing or legacy stamp, as 'recent' forever. Proposal rows are never deleted, so after one reboot the hourly limit becomes a permanent lifetime limit that disables registration.

   Failure scenario: 32 or more proposals are registered over the daemon's life, all expired within 24h. The host reboots, so the source boot_id changes. Each old row now satisfies `boot != observed.boot_id`, so recent >= 32 and every later register() raises 'notice-capacity' with no prune, export or recovery path. Upgraded databases hit the same wall at once through their unstamped legacy rows. A one-hour rate limit has become a quota that never resets.

3. `src/heterodyne/admind/gateway_custody.py:540`: A complete readback at generation 2 or 3 skips the consumed/issuing/receipt checks and the superseded-N check that a generation-1 readback still enforces, and still replies 'issued' (CLI exit 0). R1's 'permit-superseded' refusal is gone for renewed permits, and test_published_renewal_readback_needs_no_new_window_or_source_certificate makes the new behaviour a test requirement.

   Failure scenario: Generation 2 is fully published, then the queue writes K.consumed.json, or K.nonconsumption-2/K.superseded-2 to prepare generation 3. `admind gateway-custody permit K` returns result 'issued' with the generation-2 bytes, and the CLI exits 0, so the operator or an automated caller reads it as live, unconsumed authority. The same query at generation 1 refuses with 'consumption-unknown' or 'permit-superseded'. Readback should report that state, or refuse the same way, at every generation.

4. `src/heterodyne/admind/gateway_custody.py:599`: Skipping publish when the file already reads back identical also skips the directory fsync. If the earlier publish failed at os.fsync(directory), the retry reports 'issued' without ever making the directory entry durable.

   Failure scenario: recovery.publish links the file and unlinks the temporary, then os.fsync(directory) raises EIO or ENOSPC, so the call fails with OSError after the link exists. On retry, _published(immutable) is True, so publish and its directory fsync never run, and the call returns the permit as issued. A power loss can then drop the generation file or current pointer that admind said was published. Before R2, the unconditional publish re-ran the directory fsync.

5. `src/heterodyne/admind/gateway_custody.py:926`: When the 8KiB diagnostics ceiling is hit, a decline or revoke still applies state='revoked', but message() raises 'bundle-capacity' and no receipt is kept. The audit and the operator see 'refused' for a revocation that actually took effect.

   Failure scenario: The receipts budget is full after repeated settles. An operator sends `!custody revoke T`. The guarded UPDATE moves the state to 'revoked', then line 868 raises Invalid('bundle-capacity'), and custody_message audits 'refused bundle-capacity'. Nothing records that the revoke succeeded. A redelivery of the same mid repeats the path and is never matched by the exact-duplicate fast path. Operators may keep resending revokes, or wrongly believe consent is still live.

6. `src/heterodyne/admind/gateway_custody.py:580`: The final whole-row CAS fails on any concurrent agreeing-settle receipt, so a routine repeated `!custody settle` in the thread aborts a permit issuance that is in flight.

   Failure scenario: issue_permit reads the row, then spends up to about 10s in the authority and source callbacks, which now run outside the lock. Meanwhile the operator re-sends `!custody settle T` with a new mid, which is retained as a 'consented' receipt and changes the bundle bytes. The CAS `fetchone() != row` raises 'proposal-changed' even though consent and policy are unchanged. That retry pattern is the scenario R2 finding 1 described, so issuance can keep failing while the operator keeps retrying.

7. `src/heterodyne/admind/gateway_custody.py:921`: The PERMIT_RESERVE check almost never triggers: once diagnostics are capped at 8KiB, descriptor, notice, decision and diagnostics together stay far below 24KiB. _issue_permit never checks the reserve either, so the 'reserve' amounts to an indirect size bound rather than a guaranteed limit.

   Failure scenario: Cost: the reserve guarantee depends on a check that can't fail in practice while the real bound sits elsewhere, so a later change to MAX_COMMAND_BYTES or the notice size could quietly break the three-permit guarantee without any test catching it. The same block also calls canonical() on the bundle three times per command. A simpler form computes the reserve once as MAX_BUNDLE - len(canonical(bundle without permits)) and asserts it in both writers.

8. `src/heterodyne/admind/gateway_custody.py:517`: The retry/repair logic is nested, generation-specific branching: retry is inferred from the request hash, there are separate gen==1 and gen>1 sub-branches, and `complete` is computed and then rechecked. One 'retained candidate: verify, else repair, else readback' mechanism would replace it.

   Failure scenario: Cost: findings 1 and 3 come from this split. Gen-1 and gen-N readback enforce different checks, and the publication-state probe duplicates what publish already does. Each future fix has to be applied in three branches. A single helper that publishes a retained candidate idempotently through recovery.publish (which already handles existing identical files and aliases), plus one shared queue-state check, would remove these branches.

9. `src/heterodyne/admind/gateway_custody.py:789`: Each register() now loads every row's boot and stamp into Python, up to 4096 json_extract calls over 32KiB blobs, while holding the write transaction.

   Failure scenario: Cost: at 4096 rows each registration parses up to about 128MiB of JSON under the Store write lock and blocks every other admind writer. A single SQL COUNT with the boot and BOOTTIME predicate, or a separate indexed stamp column, would do the same in SQLite without the round trip into Python.

10. `src/heterodyne/admind/gateway_custody.py:470`: issue_permit is now a pass-through to _issue_permit, and publication_lock is still a threading.RLock although nothing re-enters it any more. Both are leftovers from the R1 repair that was removed.

    Failure scenario: Cost: the extra method and the reentrant lock suggest a nested-locking contract that no longer exists. With an RLock, an accidental re-entry by the same thread succeeds silently instead of deadlocking, which a plain Lock would catch in tests. Inline the wrapper and use threading.Lock.
