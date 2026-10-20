**Verdict: APPROVE (source only).** The full 15-file cumulative candidate has no correctness defects. R1 findings 1–4 are fixed. Nits 5–7 are still OPEN and don't block approval. I found one new low-severity doc point (finding 4 below). Earlier lifecycle/fallback findings that are still OPEN stay OPEN. This approval does not cover deployment or activation.

**One process problem you should know about:** you asked for no tests to be run. The forked `code-review` skill ran some anyway: it reports 236 tests passing, plus ruff and pyright clean. I didn't run anything after that, and I made no edits, posted no comments and changed no service, database or queue. Treat its test count as the skill's own claim, not as evidence for this review.

### What I checked
- **Frozen inputs:** the SHA256 of all 15 files matches your manifest. The working tree has exactly those 14 modified files plus the new test module.
- **One owner for the setting:** `fulfillment.ENABLED` is deleted. All three call sites (`daemon.py:982`, `:1079`, `:2852`) read only `self.s.fulfillment_enabled`. There is no OR/AND with a global anywhere.
- **Strict default False, frozen per instance:** only a real boolean is accepted. The key is omitted from `defaults.toml`, and the new tests assert that. The field is appended last in the frozen `AdmindSettings`.
- **Host-only boundary:** it reuses the existing closed `ADMIND_KEYS` and the workstream ban. The environment test now asserts the exact forbidden key and the full allowed-variable message.
- **Socket checks:** the order is still capability, then UID, then latch and work lock. The socket test applies successfully while latched and under `work_lock` when enabled, and returns `unavailable` when disabled.
- **Rollback docs:** both docs now say to remove the key before downgrading, keep the newest database and history, resolve unknown outcomes while enabled, and that enabling an unmigrated store refuses question and merge asks but not approvals.
- **Original test migrations:** the 8 original test files change by +22/−37 lines. They now construct `Unit(..., fulfillment_enabled=X)` instead of patching the global. Nothing is blanket-migrated and there is no global autouse True.

### Findings
1. **Nit, OPEN from R1 (5):** `monkeypatch` is now an unused parameter in some migrated tests, e.g. `tests/test_admind_fulfillment.py:407`.
2. **Nit, OPEN from R1 (6):** `src/heterodyne/admind/settings.py:90` checks the value inline instead of through a `_bool` helper like `_int`/`_text`. Its keyword argument is also out of field order.
3. **Nit, OPEN from R1 (7):** tests that build two `Unit`s create both stores before the `try`, so the first can leak if the second construction fails.
4. **Low (docs, new):** the running daemon re-reads the whole `[admind]` table every time a message comes in, to refresh operator authority (`cli.py:517` → `refresh_authority`).
   - The capability flag itself is never re-read, so the design's "no per-request lookup" rule holds.
   - The problem is ordering: if someone writes the key while an *older* daemon process is still running, that process rejects the unknown key and denies every operator until it restarts. It fails closed, so it isn't a safety bug.
   - Design §46 already counts a running old consumer as blocking the write. The docs' "Upgrade every shared-config reader" would be clearer if it said "upgraded and restarted on key-aware code".
   - This doesn't block approval.

### Forked-review findings I rejected
- **Migration-required refusal (daemon.py:1079):** the design requires it, approvals are unaffected, and both docs now warn about it.
- **Get/Inspect blocked while disabled:** design §32 and §46 say explicitly that all lifecycle operations, including Get/Inspect, are disabled.
- **`fulfillment_enabled` missing from `defaults.toml`:** the design says the packaged defaults MUST omit the key.
- **Projection validation on every post:** this code path was already there and the design keeps it. This change only alters where the flag comes from, so it isn't a defect of this change.
- **No `fulfillment_enabled` in the start audit record:** the design doesn't require it. It would be a nice-to-have.
- **Verifier test lost its "producers disabled" setup:** `p.worker(store)` never receives settings, so it is disabled by construction. R1 dropped this one too.

`★ Insight ─────────────────────────────────────`
- The flag is read once and frozen, but the config file is re-read on every message for other fields. So even a key that is read once can affect a running process indirectly, through strict unknown-key checking.
- Most of the forked review's new findings described behaviour the design chose on purpose. Checking each one against the approved design text is what separates real defects from design choices.
`─────────────────────────────────────────────────`
