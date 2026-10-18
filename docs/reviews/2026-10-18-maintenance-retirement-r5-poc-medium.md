The new code is good enough for a PoC. I found no critical flaw that blocks it.

Here is what the latest commit (1220d34) does and what still holds:
- **What changed:** an empty or out-of-date cached adapter identity no longer refuses to launch the decision.
- **Proof checks still hold:** `record_hold` rejects an empty identity (its `DIGEST.fullmatch` check). Retirement checks that the adapter still matches the proof's identity on both sides of the native read. A real launch failure (`OSError`) still becomes `NotSpawned`.

The main trade-off is finding 1. Under the old refusal, a maintenance retry that hit a cold or changed cache could leave an existing proof chain usable (it went down the `NotSpawned` path). Now the adapter runs, any maintenance-hold result is stored with an empty identity, and that attempt can never be retired with proof again. Even a reinstall with identical bytes, or a plain `chmod`, triggers this. The approved ADR's rule "If exact coverage cannot be represented or verified, HOLD" allows it, so it is not a blocker. But a cheap fix exists, and adapter reinstalls are most likely during maintenance windows, when this path runs. Findings 2–7 are smaller: one silently removed design rule, unclear operator messages, missing tests and test hygiene.

I only read code and git history, using `git diff`/`git show`. I made no edits and ran no tests, probes, services or queue commands.

7 findings, most severe first:

1. `src/heterodyne/admind/approvals.py:278`: With the NotSpawned refusal removed, a maintenance retry whose cached identity is cold or changed now runs the adapter. A resulting maintenance hold carries identity "", so record_hold rejects it while the invocation count still advances. An attempt that already had a valid retirement proof becomes permanently ineligible.

   Failure scenario: An attempt has an eligible proof at invocations=N. Before the next maintenance retry, the adapter is reinstalled with identical bytes, or chmod'd, or hardlinked; any of these changes ctime and so the stamp. cached_identity() returns "" and the child returns a maintenance hold. MaintenanceHold(adapter_identity="") makes record_hold return False (DIGEST check) while the generation is now N+1. From then on eligible() needs proof.invocations == generation, and record_hold needs previous.invocations == count-1, so neither can succeed. Retirement is closed for good and needs manual recovery. Under R4 the same case went down the NotSpawned path and record_not_spawned could keep the chain.

2. `src/heterodyne/admind/approvals.py:277`: On a cache miss, the pre-launch identity is thrown away even though the cache still holds the earlier byte hash. A maintenance hold from an adapter whose bytes did not change is still treated as having drifted. Root cause: the before/after check compares stat stamps when it should compare content.

   Failure scenario: Metadata-only change (touch, chmod, link count, or a reinstall with the same content) followed by a maintenance hold: before is "" and after is the real hash, so they differ and the proof is lost. A deeper fix: decide() keeps the stale cached hash as `before` (no file read at the fence), and the hash identity() computes after the hold verifies that the bytes match. Then only real content drift disqualifies.

3. `src/heterodyne/admind/approvals.py:214`: The old guarantee 'drift needs a fresh read phase' is gone, so a decision can run on a different adapter binary from the one that passed the gate-preview read and preflight. Nothing replaces that check.

   Failure scenario: ab.read() (gate-preview and preflight) runs adapter v1. The adapter is replaced with v2 before decide(). cached_identity() sees the drift and returns "", but decide() now launches v2 with --yes --expect-digest anyway. The native decision is made by a binary that was never preflighted. The --expect-digest check limits the damage, but the 'fresh read on drift' rule from the R4 docstring is silently dropped rather than recorded as a decision.

4. `src/heterodyne/admind/daemon.py:1391`: hold_maintenance ignores record_hold's False return, so when a maintenance hold is rejected for having no adapter identity, nothing is audited and the cause cannot be seen.

   Failure scenario: A cold or changed identity causes a hold to be rejected. The operator then gets only the generic 'retirement unavailable: complete since-begin not-issued proof is missing' notice (retirement.py:396). No audit event says the adapter identity was unavailable or changed. Operators cannot tell this new, expected route to ineligibility apart from a lost proof or a bug.

5. `tests/test_admind_retirement_identity.py:60`: The new tests cover only the port (ApproveBead.decide). Nothing runs the daemon's run_attempt maintenance-retry path with a cold or changed identity after an earlier proof exists, so the irreversible loss of eligibility (finding 1) is never asserted.

   Failure scenario: A test that has a proof at generation N, then a retry with a cold cache returning a hold, should assert that eligible() is None and that the proof bytes survive unchanged (the ADR's 'Negative tests must assert original Attempt/inbound/proof bytes survive'). Without it, a regression that silently overwrites or deletes the proof, or wrongly keeps it eligible, would go unnoticed.

6. `tests/test_admind_retirement_identity.py:37`: The 'unreadable' case patches approvals.os.open, which is the process-wide os.open, and only simulates an execute-only adapter. A real mode-0111 file is never tested.

   Failure scenario: Any other os.open call during the scenario (asyncio internals, logging, the to_thread hash after a hold) would also raise PermissionError, which could hide behaviour differences. Also, a real execute-only adapter fails differently (EACCES on open after stat succeeds) from what this mock produces at the call site, so 'execute-only' in the commit message overstates what is covered.

7. `tests/test_admind_retirement_r2.py:239`: test_real_adapter_structural_not_spawned_sites lost its parametrisation and now checks only one site, but its plural name ('sites') was kept, which misleads about how much is covered.

   Failure scenario: A future reader assumes the identity-cache NotSpawned site is still covered, or restores it under this name. The name should be singular (for example ..._popen_not_spawned) to match the single Popen OSError case.
