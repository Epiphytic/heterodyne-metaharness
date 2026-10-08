# Codex code review r1: wsd journal cleanup errors (btq-ekktm)

Reviewed commit 2fbb43e3bc62ca408c24273276f7008480a99dc2 against origin/main. Reviewer: gpt-6.1-sol, reasoning high.

1. [NON-BLOCKING] `tests/test_wsd_journal.py:799` — Cleanup-interrupt coverage tests only `KeyboardInterrupt`. A mutant that special-cases it while swallowing cleanup `SystemExit` or `GeneratorExit` would pass. Parameterize these three exceptions and assert cleanup identity, `__context__ is primary`, poison cause, and `_depth == 0`. The current implementation handles all three correctly.

No blocking findings: the code matches the approved spec, preserves exception chains and notes, checks poison under the lock, and keeps `close()` serialized. Caller inspection found no in-scope regression.

Six existing tests passed using in-memory journals, including the queued-worker test; 16 additional exception combinations passed. Full pytest could not start because the sandbox denies temporary-file creation, including under `/tmp`.

APPROVE