# One HIGH design review: ordered author dispositions

Actual verdict REVISE; all ten incorporated once. Human approval remains required.
No second review, validation or reviewer clearance is claimed.

1. **HIGH — The guard that must change is missing from the owner list (ADR lines 71–83, 115–125).** `environment.py::archive_execution_admission` (now at :704, called at `runner.py:959`) always raises "Archive physical reservation unresolved". The ADR doesn't list it, and it doesn't say what replaces it or how the running code checks the amendment approval. If the guard is left alone, the approved PoC can never run. If it is deleted, the changed behavior runs with only a bead edge as its gate, and no in-run pin of the amendment digest and approval.

Author: Incorporated §2: archive_execution_admission remains the runtime owner; genuine primary plus amendment bindings and child/source/destination receipt are required. Missing native port remains HOLD; no approval manufactured.

2. **HIGH — The metadata recursion has no stopping rule (line 50, lines 29–30).** The combined inventory has to count its own bytes and entries, plus the generated per-root files and directories. A size crossing a digit boundary can change the total again. The current code just loops up to 16 times, and it writes per-root `inventory.json` before the combined check. The ADR needs a fixpoint and a termination rule, with non-convergence as HOLD. It also needs to say which side wins when a written per-root inventory fails the final ledger.

Author: Incorporated §4: bounded64-iteration monotone fixedpoint, no circular self-hash, exact entry list and no payload/per-root metadata write before stable whole plan. Nonconvergence HOLD.

3. **HIGH — Writes to the run directory aren't budgeted, and the destination can't be identified before launch (line 36).** `run_dir` gets `disposable-source`, child logs, `collected.json`, verifier logs and receipts, and scratch lives inside it. On a shared filesystem these can use up the admitted space while the child runs. Separately, the destination is only created at copy time, so "destination change invalidates preflight" can't be checked against an identity recorded beforehand.

Author: Incorporated §3: private destination precreated and fd/identity retained before child; run-only B/E headroom includes logs/source/receipts and is explicitly an estimate, not a quota or capacity guarantee.

4. **MEDIUM — The 2 GiB threshold is called "conservative" but isn't (line 40).** The cap counts serialized bytes, not disk blocks. A payload near the cap with 65,536 entries needs roughly 256 MiB more at 4 KiB per block, so admission can pass and the copy still hits ENOSPC every time. The threshold should be 2 GiB + entries × `f_frsize` (+ directory blocks). The outcome is safe because it HOLDs, but owners would be accepting a misleading label.

Author: Incorporated §§3–4: allocation-model threshold includes2*entries*Q and precise measured pre-copy threshold; filesystem overhead and concurrent allocation remain explicitly unguaranteed.

5. **MEDIUM — "Same filesystem" is never defined (line 40).** The ADR doesn't say whether that means `st_dev`, `f_fsid` or a mount ID. Btrfs subvolumes, bind mounts or overlay/tmpfs can share one free-space pool while showing different `st_dev`, so the same free space gets counted twice. Define the identity tuple, and treat any ambiguity as shared.

Author: Incorporated §3: recorded device/mount/fs facts; independent capacity needs proof; ambiguous relations grouped shared and minimum capacity must cover summed allowances, otherwise HOLD.

6. **MEDIUM — The inode rule contradicts itself (lines 39 vs 41–43).** One line records inodes "when reported", another makes unknown availability HOLD. Btrfs reports `f_files` = 0, so the rule either skips the check or HOLDs forever. The "scratch allowance" for inodes also has no number, so the shared/distinct inode tests can't be written.

Author: Incorporated §3: explicit E+1 inode allowance per role; nonpositive/unsupported capacity including f_files=0 HOLD. No filesystem-specific exemption.

7. **MEDIUM — The pre-copy recheck has no threshold (line 46).** It could mean the full 2 GiB again or the measured total. The ADR also doesn't say whether a failed recheck still attempts a partial copy that preserves evidence.

Author: Incorporated §4: pre-copy measured bytes+2*entries*Q and entries+1 inodes; failed recheck copies nothing, preserves originals. Partial evidence semantics apply only after copy begins.

8. **MEDIUM — The author evidence is private and out of date, and the references are opaque (line 72, lines 74–83).** It points to the handoff `inputs.json`, which pins `environment.py` `e6a24636…`, but the candidate has since moved on. The cited line ranges are stale too: for example, the preflight call is at runner.py:900, not 842, and `archive_roots` is at 634–698. The ADR also cites "YG HIGH finding 6", `btq-5jwip` and `btq-yg7zf` without saying what they are, which breaks the rule against pointing Liam to a bead or file for content.

Author: Incorporated §§1,6: task/approval purpose explained, source owners named without stale line/hash claims. Public exact report supplies rationale; eventual reviewed source is separately pinned at admission.

9. **LOW — "One shared counter" reads as charging the same bytes twice (line 49).** As written, both the preflight estimate and the actual streamed bytes would be charged. Say that only actual writes debit the counter, or that the reserve is released and converted.

Author: Incorporated §4: estimates never debit actual counters; actual writes/creates charged exactly once against stable plan.

10. **LOW — The gap statement is partly stale (line 18).** The candidate already has `metadata_reserve` and checks `metadata_bytes` and `total_bytes` against the cap. What's actually missing is entry counting for generated files and directories, the order of the per-root write, and the `archive_scratch` fallback.

Author: Incorporated §1 and §4: acknowledge existing metadata reserve/byte checks; identify remaining generated-entry/publication/fallback/runtime gaps without claiming stale implementation state.
