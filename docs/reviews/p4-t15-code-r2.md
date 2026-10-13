# Plan 4 Task 15 code review, cycle 2

Reviewer: gpt-6.1-sol. Reviewed commit: 4b6ba5e9946e7ed00ef0c6cb825a57ad7debf6f0. Verdict: REVISE.

Note: the findings were fixed in daad0a8.

1. **[BLOCKING] Dead-socket control always fails on a correctly enforcing sandbox.** `tests/live/test_live_sandbox.py:734` expects exactly `hook-event-accepted`. Closing `s.sock` also fails `control-op-rejected`: neither request receives its required reply (`resources/probes.py:253`, `:288`). `_results()` therefore reports `control-op-rejected, hook-event-accepted`, which the exact matcher rejects. **Fix:** require that exact two-check reason and add an offline case exercising the combined failure.

2. **[BLOCKING] `shown()` still exposes secrets and 64-hex values.** `tests/probe_verdicts.py:27–32` permits arbitrary text up to 100 characters, including `token=<64 hex characters>`. A malformed operation line containing that text reaches assertion output through the verdict’s reason. The pattern restricts characters, not vocabulary. **Fix:** render only validated operation names, numeric PIDs and enumerated answers; replace unknown text with a fixed diagnostic. Add rejection cases for short secrets and 64-hex strings.

3. **[BLOCKING] Raw diagnostic paths bypass sanitization.** `tests/live/test_live_sandbox.py:852–854` prints unvalidated channel reasons and environment names on failure; `:1049` prints rejection reasons unconditionally. `ProcVerifier` can include the CLI’s absolute path in a rejection (`channel.py:278`) and arbitrary unexpected environment names (`:275`). Additionally, `plain_probes()` at `:767` leaves `OSError` uncaught, allowing an executable’s home path into the traceback. **Fix:** map reasons to fixed diagnostic categories, display only validated tool-variable names, and convert subprocess exceptions to sanitized failures with suppressed exception chaining.

REVISE