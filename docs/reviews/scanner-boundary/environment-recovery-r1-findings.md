# Actual single HIGH design review

Verdict: REVISE. Preserved verbatim; incorporated once without another review.

I read both documents and ran the eight review angles. There's no diff here because only a design exists, so I treated proposed-design.md as the review scope and compared it against controlling-D4.md. I ran no commands or tests, edited nothing and spawned no subagents.

10 findings, most severe first:

1. `proposed-design.md:22`: CRITICAL: The design names two causes (socket paths too long and RuntimeDirectory not resolving) for 379 failures but never requires mapping each failure to a cause. B1 under the new 14-key environment therefore becomes the first full-suite test of the fix, and it consumes the only authorized attempt.

   Failure scenario: The 13-key empty environment in D4 lines 215-232 was first used in attempt a22759f7. The earlier 3372-node runs had only 5 failures (D4 line 44), so the new environment contract may have caused failures beyond the two named causes, for example missing USER, LOGNAME or TERM, or a fixture that assumes a writable path. Those failures would recur in the next B1. Fresh H92 (92 hostread tests) and the 88+6 red baselines don't run the sandbox, channel or unit-check modules, so nothing before B1 would reveal them, and the B1 rerun would HOLD again. Fix: before approval, require a table built from the retained JUnit that assigns all 375 unexpected node IDs to a cause, with an explicit 'unexplained' count. If that count is not 0, either authorize a bounded diagnostic run that doesn't count as a gate, on exactly those node IDs under the 14-key environment, or say plainly that B1 is expected to be at risk.

2. `proposed-design.md:43`: CRITICAL: The 60-byte suffix budget isn't derived from the socket paths in the failed run. It also ignores pytest's tmp_path nesting under TMPDIR, so the 40+60 byte budget can still exceed the 107-byte sun_path limit.

   Failure scenario: pytest's tmp_path base is $TMPDIR/pytest-of-<user>/pytest-<N>/<test name truncated to 30 chars>0/. That's about 60 bytes before any socket filename. A 40-byte TMPDIR plus that nesting plus a name like 'channel.sock' is over 107 bytes, so bind() fails with the same error as before and the one-shot B1 HOLDs. Line 44 admits this can happen but doesn't prevent it. Fix: (a) take the longest socket suffix seen in the retained logs, measured relative to TMPDIR, and require a stated margin over it; (b) pin pytest --basetemp to a short directory inside the private root, which needs D4 argv approval, or measure the nesting explicitly; (c) add a real AF_UNIX bind() at the maximum budgeted length to the synthetic validation at lines 97-99.

3. `proposed-design.md:61`: CRITICAL: The design says no verifier function changes and keeps the '51 required fields' (line 93), but it changes the environment from 13 keys to 14 and adds new evidence (path byte lengths, lstat identities, the private-root locator) without saying whether verify() or manifest.result_contract pins the 13-key set.

   Failure scenario: If the strict verifier or the result_contract in D4's manifest checks the environment keyset or the field list exactly, a correct 14-key run fails verification and HOLDs for a reason unrelated to the tests. Changing the manifest to avoid that would be an unreviewed edit to a D4 artifact. If neither pins them, the new preflight evidence has no required field and can quietly go unrecorded. Fix: state exactly which bytes of the manifest and contract change (old and new key set, any new fields), confirm that verify(), mask() and the 19 probe bytes are untouched, and add a negative probe that rejects a 13-key or 15-key environment.

4. `proposed-design.md:32`: HIGH: The design moves HOME, TMPDIR, the runtime directory and the Hypothesis directory to /tmp without checking /tmp's mount properties or how long it keeps files, so the promise to keep the directories (lines 34-35) can't be kept.

   Failure scenario: If /tmp is mounted noexec, fake tmux and other helper scripts created under TMPDIR or HOME fail with EACCES. Before, they ran from the evidence tree. If /tmp is a size-limited tmpfs, a 718-second run can hit ENOSPC. Both would be new unexpected failures. A reboot (D4 line 212 expects reboots) or systemd-tmpfiles-clean deletes the 'retained' private root, including the Hypothesis example database needed to reproduce a failure. Fix: in preflight, record and require the /tmp filesystem type, the noexec flag and free space. After each attempt, hash and copy the private root's contents into the durable evidence tree. Validate /tmp itself as root-owned with mode 01777 (sticky).

5. `proposed-design.md:74`: HIGH: The fresh H92 gate doesn't say which source tree is the active src (first PYTHONPATH entry) or which per-stage import hashes are expected. D4 lines 220-225 require both before collection.

   Failure scenario: The phrase 'from the exact delivered H test/product bytes' could mean a disposable copy of the delivered H commit or the scanner candidate with B1 applied. Those give different import origins and scanner file hashes. The executor either guesses, which leaves evidence that can't be verified against the contract, or the import-origin check HOLDs. Fix: name the source root (for example, a disposable checkout of the H merge commit), the expected hostread, scanner, redactor and creates hashes, and the collection file for this gate.

