**Verdict: REVISE.** I ran one full high-effort review with the built-in `code-review` skill. Nothing was edited, tested, written to scratch, or sent. I checked that the `proposal.md` hash (`7d2df775…e4fd`) and all 23 packet files match `freeze.json`.

The core approach holds up: coverage first and enrollment last, the original exclusive locks kept from creation onward, and no adoption of existing state. It doesn't call for a fake lease and doesn't weaken generic readiness. But as written it can't be implemented safely.

**Findings**

1. **HIGH: once activated, nothing can change.** Generic maintenance refuses any enrollment with `initial_controller_scope` (`source-Coordinator-maintenance.py:20-21`). Rollback is forbidden, and the proposal leaves out a later transition owner. After activation, any source fix, a new BTQ lane actor, or a new controller run UUID puts everything on HOLD permanently. The design should define at least a minimal same-domain update under EX, or the exact approval path for one, before activation is allowed.

2. **HIGH: the first bootstrap step has no owner.** The owner, the amended coordinator with the interlock, and the authority reader all have to be installed by a "preceding separately approved" deployment. The only existing publisher, `install-agents.py`, needs an EX maintenance lease, which needs an existing enrollment. The single-use 98-target publisher can't be rerun. The proposal needs to name that pre-enrollment publication step and say whether it takes one approval or two.

3. **HIGH: the authority input doesn't match the real reader.** `enroll_initial(plan_path, deployment_ask_id)` takes a Marmot ask ID. The native `authorize()` needs an approval Bead with `via=marmot` and a plan whose bytes are committed to a pinned git revision. Neither the Bead nor the commit exists before enrollment, and the proposal doesn't create them. Separately, a malformed ask record makes `authorize()` crash with a `KeyError` (a missing-field error, not a clean refusal), and that happens after the intent is already written.

4. **HIGH: the completion interlock has no defined home, and step 7 contradicts it.** If the interlock check goes in `_enrollment()`, step 7's production validation fails because completion isn't written yet, so activation can never finish. If it goes only at the three admission points the proposal names, other enrollment readers accept a published pair with no completion. The record's path and mode aren't specified either. The simpler fix: validate the in-memory pair through `snapshot=` before publishing, then reconsider whether a separate completion record is needed at all.

5. **MEDIUM-HIGH: a crash during creation leaves the domain stuck for good.** Directories, locks and files are created one at a time at their final paths. A crash after creating `turnstile.lock` but before its identity is saved can't be attributed, and deleting it or starting a fresh domain is forbidden. The fix is to build the whole tree in a staging directory named after the operation UUID, then move it into place with `renameat2(RENAME_NOREPLACE)`.

6. **MEDIUM: temp files from a crashed write can't be attributed.** `Coordinator._write` creates temp files with random `.new-<uuid4>` names. A crash leaves one behind that reconcile classifies as an "unexplained domain" and holds on forever. Temp names should come from the operation UUID and be listed in the intent.

7. **MEDIUM: reconcile doesn't check that the original run is dead.** Before the lock files exist, nothing stops `reconcile_initial` from running alongside a live `enroll_initial`. The existing `maintenance()` does refuse while the owner process is alive. Reconcile should require proof that the recorded owner is dead, and hold if that can't be shown.

8. **MEDIUM: expired authority strands a coverage-only state.** The published coverage is immutable and bound to the original authority. If that authority expires before reconcile, new authority can't be recorded against it. The proposal needs to say whether fresh authority may finish the original operation (and how that's recorded), or state that the domain is permanently stranded.

9. **MEDIUM, likely: step 7 may block on its own locks.** Step 7 calls production validation while holding EX on the original lock FDs. If any of those helpers opens the turnstile or gate itself and asks for a shared lock, it blocks against the owner's own EX and times out. That leaves the published-pair-without-completion state. The step-7 validators need to be lock-free or reuse the held FDs, and the exact entry points should be listed.

10. **MEDIUM, likely: v1/v2 inventory selection doesn't match the frozen code.** The producer always emits `unapplied_targets`, even with a count of 0, so it returns v2. The validator expects that key only when targets exist, and allows one Hermes part where the proposal says "each". That means HOLD with zero unapplied targets. With no obligations at all, it returns v2 instead of the strict v1 the proposal relies on. Candidate `f67953cc…` should be fixed or confirmed.

Findings 9 and 10 depend on code that isn't in the packet (`enrollment()`, the inventory adapters, `publish_exclusive`), so I marked them likely rather than confirmed. They're still deliverables to settle before deployment, not open unknowns.

`★ Insight ─────────────────────────────────────`
- `flock` locks belong to each open file, not each process. So code that holds EX on one FD blocks itself if a helper reopens the same lock file. That's why finding 9 needs step-7 validators to reuse the held FDs.
- Building in staging and then renaming into place is the standard way to make multi-file creation crash-safe. Inode identities survive the rename, so anything left behind before it can be traced by its operation-UUID name.
`─────────────────────────────────────────────────`
