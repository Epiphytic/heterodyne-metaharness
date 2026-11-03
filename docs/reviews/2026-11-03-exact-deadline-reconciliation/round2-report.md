I ran one full HIGH review of frozen head `3a90c0a` against baseline `7caddc2` in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. It covers the cumulative source and test diff, the touched callees, the three review documents and the approved daf3 owner design. This is exact-reconciliation round two, separate from the PR160/161 preflight history. I made no edits and did nothing live.

- **How the review ran:** the high recipe for the built-in /code-review (8 angles, dedup with no verify, at most 10 findings) came in the task prompt, and I ran all 8 angles myself in this context. I did not make a separate Skill tool call.
- **Model and effort:** the exposed model is `claude-opus-5-5` and the recipe was given at "high". I can't see the CLI, settings or environment effort configuration, and internal effort is not exposed.
- **Tests:** I ran the safe isolated command myself: 18 passed, 324 deselected, in 31.9s. I did not run the full suite, Ruff or Pyright, and I have no result for the repaired full suite.
- **Conventions:** no CLAUDE.md applies to the changed Python files (the user-level rules have no code-style rules for them), so that angle found nothing.

**Verdict: clearance for the bounded source, with no blockers at this head.** I found no path that ends a live or uncertain executor, writes before the owner and snapshot check, or invents death proof. All ten round-1 findings are addressed as the disposition describes:
- **Typed holds:** `OSError` and `LaunchFailed` now become `RuntimeUnavailable`. `json.loads` raised `ValueError` even for 4 KB of deep nesting, not `RecursionError`.
- **Guards:** the resolve-intent and sibling-branch guards run before any effect.
- **Retained pane:** a pane that still exists must match the retained one exactly, and continuation proceeds only when tmux itself reports the pane absent.
- **Readbacks:** LIVE and ENDED both re-read the owner and the record before returning.
- **One journal writer:** `_end` alone writes the STOPPING/WIP record.
- **Conservative holds kept, as you asked:** ends by another path without a receipt, clock rollback and schema changes still hold.

Wiring still belongs to Hermes: native admission/profile/account/model/ancestry preflight, the ending and send fences, paired enrollment, the root installer and live acceptance.

**Advisories (none block this source):**
- Finding 1 (runtime.py:649, no in-process serialization against `expire()`) should be a stated requirement when Hermes wires this.
- Findings 2–4 cover test gaps, a reaper liveness gap and receipt retention.
- Findings 5–9 are diagnostics and cleanup.

