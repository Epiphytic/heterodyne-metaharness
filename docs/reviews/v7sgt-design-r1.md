Reviewer: gpt-6.1-sol. Reviewed commit: 66ef0b571ef831854df7a61b64be943ecc7792b5. Verdict: REVISE.

Note: cycle 1 of 3 of the cross-model design review (author claude-opus-5-5). Scope cut by the lead before r2: findings 4 and 7 are removed (no installer-managed lingering; `--user-only` becomes a follow-up).

Reviewed the design at `66ef0b5`, the approved host design at `bebf550`, the affected code, and ADR r16 at `93d0e90b`. I found seven correctness or safety gaps that need revision.

1. **[BLOCKING] [design.md:84](docs/superpowers/specs/2026-10-14-platform-install-design.md:84) — The daemon environment is not the checked environment.**  
   `env -i` cleans the renderer’s environment, but systemd user services inherit the user manager’s environment. Omitting `BTQ_REPO`, `HETERODYNE_CONFIG_DIR`, and the XDG overrides from the render does not remove inherited values. Thus wsd can use the wrong beads repository, or either daemon can load config/state different from what preflight checked—including a different fallback setting. Explicitly unset prohibited variables in the units and establish the same location environment for checks and execution. Test a contaminated manager environment without changing or restarting the manager.

2. **[BLOCKING] [design.md:49](docs/superpowers/specs/2026-10-14-platform-install-design.md:49) — The proposed offline preflight does not detect missing packages, and dry-run writes to the cache.**  
   With uv **0.11.16**, the exact proposed metadata-only probe against an empty cache exited **0**, reporting “Would download 2 packages”. An actual offline sync of the full archived tree failed on missing `msgspec`. The dry-run probe also created cache metadata. Resolve this now: use a full-tree scratch build for actual cache validation, isolate all dry-run cache writes in disposable scratch storage, and define dry-run’s fallback without silently performing a real build against persistent caches. A reusable completed release should not require rebuilding dependencies merely to pass preflight.

3. **[BLOCKING] [design.md:147](docs/superpowers/specs/2026-10-14-platform-install-design.md:147) — Recovery information is persisted too late.**  
   A failure after enabling linger loses ownership of that change. A failure after replacing an adopted unit loses its backup association. On rerun, linger already says `yes` and the unit already has the marker, so those facts cannot be reconstructed using the specified rules. Persist recovery intent before each corresponding change, atomically update its completion, and retain prior backup associations and linger ownership across upgrades. Add failure-and-rerun tests at these boundaries.

4. **[BLOCKING] [design.md:21](docs/superpowers/specs/2026-10-14-platform-install-design.md:21) — Enabling linger can start platform services before the safety checks finish.**  
   Starting a previously absent user manager activates its enabled units. Existing enabled admind/wsd units can therefore start at step 5, before release/config checks and verity, despite D4’s ordering guarantee. Also, unconditional bus preflight cannot pass on precisely the fresh-host case linger is supposed to support. Define separate cold-manager handling: permit deferred bus validation only in that case, and prevent existing enabled platform units from starting before their checks succeed, without restarting the manager. Cover implicit startup in tests, not just explicit `systemctl start` calls.

5. **[BLOCKING] [design.md:114](docs/superpowers/specs/2026-10-14-platform-install-design.md:114) — Interpreter identity alone cannot reconcile the running configuration.**  
   Reinstalling the same commit after a login PATH change writes a new unit and reloads systemd, but leaves the old process environment running because its interpreter still matches. A drop-in running another module with the same interpreter likewise passes the identity check; admind’s “still running” check does not catch it. Check the expected daemon command and relevant effective settings, and retain enough running-generation information to detect stale environments after crashes. Verify identity again after startup/readiness.

6. **[BLOCKING] [design.md:167](docs/superpowers/specs/2026-10-14-platform-install-design.md:167) — Uninstall protects unmanaged files only after disrupting their services.**  
   The specified order stops and disables both names, then removes only marked files. A hand-made unit that was never adopted would still be stopped and disabled. Determine management ownership before every stop, disable, removal, or restoration. Refuse ambiguous state without disrupting it, and retain fail-stop behavior before the existing root teardown.

7. **[BLOCKING] [design.md:32](docs/superpowers/specs/2026-10-14-platform-install-design.md:32) — `--user-only` cannot use the existing execution helpers.**  
   D1 says platform commands use the existing `as_service_user`, which invokes `runuser`; `login_path` also invokes `runuser`. Both require root. I confirmed the unprivileged call refuses. Specify an unprivileged branch that validates the current uid is the intended service user, executes directly with a cleared environment, obtains that user’s login PATH without `runuser`, and handles staging checks appropriately. Tests must exercise this distinction rather than shim away `runuser`’s restriction.

**[NON-BLOCKING] [design.md:44](docs/superpowers/specs/2026-10-14-platform-install-design.md:44) — Narrow the dependency-authentication claim.**  
The project’s editable build requires `hatchling`, which is absent from `uv.lock`. `--locked` therefore does not make every build dependency a lock-hash-verified package. Pin the build toolchain separately or state this limitation. It remains service-user execution, so this does not expand root authority.

The proposed tests are appropriately offline and unprivileged, but the shims alone cannot establish the uv behavior, manager-environment inheritance, or implicit startup guarantees. Add the real offline uv probe and the recovery cases above. Ensure `admind initialised` bypasses the existing writable `_load()` path: that path opens `Store` and `Audit`.

Existing deployment tests passed: **102 passed**, using the requested Python interpreter with `PYTHONPATH` pointing at this worktree. No repository edits, live queue commands, prohibited tests, or pushes were performed.

My views on the operator decisions:

- **[NON-BLOCKING] [design.md:231](docs/superpowers/specs/2026-10-14-platform-install-design.md:231):** Start wsd after these fixes. The current bubblewrap gate holds workstreams and claims nothing; `wsctl status` demonstrates availability, not launch capability.
- **[NON-BLOCKING] [design.md:232](docs/superpowers/specs/2026-10-14-platform-install-design.md:232):** Choose release directories. They isolate daemon restarts from checkout edits. Service-user writability remains an explicitly accepted limitation.
- **[NON-BLOCKING] [design.md:233](docs/superpowers/specs/2026-10-14-platform-install-design.md:233):** Allow user-only upgrades once their execution branch works. They add no privilege beyond the service user’s existing control of the platform code.

Apart from the environment mismatch, I found no proposed weakening of the pinned root bootstrap, helper digest verification, admind latch, approval digest binding, or existing redaction checks.

Verdict: REVISE