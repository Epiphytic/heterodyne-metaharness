**Verdict: REVISE (minor).** I found no correctness defects in the runtime switch. The issues are in the operator docs and in how strong the tests are. This is a read-only review: I made no edits, ran no tests, and posted no comments. I checked the forked review's output against the source and the approved design (aee72a40). Earlier lifecycle/fallback findings that are still open stay open.

### What matches the design
- **Single owner:** `fulfillment.ENABLED` is deleted. All three call sites read only `self.s.fulfillment_enabled` (`daemon.py:982`, `:1079`, `:2852`), with no OR/AND against a global.
- **Strict default False:** `isinstance(..., bool)` rejects `0`, `1`, `"true"` and `None`. The key is omitted, so it defaults to False. The field is appended last in the frozen `AdmindSettings`, so existing positional order is unchanged.
- **Host-only boundary:** this reuses the existing `ADMIND_KEYS` and the workstream ban. There is no environment key and no parallel config loader.
- **Socket checks unchanged:** the capability check still runs before the UID check, then the latch and the work lock. Only the source of the flag changed.
- **Migration-required refusal:** when enabled on an unmigrated store, question and merge asks are refused. That is design-mandated (§38), and `post_approval` does not call `require_projection`. I **rejected** the forked review's top finding, which treated this as a defect and wrongly said approvals were affected too. A small doc gap remains (finding 2).
- **Original test migration:** the original test edits stay narrow. Fresh `Unit(..., fulfillment_enabled=X)` construction replaces the global monkeypatch, and the one changed assertion (`f.ENABLED` → `unit.settings.fulfillment_enabled`) is the expected replacement.

### Findings
1. **Medium (docs): `docs/configuration.md:118-141` does not cover the new key.** That file is the canonical `[admind]` key table, and it already has a rollback warning for `approve_bead` saying older admind exits 78 on unknown keys. The new key has the same rollback trap that the design (§46) requires to be documented, but it is only described in `admind-fulfillment.md`. An operator who reads the canonical table will miss it.
2. **Low (docs): `admind-fulfillment.md:107` does not warn that enabling on an unmigrated store refuses all question and merge asks.** The line only says migration is "independent of this switch". It should add that a quiesced migration must come first, or ordinary posting stops after the restart. The behaviour is correct by design; the doc just doesn't say it.
3. **Low (test): the environment-override check at `test_admind_fulfillment_runtime_settings.py:46` proves nothing.** `match="HETERODYNE"` passes for any unknown `HETERODYNE_*` variable. It should match the specific rejection message.
4. **Low (test): the disabled half of the parametrized test at `test_admind_fulfillment_runtime_settings.py:150` compares `None == None`.** No receipt is written when the switch is off. Retention after reopening is only really tested in the enabled run.
5. **Nit:** `monkeypatch` is now an unused parameter in some migrated tests, for example `test_admind_fulfillment.py:407`. This is not true of all nine places the forked review listed: `activation_r1`'s verifier test still uses it.
6. **Nit:** `settings.py:90` checks the value inline. A `_bool(admind, key)` helper next to `_int`/`_text` would match the rest of the file, and the keyword argument order differs from the field order.
7. **Nit:** two-`Unit` tests build both stores before the `try`, so if the second construction fails, the first store stays open.

**Dropped as not defects:**
- An invalid value breaking shared readers such as wsd: an invalid value for any existing `[admind]` key already fails `resolve` the same way, and the design lists those readers explicitly.
- The verifier test "premise" finding: verification is deliberately not gated by this setting, and the worker never receives settings.

Clearing findings 1–4 would move this to APPROVE. Approval would cover source only: the design still needs a separate deployment ask before anything is activated.

`★ Insight ─────────────────────────────────────`
- Turning a module constant into an instance setting means code that couldn't run before can now run. The `post_ask` migration-required branch was dead code until now, which is why the forked review flagged it first. Here the design had already made that call.
- Strict config schemas (closed key sets that exit 78 on unknown keys) make rollback a matter of config ordering. Every new key needs "remove before downgrading" written where operators actually look.
`─────────────────────────────────────────────────`
