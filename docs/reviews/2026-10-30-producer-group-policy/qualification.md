# Producer group policy repair after the third review

The actual third review of PR149 cleared PoC source and retained the group-width mismatch as a pre-enrollment finding. The current Marmot group uses 32 hexadecimal characters. The producer previously required exactly 64 while the existing authority binder accepts 32–128. Share the binder's existing lower-case hexadecimal regular expression, retaining exact 64-character SHA256 validation independently. Actual isolated child tests now prove all three widths 32/64/128 produce output that passes exact correlation; malformed widths, uppercase and nonhex values refuse before publication.

Also add O_NOCTTY to root's no-follow/nonblocking file opens, as requested in the same report. This prevents acquiring a controlling terminal; it is not a claim to remove every possible device-open side effect. Regular-file, ownership and inode custody checks remain, and protected production owning-factory enrollment stays HOLD.

140 producer/publication/card/cessation/root-helper/authority tests passed in 15.04s. Ruff and strict Pyright passed. No live configuration, queue, service or deployment changed. The review is an authorized repair follow-up using MEDIUM and the operator's exact PoC question; prior PR149 reports and review budget are preserved, not reset.

The first actual MEDIUM follow-up found two missed O_NOCTTY open sites despite PoC-level clearance. Its conditional findings are preserved in full; both the root-helper constant and the cgroup.events reader now include the flag. A second authorized MEDIUM repair review addresses that concrete incomplete repair. No prior verdict is rewritten.

The first repaired-source test run caught an extracted-method fixture missing the newly reused _READ_FILE constant: 1 failed, 100 passed. Its complete failure report is retained in repair-tests.log. The fixture now carries the actual source constant into its isolated observer. Final qualification follows after rerun; no passing test result is inferred from lint.

Final repaired-source suite: 101 passed in 14.60s. Prior Ruff and strict Pyright passed; commit hooks cover the adjusted extraction fixture.
