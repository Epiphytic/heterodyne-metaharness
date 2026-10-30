# Typed Codex profile reasoning source qualification

The approved build-isolation composition needs a Codex manager and coding profile with medium reasoning. This implementation adds a finite typed `reasoning_effort` setting (`low`, `medium`, `high`) for Codex profiles. It seeds the private server configuration before sandbox creation, including resumed sessions. The generic argument allowlist stays empty; remote and resume argv carry no generic configuration overrides. Other adapters reject the setting. Omitting it retains the installed default.

The candidate starts at reviewed Product main `67ce2df8acdb00e6a698f90bccc450bc32137a63`. The earlier exploratory `2c0c5b5` checkout is its ancestor but lacks subsequent reviewed changes; no qualification from that checkout is represented here. The receipt pins the actual source commit, all nine changed file hashes and the current-worktree logs.

## Validation

- Focused adapter/config/content/refusal tests: **307 passed in 13.71s**.
- New runtime integration test: **1 passed, 271 deselected in 3.18s**.
- Complete sandbox runtime suite: **272 passed in 665.73s**.
- Ruff on all eight changed Python files: **exit 0**; commit Ruff, install-agnostic and hardcoded-secret hooks passed.
- Pyright on those eight files using the explicit existing virtualenv interpreter: **exit 0, 0 errors, 0 warnings**. The log also reports the worktree-local `.venv` directory is absent; package resolution uses the explicit `--pythonpath`.
- `git diff --check`: **exit 0**.

Quality measurement against the actual uncommitted parent diff exited **2**: the shared adapter method and two implementations gain a keyword-only profile parameter (reported count 4 to 6 including the keyword boundary), with small existing class LOC growth. The extension carries the already-validated typed profile to private-home preparation. No suppression, baseline reset or quality clearance is claimed.

These runtime tests use the existing test backend. They establish configuration and ordering behavior, not actual OpenShell isolation, account enrollment, service deployment, or Hermes activation. No actual sandbox, account or service was created. Claude review and deployment qualification remain separate.

Qualification commands and the Pyright environment note normalize installation-specific paths to `$WORKTREE` and `$TEST_ENV` to satisfy the install-agnostic hook. The receipt retains the unnormalized capture hashes separately.
