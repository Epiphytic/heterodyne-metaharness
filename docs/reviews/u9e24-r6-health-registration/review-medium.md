Public copy: absolute local repository prefixes normalized; all findings retained. Raw SHA256: 1a6df95385268b806b34c4d07fda50b706420bff5526631c537d25d869365f52.

9 findings, most severe first:

1. `src/heterodyne/admind/recovery.py:656`: Finish-health registration never checks whether the backup owner is still alive or whether the backup is still active or issued, so a consent notice can be posted for a backup the queue will always refuse.

   Failure scenario: backup.json has state="active" or "issued", the owner process (pid, start, boot) is still running, and a matching backup-failure.json exists. original_provenance accepts it and register posts a "Maintenance recovery consent" notice. Liam replies settle. The queue's finish_backup_health then raises 'backup owner still alive or unknown' (btq-maintenance.py:774). The result is an ask Liam answered that can never succeed, and it uses up notice capacity.

2. `src/heterodyne/admind/recovery.py:1099`: The finish-health notice shows only 'Backup owner: pid= start= boot=' and leaves out the backup state and failure-marker facts the consent will clear. It also keeps the generic line 'Remaining native unknowns: never cleared by this consent', which contradicts what finish-health does.

   Failure scenario: For a backup with state=unknown plus a failure marker, Liam sees a pid/start/boot triple that tells him nothing, and is told unknowns are never cleared. Approving it actually moves backup.json from unknown to complete and settles backup-failure.json (queue issuing 'after': dict(record, state='complete')). He is consenting without the material scope, and the provenance claim is only partly true.

3. `tests/test_admind_recovery_health.py:80`: The bad-owner and bad-ordinal cases (pid True, start 1.0, ordinal True) are rejected by msgspec strict decode as 'malformed', so the new semantic checks in original_provenance are never exercised.

   Failure scenario: Remove 'owner.pid <= 0', 'owner.start < 0', 'not valid_boot', the ordinal range check, the '(ordinal == 0) != (intent is None)' check, or the intent/source HEX checks from lines 660-668. All 20 new tests still pass. A backup.json with pid=0, a non-UUID boot, ordinal=0 with a non-null intent, or a non-hex source_sha256 would then register a notice, and nothing catches the regression.

4. `tests/test_admind_recovery_health.py:75`: Several refusal branches added in the diff have no negative test: missing backup-failure.json, coverage.json hash drift, backup_policy that is null or not a dict, policy source not in enrollment sources, and enrollment profile that is not a dict.

   Failure scenario: The 'enrollment-drift' coverage-hash check and the 'backup-source' branches at lines 674-686 could be inverted or deleted without any test failing. A coverage.json swapped to point backup_policy.source at a different enrolled source would then register a consent notice bound to the wrong source.

5. `src/heterodyne/admind/recovery.py:641`: The fix hand-copies the queue's backup_record, valid_owner, backup_failure, and backup_target validators (btq-maintenance.py:2447, 2543, 2829, 719) into admind, which gives two validators that can drift apart.

   Failure scenario: If the queue changes the backup schema (for example the ordinal bound, owner fields, or failure-marker fields), admind starts refusing real health targets as backup-identity or malformed, or starts accepting records the queue rejects. This is the same class of composition break this repair exists to fix. A deeper fix would have the queue publish a validated target snapshot or digest set that admind only hash-compares, or share one schema definition.

6. `src/heterodyne/admind/recovery.py:658`: The new code repeats the inline UUID canonical-form try/except that already exists in descriptor() at lines 207-213 instead of factoring out a shared valid_uuid helper.

   Failure scenario: There are now two copies of the same UUID validation in recovery.py, which adds to the Service verbosity and churn debt the doc already lists as UNWAIVED. A future fix to one copy, such as rejecting uppercase or braced forms, can miss the other.

7. `src/heterodyne/admind/recovery.py:641`: A roughly 65-line, two-mode method is added to the Service class (public name original_provenance, with mode branching inside), which makes the measured Service verbosity debt worse instead of moving pure validation into module-level functions.

   Failure scenario: Service grows further even though the doc records that its verbosity is UNWAIVED. The finish-health path is a pure function of (root, obj, enrolled) and could be a module-level validator next to descriptor(), which keeps the stateful class smaller and lets the validator be tested on its own.

8. `tests/test_admind_recovery_health.py:149`: test_health_exact_target_null_root_certificate_advances_generation repeats the rewritten r2 test test_finish_health_exact_null_effect_root_certificate, which now uses the same health() fixture and the same deliver/message/permit/certificate/generation-2 flow.

   Failure scenario: The same scenario runs twice, and two tests must be kept in step whenever the fixture or the permit flow changes. Either drop the new test or keep only the r2 version.

9. `tests/test_admind_recovery_r2.py:327`: The r2 test now imports its fixture from another test module inside the function body (from test_admind_recovery_health import health), so r2 depends on a second test file being importable.

   Failure scenario: Renaming or moving test_admind_recovery_health.py, or running r2 alone with a different rootdir or import mode, breaks the original 'mandatory' r2 test at runtime with ModuleNotFoundError rather than at collection. A shared conftest or helper module is the conventional home for health().

To answer the question directly: I found no critical flaw that rules this out as an actual-health-source PoC candidate. I checked the new validation against the accepted queue at 46fd58a (`backup_record`, `valid_owner`, `backup_failure`, `backup_target`, `prepare_health`). Both sides hash records the same way, both bind the record hash to the original-record and backup-before hashes, and the field rules match. Non-health modes still read the ordinary operation record and validate the actor exactly as before.

The most important gaps:
- **Liveness:** registration doesn't check owner liveness or backup state, so Liam can get a consent ask the queue will always refuse.
- **Notice wording:** the finish-health notice says unknowns are "never cleared", but approving it clears the backup's unknown state and failure marker.
- **Test coverage:** none of the new tests exercises the owner/ordinal/intent checks or the coverage/policy-source refusals, so removing those checks would not fail any test.

As instructed, I only read the code: no edits, test runs, probes, or queue/service/network access. The eight earlier R5 findings and the open quality debt are unchanged and not re-reviewed here. Files: `src/heterodyne/admind/recovery.py`, `tests/test_admind_recovery_health.py`, `tests/test_admind_recovery_r2.py`, and for the queue reference, `<accepted-queue>/bin/btq-maintenance.py`.
