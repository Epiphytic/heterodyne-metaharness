Reviewer: gpt-6.1-sol. Reviewed commit: ed6f722. Verdict: REVISE.

1. [BLOCKING] `docs/superpowers/specs/2026-10-08-tmux-exit0-design.md:102` — The specified parser splits the full marker line at most twice into `marker, sid, reported`, but the marker itself contains a space. Valid output `hz-started 0123abcd $0 admin` becomes `("hz-started", "0123abcd", "$0 admin")`; the nonce fails session-ID validation, so successful starts raise errors. **Fix:** remove the verified `f"hz-started {nonce} "` prefix first, then split the remainder once into `sid, reported`, preserving spaces in the session name. Specify handling for missing fields and test a literal marker line matching the probe output.

REVISE