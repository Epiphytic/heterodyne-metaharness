# AU-2 config design review r2 (cycle 2 of 3)

Reviewer: gpt-6.1-sol (Codex, configured effort). Author: claude-opus-5-5. Reviewed commit: f3456e8.

R1 findings:
- **1 resolved:** line 181 hides `login_dir`.
- **2 resolved:** lines 120–124 sanitize canonicalization and alias errors.
- **3 resolved:** rule 18a requires a usage table.
- **4 resolved:** rule 13 rejects explicit `default`.
- **5 resolved:** lines 107 and 174 fix default locations and specify HOME expansion.
- **6 resolved:** lines 110 and 255 define the versioned key and shared computation.

1. [NON-BLOCKING] `docs/superpowers/specs/2026-10-08-au2-config-design.md:231` requires the login marker to be absent from the exception’s `__context__` rendering, but line 122 prescribes `raise ConfigError(...) from None`. Python suppresses the context in ordinary tracebacks but retains the original exception in `__context__`; the prescribed implementation therefore fails this acceptance test. **Fix:** test the sanitized message and formatted traceback, including CLI stdout/stderr. If removing paths from exception objects is intended, specify raising the sanitized error outside the exception handler.

REVISE