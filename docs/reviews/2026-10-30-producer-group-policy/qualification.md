# Producer group policy repair after the third review

The actual third review of PR149 cleared PoC source and retained the group-width mismatch as a pre-enrollment finding. The current Marmot group uses 32 hexadecimal characters. The producer previously required exactly 64 while the existing authority binder accepts 32–128. Share the binder's existing lower-case hexadecimal regular expression, retaining exact64 SHA256 validation independently. Actual isolated child tests now prove all three widths32/64/128 produce output that passes exact correlation; malformed widths, uppercase and nonhex values refuse before publication.

Also add O_NOCTTY to root's no-follow/nonblocking file opens, as requested in the same report. This prevents acquiring a controlling terminal; it is not a claim to remove every possible device-open side effect. Regular-file, ownership and inode custody checks remain, and protected production owning-factory enrollment stays HOLD.

140 producer/publication/card/cessation/root-helper/authority tests passed in15.04s. Ruff and strict Pyright passed. No live configuration, queue, service or deployment changed. The review is an authorized repair follow-up using MEDIUM and the operator's exact PoC question; prior PR149 reports and review budget are preserved, not reset.
