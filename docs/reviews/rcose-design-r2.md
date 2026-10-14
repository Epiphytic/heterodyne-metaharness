Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 3ce07d77170aece9e42e8920c22ad3ce7df12a5e (range fe98f19132cc535020d3a0d9dab136c562b1689c..3ce07d77170aece9e42e8920c22ad3ce7df12a5e). Verdict: APPROVE.

Note: the two non-blocking findings (a trust-migration regression test, and passing the event explicitly in existing lifecycle tests) are carried into implementation.

- **[NON-BLOCKING] Design §1, “Codex trust,” and test 13:** Adding event arguments changes command hashes. Normal launches already regenerate `hooks.json` and `config.toml`, then seed and verify trust before starting the app-server (`runtime.py:595,647,672`; `codex.py:137–150`). This covers existing installs through the current launch path, but command assertions alone do not verify it. Add an offline regression starting with old hook commands/trust and confirming the regenerated commands are trusted before launch proceeds.

- **[NON-BLOCKING] Design §Tests, final paragraph:** Existing lifecycle tests cannot all remain unchanged. For example, `test_session_shim.py:267` calls `run_hook` with a Stop payload and no event argument. The proposed default makes that a PreToolUse/Stop mismatch, so it denies instead of recording Stop. Update lifecycle callers and standalone invocations to pass their event explicitly; preserve separate tests for the conservative missing-argument fallback.

The r1 blocker is resolved: saved Write controls create markers, early-close deny cases report `test deny` without creating markers, and the timeout control runs the tool. The trusted event argument preserves tool denial for input failures; its trust matches the hook configuration’s existing boundary. The 14 planned tests sufficiently cover the input hardening, with the trust regression above strengthening migration coverage.

Verdict: APPROVE