6. `proposed-design.md:82`: MEDIUM: Step 4 adds a 'create-green6' gate that D4 doesn't have, and it lists the B1, overlap and green gates by count only, without D4's per-gate oracles.

   Failure scenario: D4 step 4 (line 289) has only 'Same3373 nodes all green'. A separate create-green6 run is either redundant or an unreviewed new gate with no defined oracle. Separately, the counts 4/3369 and 4/3 could be implemented without the other D4 checks: legacy SHA ce942161… after removing the one new node, the node's position, the Appendix A 88, the 92 hostread and the 19 metadata cases passing, no skip/error/xfail, and exit 1. That would weaken the oracle. Line 81 only covers step 3. Fix: remove create-green6 or justify it with an oracle, and state that steps 3-4 of D4 (lines 276-289) apply word for word.

7. `proposed-design.md:5`: MEDIUM: The amendment doesn't pin the exact D4 revision it amends (commit or blob hash), and it doesn't give its own ADR path or revision binding. The D4 text provided is still marked 'UNREVIEWED d4 candidate' (D4 line 3).

   Failure scenario: Phrases like 'all other 13 keys retain D4 meaning' and 'additive amendment' can't be checked if D4 changed between versions. Approval might bind a different D4 than the one B1 actually ran under. D4's R=D pinning (lines 81-87) also needs a docs/adr path. Fix: pin the D4 revision and SHA that the failed attempt was admitted under, give this amendment's ADR file path, and state its R=D binding.

8. `proposed-design.md:67`: MEDIUM: The baseline mode is described as explicit, but if nobody answers it silently becomes 'fresh', and the design doesn't say when the rcje answer must exist relative to approval and step 1.

   Failure scenario: The approval is recorded and execution begins, then a 'reuse' answer to rcje arrives after the fresh baselines have already run, or the reverse. Two inconsistent dispositions now govern one sequence. Fix: require the rcje answer (or a recorded 'no answer, so fresh') to be authenticated and read back before the step 1 checks. Any later answer must not change a running sequence.

9. `proposed-design.md:72`: MEDIUM: The design doesn't say how long the broad slot is held, when it is released, or what happens if it's lost (D4 lines 259-265 define these). The final whole offline suite (line 86) runs modules outside the 3373 that have never run under the new environment.

   Failure scenario: One reservation spans H92, the baselines, B1, overlap, B2 and green. If the slot is lost partway, the executor doesn't know whether to resume or rerun. Separately, the final suite reaches modules outside B1's 81 that create sockets or call systemd-analyze. It is the first time those run under 14 keys and a /tmp root, so an environment-caused failure would HOLD after B2 has already been applied. Fix: restate D4's per-batch reservation, release and loss rules, and add the full suite's modules to the failure-cause analysis and path-budget check.

10. `proposed-design.md:53`: LOW: The design doesn't list other programs that read the runtime and socket directories (the tmux default socket directory, keyring and D-Bus clients), so the claim of 'no live user bus' rests on one omitted variable.

    Failure scenario: tmux ignores TMPDIR and XDG_RUNTIME_DIR and uses /tmp/tmux-<uid>/default when TMUX_TMPDIR is unset. Any fixture that runs real tmux without -S or -L would reach the live tmux server the workers share. Libraries that look for $XDG_RUNTIME_DIR/bus would now find a real, empty directory instead of nothing, which can change their fallback path. Fix: state that fixtures always pass an explicit -S path under the private root, or that a scan of the retained logs found no real tmux calls, and record that the private runtime directory is empty after the run.

**Verdict: REVISE.**

The general approach fits the problem: a fresh short private root, an XDG_RUNTIME_DIR built from that root rather than inherited, offline-only systemd-analyze, an explicit fresh H92, an explicit authenticated disposition, and no waived failures or oracles. It is not yet a bounded recovery because the next B1 is still the first real test of the fix, and of the 13-key environment contract the fix extends.

These four must be fixed before human approval:
- **Failure-cause table:** assign all 379 failures to causes and decide what happens with any unexplained ones (finding 1).
- **Socket budget:** derive it from the paths in the failed run and account for pytest tmp_path nesting (finding 2).
- **Verifier and manifest:** state exactly how they treat the 14-key environment and the new evidence fields (finding 3).
- **/tmp checks:** check its mount properties and copy the private root into the durable evidence tree (finding 4).

Findings 5-10 should also be incorporated, but they don't block approval.

This manual review is not a reserved-profile admission proof.
