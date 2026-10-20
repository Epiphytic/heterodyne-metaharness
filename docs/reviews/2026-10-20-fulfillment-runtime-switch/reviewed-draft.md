# Default-off runtime control for legacy question fulfillment

Status: proposed narrow amendment; not implementation, activation or deployment authority. This draft requires exactly one actual Claude HIGH design review, one incorporation pass, publication and separate genuine human design approval. Existing review history is retained. The controller owns those steps.

## Problem and governing contract

Approved activation design `f8a80b8fad51c0be015a797e11f4a968f90c4bfb:docs/designs/2026-10-18-fulfilled-question-activation.md` §§1/6 requires source to land disabled and genuine separately pinned deployment consent before producers are enabled. Its migration, immutable receipts, original answers/status, native-approval exclusion, finite verification and conservative counting remain binding. This amendment changes only how the selected daemon enables its existing legacy producer protocol.

The enabled-source candidate based on `c66d030d9bd203580a31cff5d1e14792eaf74a9e` changed the global source constant to True. Actual first HIGH findings1/2 identify automatic enablement on restart and ordinary unmigrated fixture failures. That candidate will not merge as the activation mechanism; its exact source, tests, authorized expectation changes, failures and review remain historical evidence. Neither those tests nor this draft are deployment permission.

## One production owner

Host `config.toml` may contain:

```toml
[admind]
fulfillment_enabled = false
```

Omission means False. Only TOML boolean `true` or `false` is accepted: integers0/1, strings, floats, lists, tables and null-like programmatic values are configuration errors. Add the key to the existing closed ADMIND_KEYS, resolve it through existing `admind.settings.resolve`, and store `fulfillment_enabled: bool = False` in frozen `AdmindSettings`. Its default also protects trusted programmatic fixture construction; production configuration still passes the strict resolver. Existing host-only `[admind]` restrictions and environment override allowlist remain unchanged. No environment variable, workstream-layer key, CLI enable flag or automatic inference from schema/receipt presence is added.

Every daemon uses its own immutable resolved settings object for its entire lifetime. Restart is required to change capability; no reload watcher, background mutation or per-request configuration lookup. Two daemons in one process can have different settings without cross-instance mutation.

The final production owner is the instance setting alone. No `setting OR fulfillment.ENABLED`, fallback-to-global, source True default or monkeypatchable global override may enable a configured-off daemon. The old constant may temporarily remain False for compatibility with nonproduction imports, but must be unused by production capability decisions; preferably remove it after explicit test-seam migration. A constant must not serve as a second production kill switch either: one setting has one meaning. Source-level Lifecycle primitives and their pure fixtures do not acquire authority through this setting.

## Exact daemon behavior

Replace the three current global capability checks consistently:

| Existing source seam | False/default | True |
| --- | --- | --- |
| `Daemon.on_fulfillment` (`daemon.py:980–985`) | Existing typed unavailable reply/message `capability disabled`; no lifecycle operation | Existing protocol validation and same-UID requirement remain unchanged; enabling does not authorize a caller |
| `Daemon.post_ask` (`daemon.py:1069–1085`) | Preserve existing disabled ordinary posting/routing behavior and Store capacity predicates | Enforce original approved unmigrated-mode requirement; preserve conservative posting on damaged migrated projection |
| `Daemon.handle` (`daemon.py:2841–2862`) | Literal `!fulfill` follows exact prefeature command/bump/ask routing | Existing durable sender/current authorization, latch/deny_rows, target/binding and transactional confirmation checks remain mandatory |

This switch does not grant network send permission, override a latch, repair provenance, authenticate terminal receipts or relax validation. Existing local API UID behavior stays unchanged. No receipt/status/answer is fabricated when off or on. A request already committed before shutdown remains an immutable fact; turning off does not cancel, erase, repeat or infer absence of an operation.

The damaged-projection posting defect in actual HIGH1 finding3 is an existing-design bugfix, not new semantics here: original activation §4.2 requires conservative unsuppressed capacity fallback and holds only fulfillment mutations/verified suppression on damaged v2. Exact unmigrated mode is distinct and retains approved migration-required refusal when producer-enabled. Base Store failure remains an error, never an empty count. The switch neither changes schema nor weakens exact DDL verification.

## Migration, verification and evidence are separate

`admind ask fulfillment migrate --plan --json` and `--apply --json` remain explicit local administrative paths under their existing implementation. They do not consult the producer switch as deployment authority. True does not migrate; False does not prevent an independently authorized planned/quiesced migration. Ordinary Store open must not install the projection. Existing dedicated verification scheduling, conservative Store list/count membership, readback, immutable history and triggers remain as designed; they are not enabled/disabled by this producer setting.

Deployment controller responsibilities remain: genuine separately authenticated deployment consent; exact source/config/consumer pins; stopped-writer quiescence and coherent backup/rollback custody; schema plan/classification and separately approved apply if actually needed; restart/readback; finite genuine receipt acceptance. Source default False permits delivery of repaired code before activation. Nothing here authorizes replay of the already executed morning deployment or any fresh operational step.

