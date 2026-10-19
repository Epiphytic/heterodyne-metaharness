# Plan8 MED10 dispositions

Actual built-in medium review: no critical flaws; good enough as a source-only merge PoC. All ten findings are inherited from accepted main and remain OPEN UNWAIVED. This is not deployment clearance.

## 1. OPEN UNWAIVED

**Capacity fallback on a store that hasn't been migrated** (`fulfillment_projection.py:221`): this one matters before activation. If `fulfillment migrate --apply` hasn't been run, asks with valid receipts keep counting toward capacity. Once enough fulfilled asks pile up, every `post_ask` and approval post is refused with `ASK_TOO_MANY`. `fulfilled_asks()` also returns nothing.

Inherited main activation prerequisite. Explicit fulfillment migration and verified projection rollout remain required; this archival makes no migration or deployment claim.

## 2. OPEN UNWAIVED

**Receipt size mismatch** (`fulfillment.py:416`): stored receipts are accepted up to 128 KiB but parsed with a 64 KiB limit. Receipts between those sizes can't be viewed or re-applied, even though the background worker marks them valid.

Inherited main receipt-size inconsistency remains open; no repair or independent reproduction in this documentation-only task.

## 3. OPEN UNWAIVED

**Reconcile reports conflicts as `already-applied`** (`fulfillment_cli.py:167`): if an ask changed after its receipt was applied, the operator is told everything is fine when the receipt is actually in conflict.

Inherited main conflict-reporting defect remains open; already-applied output is not independent proof of settlement.

## 4. OPEN UNWAIVED

**`duplicate()` replies "fulfilled" for receipts marked invalid** (`fulfillment.py:553`): this happens after an ask is edited or a new answer is added, while the ask still counts toward capacity.

Inherited main duplicate response defect remains open; no capacity or authority exception is granted.

## 5. OPEN UNWAIVED

**`!details` refused on full-size management cards** (`management.py:155`): `!details` adds one line, so a card exactly at the size limit posts fine but `!details` on it is refused.

Inherited main full-size details refusal remains open; no rendering policy change made.

## 6. OPEN UNWAIVED

`ask list` can show an ask twice or drop it, because its two queries can see different verification states (`daemon.py:1046`).

Inherited main list snapshot race remains open.

## 7. OPEN UNWAIVED

Every post or list runs about 30 extra schema queries.

Inherited main schema-query overhead remains open; no performance claim or broad refactor.

## 8. OPEN UNWAIVED

On stores that haven't been migrated, the verification worker logs a warning every 30 seconds forever.

Inherited main unmigrated-worker warning behavior remains open; migration is a separate authorized rollout step.

## 9. OPEN UNWAIVED

The binding-digest code is duplicated, and only one copy caps answers at 50.

Inherited main duplicated binding digest and answer-bound inconsistency remain open; no authentication or binding equivalence claim.

## 10. OPEN UNWAIVED

`Lifecycle.refresh()` and `CAPACITY_ACTIVE_SQL` are now unused.

Inherited main unused lifecycle helpers remain open; no code removed.

All MED9 activation holds remain, including explicit EffectStore.initialize with installation/generation provenance, no automatic missing-store recreation, bounded foreign-alert capacity, cancellation/origin observability, shutdown versus long install, and probe/CI limitations. Scheduled maintenance hold does not quiesce operator commands. The install reply allowance is not a proved execution bound.

Existing integration checks: 595 passed, 2 BTQ_REPO-dependent skips; configured typing 466 files, zero errors/warnings; Ruff and diff checks passed. No tests were rerun for this evidence-only archival. The inherited 317 quality regressions / 148 gates remain UNWAIVED.

Prior review ordinals and actual reports remain unchanged. There is no authority here for deployment, systemd ownership, native acceptance, permission waiver, scanner execution or starting the release clock.