9 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:649`: reconcile_session takes no runtime lock against Product's own expire()/stop()/sessions(). expire() calls _end(lifetime) on any RUNNING record once now >= deadline (runtime.py:870). The _held/_claim_branch guards (595-600) also run once at entry and are not re-checked before _end/_settle_wip touch Git.

   Failure scenario: wsd's periodic expire(ws) and a Hermes reconcile_session(original) run at the same time on the same deadline-reaped record. Both run _teardown, _land on the same private git directory, and the lifetime WIP commit on btq/<bead>. Or a resolve intent or sibling launch appears after the entry guard and the WIP commit lands over it. Or expire wins first and reconcile holds for good ('changed without a receipt'). Hermes wiring must serialize this against wsd's own lifetime pass, not just against other Hermes calls.

2. `tests/test_sandbox_runtime.py:3124`: The 'identity' guard only tests a record generation mismatch. The new check that the retained pane matches the receipt (runtime.py:636-640) has no test, and neither do a tampered or oversized receipt, a STOPPING/ENDED record with a non-lifetime cause (631-633), or an unlanded landing failure at the ENDED gate (655).

   Failure scenario: A regression that ends a session whose tmux pane was replaced by a different dead pane, accepts a receipt with a changed 'original' or marker hash, or reports ENDED with unlanded=True would still pass all 18 reconcile tests.

3. `src/heterodyne/sandbox/runtime.py:645`: On a retry where the record is already ENDED, the reconcile never kills the generation's retained dead reaper tmux session (remain-on-exit is on globally). _end kills the reaper only when creates.gone() and close_notice() both succeed, and suppresses tmux errors.

   Failure scenario: close_notice() returns False (an OSError writing the alert) or tmux.kill fails during the first _end. The record is ENDED, but wsd-<sid>-r<gen> stays as a dead pane, so _running() is non-empty and the gate at 656 holds. Every retry then holds at 645 until some other Product path (_restore_creates/_cleanup_create via sessions or launch) sweeps the watcher.

4. `src/heterodyne/sandbox/recovery_evidence.py:92`: Each generation's receipt keeps up to 8 KB of the executor's pane tail (raw agent terminal output) and is never pruned. _drop_markers only globs 'reaped-r*', so deadline-recovery-r<g>.json files pile up in the session directory.

   Failure scenario: An agent printed a token or other sensitive output in its last 40 lines before its deadline. That text now lives forever in host state beside session.json and survives later generations. It is 0600 and outside the sandbox binds, so this is retention and privacy, not exposure.

5. `src/heterodyne/sandbox/runtime.py:570`: The wrapper reduces every OSError and LaunchFailed to 'held: <TypeName>', so a sibling-branch hold (LaunchFailed(BRANCH_TAKEN)) looks the same as any I/O hold.

   Failure scenario: Hermes can't tell 'another session of this bead holds btq/<bead>', which needs an operator or sibling to finish, from a passing ENOSPC/EIO that a retry would clear. It may retry forever or escalate the wrong thing.

6. `src/heterodyne/sandbox/runtime.py:1390`: deadline_evidence is only checked for None, so its contents are never used, and callers also pass cause=EndCause.LIFETIME, which says the same thing twice.

   Failure scenario: A reader expects _end to check or record the Evidence, but it is just a boolean override of _cause(). A plain 'trusted lifetime cause' flag, or letting _cause accept retained proof, would be the simpler form.

7. `src/heterodyne/sandbox/runtime.py:597`: A placeholder LaunchSpec (empty label, resume=True) is built only so _claim_branch(settle_wip=False) can run as a read-only query of who holds the branch.

   Failure scenario: Any future change to _claim_branch or LaunchSpec (a new required field, or logic that reads spec.label/resume/generation) silently changes or breaks reconcile. A _branch_holders(key, bead, repo) query shared by both would remove the fake spec.

8. `src/heterodyne/sandbox/recovery_evidence.py:33`: read_bounded repeats _reaped_live's O_NOFOLLOW|O_NONBLOCK open, fstat regular-file check and bounded read, and MARKER_LIMIT=4096 duplicates runtime.MARKER_BYTES.

   Failure scenario: Changing the marker bound or the link/special-file policy in one place leaves _cause's marker reading and the strict reconcile reading out of step: one reports LIFETIME while the other holds on the same marker.

9. `src/heterodyne/sandbox/runtime.py:656`: The ENDED branch calls _running() twice (645 and 656), and each call lists the ledger, tmux sessions and backend. The method re-reads session.json up to six times, and _claim_branch scans every session record on each call.

   Failure scenario: Each retry costs several extra tmux/openshell subprocesses and a full scan of all records, which adds up when Hermes retries a held operation.

I ran one full HIGH review of frozen head `3a90c0a` against baseline `7caddc2` in `${OPERATOR_HOME}/repos/heterodyne-exact-session-reconcile-20261103`. It covers the cumulative source and test diff, the touched callees, the three review documents and the approved daf3 owner design. This is exact-reconciliation round two, separate from the PR160/161 preflight history. I made no edits and did nothing live.

- **How the review ran:** the high recipe for the built-in /code-review (8 angles, dedup with no verify, at most 10 findings) came in the task prompt, and I ran all 8 angles myself in this context. I did not make a separate Skill tool call.
- **Model and effort:** the exposed model is `claude-opus-5-5` and the recipe was given at "high". I can't see the CLI, settings or environment effort configuration, and internal effort is not exposed.
- **Tests:** I ran the safe isolated command myself: 18 passed, 324 deselected, in 31.9s. I did not run the full suite, Ruff or Pyright, and I have no result for the repaired full suite.
- **Conventions:** no CLAUDE.md applies to the changed Python files (the user-level rules have no code-style rules for them), so that angle found nothing.

**Verdict: clearance for the bounded source, with no blockers at this head.** I found no path that ends a live or uncertain executor, writes before the owner and snapshot check, or invents death proof. All ten round-1 findings are addressed as the disposition describes:
- **Typed holds:** `OSError` and `LaunchFailed` now become `RuntimeUnavailable`. `json.loads` raised `ValueError` even for 4 KB of deep nesting, not `RecursionError`.
- **Guards:** the resolve-intent and sibling-branch guards run before any effect.
- **Retained pane:** a pane that still exists must match the retained one exactly, and continuation proceeds only when tmux itself reports the pane absent.
- **Readbacks:** LIVE and ENDED both re-read the owner and the record before returning.
- **One journal writer:** `_end` alone writes the STOPPING/WIP record.
- **Conservative holds kept, as you asked:** ends by another path without a receipt, clock rollback and schema changes still hold.

Wiring still belongs to Hermes: native admission/profile/account/model/ancestry preflight, the ending and send fences, paired enrollment, the root installer and live acceptance.

**Advisories (none block this source):**
- Finding 1 (runtime.py:649, no in-process serialization against `expire()`) should be a stated requirement when Hermes wires this.
- Findings 2–4 cover test gaps, a reaper liveness gap and receipt retention.
- Findings 5–9 are diagnostics and cleanup.

9 findings, most severe first:

1. `src/heterodyne/sandbox/runtime.py:649`: reconcile_session takes no runtime lock against Product's own expire()/stop()/sessions(). expire() calls _end(lifetime) on any RUNNING record once now >= deadline (runtime.py:870). The _held/_claim_branch guards (595-600) also run once at entry and are not re-checked before _end/_settle_wip touch Git.

   Failure scenario: wsd's periodic expire(ws) and a Hermes reconcile_session(original) run at the same time on the same deadline-reaped record. Both run _teardown, _land on the same private git directory, and the lifetime WIP commit on btq/<bead>. Or a resolve intent or sibling launch appears after the entry guard and the WIP commit lands over it. Or expire wins first and reconcile holds for good ('changed without a receipt'). Hermes wiring must serialize this against wsd's own lifetime pass, not just against other Hermes calls.

2. `tests/test_sandbox_runtime.py:3124`: The 'identity' guard only tests a record generation mismatch. The new check that the retained pane matches the receipt (runtime.py:636-640) has no test, and neither do a tampered or oversized receipt, a STOPPING/ENDED record with a non-lifetime cause (631-633), or an unlanded landing failure at the ENDED gate (655).

   Failure scenario: A regression that ends a session whose tmux pane was replaced by a different dead pane, accepts a receipt with a changed 'original' or marker hash, or reports ENDED with unlanded=True would still pass all 18 reconcile tests.

3. `src/heterodyne/sandbox/runtime.py:645`: On a retry where the record is already ENDED, the reconcile never kills the generation's retained dead reaper tmux session (remain-on-exit is on globally). _end kills the reaper only when creates.gone() and close_notice() both succeed, and suppresses tmux errors.

   Failure scenario: close_notice() returns False (an OSError writing the alert) or tmux.kill fails during the first _end. The record is ENDED, but wsd-<sid>-r<gen> stays as a dead pane, so _running() is non-empty and the gate at 656 holds. Every retry then holds at 645 until some other Product path (_restore_creates/_cleanup_create via sessions or launch) sweeps the watcher.

4. `src/heterodyne/sandbox/recovery_evidence.py:92`: Each generation's receipt keeps up to 8 KB of the executor's pane tail (raw agent terminal output) and is never pruned. _drop_markers only globs 'reaped-r*', so deadline-recovery-r<g>.json files pile up in the session directory.

   Failure scenario: An agent printed a token or other sensitive output in its last 40 lines before its deadline. That text now lives forever in host state beside session.json and survives later generations. It is 0600 and outside the sandbox binds, so this is retention and privacy, not exposure.

5. `src/heterodyne/sandbox/runtime.py:570`: The wrapper reduces every OSError and LaunchFailed to 'held: <TypeName>', so a sibling-branch hold (LaunchFailed(BRANCH_TAKEN)) looks the same as any I/O hold.

   Failure scenario: Hermes can't tell 'another session of this bead holds btq/<bead>', which needs an operator or sibling to finish, from a passing ENOSPC/EIO that a retry would clear. It may retry forever or escalate the wrong thing.

6. `src/heterodyne/sandbox/runtime.py:1390`: deadline_evidence is only checked for None, so its contents are never used, and callers also pass cause=EndCause.LIFETIME, which says the same thing twice.

   Failure scenario: A reader expects _end to check or record the Evidence, but it is just a boolean override of _cause(). A plain 'trusted lifetime cause' flag, or letting _cause accept retained proof, would be the simpler form.

7. `src/heterodyne/sandbox/runtime.py:597`: A placeholder LaunchSpec (empty label, resume=True) is built only so _claim_branch(settle_wip=False) can run as a read-only query of who holds the branch.

   Failure scenario: Any future change to _claim_branch or LaunchSpec (a new required field, or logic that reads spec.label/resume/generation) silently changes or breaks reconcile. A _branch_holders(key, bead, repo) query shared by both would remove the fake spec.

8. `src/heterodyne/sandbox/recovery_evidence.py:33`: read_bounded repeats _reaped_live's O_NOFOLLOW|O_NONBLOCK open, fstat regular-file check and bounded read, and MARKER_LIMIT=4096 duplicates runtime.MARKER_BYTES.

   Failure scenario: Changing the marker bound or the link/special-file policy in one place leaves _cause's marker reading and the strict reconcile reading out of step: one reports LIFETIME while the other holds on the same marker.

9. `src/heterodyne/sandbox/runtime.py:656`: The ENDED branch calls _running() twice (645 and 656), and each call lists the ledger, tmux sessions and backend. The method re-reads session.json up to six times, and _claim_branch scans every session record on each call.

   Failure scenario: Each retry costs several extra tmux/openshell subprocesses and a full scan of all records, which adds up when Hermes retries a held operation.
