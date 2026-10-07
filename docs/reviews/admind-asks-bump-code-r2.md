1. [NON-BLOCKING] The ordinary-command clock boundary lacks a committed regression test. [tests/test_admind_asks_bump.py:1035](tests/test_admind_asks_bump.py:1035) tests `!asks` without a reply target. **Fix:** add fake-clock assertions that `!asks` and `!ps` replies to original, details and repeat cards leave activity unchanged, while parse errors advance it.

R1’s refusal paths are fixed. The judgement call is sound, and the new touches exclude reminders, ingress drops and authorisation denials. No regression found.

Validation: 14 fixture-free tests, 33 in-memory refusal cases, 42 exclusion cases and three rollback checks passed. The full suite remains blocked by the read-only sandbox’s temporary-file restriction.

APPROVE