Reviewer: gpt-6.1-sol. Reviewed range: 3d45f06..7e1fca3. Verdict: REVISE.

1. [BLOCKING] `tests/test_admind_settings.py:328` — At `7e1fca3`, refusal coverage does not independently exercise the summarizer: both profiles use Claude, so the agent check raises first. No committed test combines inherited `CODEX_HOME` with a named Codex account. Fix: add cross-adapter cases with Codex as each process in turn, asserting the path-free `ConfigError`. The additional test seen in the working tree is outside the requested range.

The production fix resolves r1. The CLI exit-78 test is non-vacuous, and accepting `CODEX_HOME` for a Claude-only admind conforms to §3.2. No new production problems found. Test execution was blocked by the read-only filesystem, including `/tmp`.

REVISE