Turning False and restarting disables the three producer entrypoints but preserves newest DB, projection, receipts, pending-intent history, audits, answers and decisions. It does not retract possibly issued notices or reconcile an unknown operation. Approved rollback restores qualified source/config only and retains newest coherent database/history; never restore a stale backup over later records. Known076/a73 backward-compatibility limits and forward validation remain unchanged. The controller must check supported config compatibility when rolling back to an older release that does not recognize this new key; it must preserve the old/new pinned config and remove the unsupported key only as part of the exact separately approved rollback configuration. No arbitrary old release is declared compatible.

## Implementation and test responsibilities

Implementation owns settings.py, the three daemon call sites and focused configuration/capability tests. Reuse frozen settings and existing host-layer/unknown-key rejection; do not create a parallel config loader. Update operator example/documentation to show False explicitly. No migration/receipt/schema, V2 management presentation, lifecycle enum, native-approval, backfill, automatic completion or new transport scope is added.

1. Resolver tests: omitted/False/True, strict invalid values, host-only workstream refusal and rejected attempted environment override; defaults on directly constructed trusted fixture settings remain False.
2. Two genuine daemon instances with different settings: all three entrypoints agree; changing the historical module constant cannot bypass configured False or affect the other instance. No production global writes are introduced.
3. Default-off ordinary unmigrated fixtures preserve their original assertions and disabled `!fulfill` routing. New enabled fixtures explicitly select True and perform migration while writers are quiescent BEFORE daemon opening; no blanket test migration or global autouse True.
4. Existing tests that monkeypatch ENABLED migrate narrowly to an explicit per-instance immutable settings fixture (for example construction/replacement before the operation). Archive original bytes, exact setup diff and assertion AST. Do not silently weaken assertions. Restore an independent disabled local-wire regression (HIGH1 finding4).
5. Repair the fresh latched/work_lock-held apply test separately from immutable duplicate readback (finding5). Test real owned ask_server union/wiring rather than hand-built protocol configuration (finding7). Existing UID/authentication checks remain asserted.
6. Enabled tests assert exact migration-required diagnostic, unchanged approval posting, damaged-v2 conservative question/merge capacity behavior and actual new ask admission after a genuinely verified slot release. Do not represent external CLI migration under an already-open daemon as supported (finding6).
7. Retain merged PR132 repeated-cancellation tests and original single-cancellation oracle; verifier drains repeated cancellation while permitting loop callbacks. Its bounded-verifier assumption remains disclosed.
8. Run affected legacy/config/fulfillment suites, configured typing and quality checks with baseline evidence; preserve failures and all historical quality debt. Existing HIGH1 findings8–10 and prior history receive explicit dispositions; a design amendment does not clear a code review.

## Review and activation boundary

This proposal resolves the source-default control ambiguity by keeping default False and moving enablement to exact immutable host configuration. It does not grant deployment approval or assert that enabled-source HIGH1 has passed. After the sole design review/incorporation and genuine human approval, the controller may admit implementation under its native dependency contract. Code repair/review retains its actual ordinal. Subsequent deployment ask must pin the approved amendment, repaired source/review, exact False→True configuration, selected legacy consumers/producers, migration applicability, downtime/backup, finite first genuine completion/terminal-attestation acceptance and rollback. No native approval or history is rewritten.

## Source-grounded citations

Line references below describe the observed candidate and are not stable identifiers; `citations.json` pins exact bytes.

- `src/heterodyne/admind/settings.py:1–6,23–26,56–86,124–146`: existing host-only key owner, frozen DTO and resolver; `269–280` shows existing strict numeric rejection of bool rather than coercion. New strict bool parsing must be explicit, not inferred from integer parsing.
- `src/heterodyne/config/layers.py:60–68,82–92`: environment allowlist and workstream rejection. Do not add a capability environment alias.
- `src/heterodyne/admind/daemon.py:980–985,1079–1085,2849–2862`: exactly three existing global checks; `734–741` owns real request union.
- `tests/test_admind_fulfillment.py:407–435`: current changed capability/UID/latch fixture and global monkeypatch; `439` onward covers enabled/disabled inbound modes. Those existing assertions need explicit oracle accounting during fixture migration.
- Actual enabled-source HIGH1: `N/x84mg-enabled-legacy-code-r1-high-actual-skill-result.md`; findings1/2 motivate this amendment, finding3 is the original approved fallback bugfix, findings4–7 identify separate test/behavior repairs.
- Existing deployment preparation and compatibility evidence: `N/x84mg-activation-deployment-plan-preparation/`, `N/x84mg-activation-consumer-inventory/`, `N/x84mg-repeated-cancel-repair/`, `N/admind-accepted-source-offline-rehearsal/`. Private runtime copies are not publication material or new authority.
