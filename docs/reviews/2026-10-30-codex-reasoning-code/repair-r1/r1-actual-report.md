**Verdict for round one: changes requested.** The core change looks correct: a typed `reasoning_effort` for Codex only (low, medium or high), written into the synthetic server home before `backend.create`, overriding the installed default, with the argument allowlist still closed. But one claim the docs make has no evidence behind it, and two cheap fixes are worth making before this is approved.

I ran the built-in `code-review` skill at high effort on `67ce2df...df968b3`. The skill doesn't verify its findings at this level, so I re-read the code for findings 1–3 at `df968b3`, and they hold. The skill also found that the qualification receipt is consistent: its file hashes, source tree and log hashes all match `7d5387b`. Nothing was edited, committed or posted.

**Should block approval**

1. **The resume claim is untested** (`src/heterodyne/agents/codex.py:178`, `docs/configuration.md:64`). The docs say the setting "applies to new and resumed sessions." The resume argv is deliberately unchanged (`resume <id> --remote … --model`), so the effort reaches a resumed thread only through the server's `config.toml`. If Codex 0.160 restores the effort saved with the thread instead, a thread started at `high` stays at `high` after the profile moves to `medium`. The tests check the home file and the argv, never what effort a resumed thread actually runs at. **Fix:** add evidence that Codex 0.160 takes the effort from `config.toml` on resume, or narrow the doc to new sessions and say resume is unqualified.

**Should fix (cheap)**

2. **A misspelled key is silently ignored** (`src/heterodyne/config/layers.py:165`). `check_profiles` doesn't reject unknown profile keys, so `reasoning_efort = "medium"` passes and the installed default (`high`) stays in force without any error. That undermines the requirement for queues to be qualified at medium. The loose key checking predates this PR, but this feature makes it matter. **Fix:** reject unknown keys in `[profiles.*]` (`adapter`, `model`, `args`, `reasoning_effort`).
3. **The error message is misleading** (`src/heterodyne/config/profile_args.py:50`). On a non-Codex adapter, a valid `"medium"` fails with "must be low, medium or high for codex." **Fix:** give "not supported for adapter X" and "invalid value" separate messages.

**Not blocking: design and quality**

4. `src/heterodyne/agents/codex.py:172`: the adapter re-runs the config-layer check, using `ConfigError` and a placeholder `where="profile"`. That means the same check lives in two layers that could drift apart.
5. `src/heterodyne/config/profile_args.py:44`: `"codex"` and the three allowed values are hardcoded in the profile check, in `prepare_install_home` and in the docs. If more typed settings are planned, a per-adapter table next to `FLAGS` would scale better.
6. `src/heterodyne/agents/base.py:296`: adding `profile` to the shared `prepare_install_home` causes the disclosed quality failure (exit 2, parameter count up from 4 to 6 in three places). Only Codex uses it, and Claude Code ignores it. Passing only the effort Codex needs would be a smaller change.
7. `tests/test_sandbox_runtime.py:2500`: the new runtime test doesn't follow the file's conventions. It imports inside functions, uses single quotes, and assigns to `rig.backend.create` directly under a `type: ignore` instead of using `monkeypatch.setattr`.

**Scope as you disclosed it:** this is source-only. No real host sandbox was created, and nothing here shows the change adopted or deployed at runtime; the H facade integration is separate.
