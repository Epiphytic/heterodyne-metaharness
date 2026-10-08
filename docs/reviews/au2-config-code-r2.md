Reviewer: gpt-6.1-sol. Reviewed range: 49ba1e4..15eec1a. Verdict: APPROVE.

R1 is resolved at `src/heterodyne/config/accounts.py:44`: `surrogateescape` preserves ordinary keys and handles undecodable POSIX path bytes. Direct regression checks passed.

No additional findings in `49ba1e4..15eec1a`. Validation, defaults, host-only restrictions, hidden login paths, alias detection, warnings, and `configured_dir` match the approved requirements.

The tests contain substantive offline checks. Pytest could not start because the read-only sandbox prevents temporary-file creation; a full suite pass remains unverified.

APPROVE
