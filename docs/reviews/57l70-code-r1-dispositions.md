# Scanner helper first full HIGH review dispositions

The actual built-in Claude Opus /code-review returned **REVISE** for
597a1d4aec3d4fc081e5774ff3ffc7aa33aa16be..2dd16a927dce178087547bc85aadb3788592bf1c.
Raw report is retained verbatim next to this file. No claim of reviewer approval.
This is helper construction only; no scanner gate, runtime, native approval,
service or deployment was performed. Original verifier/mask/probes/oracles and
approved contract/ADR byte copies remain unchanged.

1. Both test modules put the repository scripts directory on sys.path before
   importing scanner_recovery. No dependence on pytest console cwd import paths.
2. Runtime checking and scratch archival execute independently, and either HOLD
   wraps the earlier verdict. Leftover bus/socket and changed runtime mode cannot
   bypass archive attempt or discard the initial failure reason.
3. Replaced fwalk with no-follow descriptor traversal. Symlinks are lstat/readlink
   metadata only, never opened; directory listing/open failures propagate. Archive
   inventory digest exists only on completion; files and directories fsynced.
4. Before plan returns, before release and before creating a run directory, validate
   whole-source inventory and worst-case budgets, additive contract, construction
   rejection probes and exact375-node diagnostic hashes/order. Environment probes
   that require execution remain in the separately released actual attempt.
5. All later stage prerequisite lists include diagnostic and every earlier stage.
   Common task/B1/B2/H-delivery, driver/support/supplement/disposition/failure-map
   hashes and exact transitive predecessor references must match. A different
   diagnostic cannot silently be substituted partway through the sequence.
6. H92 requires clean source porcelain, exactly92 unique hostread-only node IDs,
   the fixed regression node and its exact manifest predecessor/successor.
7. Candidate discovery covers indirect bind/connect/Unix server/client calls
   throughout Python sources plus all shell/config lines, including docs scripts
   and root defaults. This remains a candidate enumeration, **not a completeness
   proof**. Actual whole-suite endpoint/config/external-command reachability and
   concrete bound derivations must be manually reviewed before root release. No
   actual inventory has been fabricated or marked complete by these unit tests.
8. Required result fields must exactly concatenate original51 and additional6;
   environment must exactly append XDG_RUNTIME_DIR to original13. Same-count
   substitutions refuse. Original lists must match original manifest.
9. Socket bound includes the separator before a nonempty case component, uses
   full16/18-byte conservative root/child limits, and requires suffix bytes include
   all remaining separators. Measured /tmp lstat owner/mode/device/inode are checked
   at preflight rather than hard-coded into evidence.
10. Added regression tests for the real main plan/refusal paths, ensuring no
    attempt directory, scratch construction or child invocation; diagnostic source
    identity, H92 dirty-source rejection, transitive stage membership, predecessor
    source/driver mismatches, runtime HOLD archival and nofollow archive failures.
    Eight initial regressions were RED against the original implementation;
    original11 tests retained (only imports corrected),14 additions now pass.

The lower-ranked input read/hash timing observation is also addressed for
inventory, disposition, receipt, reservation, supplement and diagnostic input:
parse and hash the same bytes retained in memory. Execution uses that inventory,
not a later second read after a child has run. Reservation freshness remains
independently rechecked before child boundaries.

All quality findings remain **UNWAIVED**. Initial repair delta against2dd16a9:
31 total,3 gating,0 acknowledged (source/inventory complexity and test-duplication
metrics). Existing helper complexity/new-symbol debt also remains unwaived;
no baseline, acknowledgement or test oracle weakening was used to hide it.
