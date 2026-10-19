**Verdict: good enough as a source-only merge PoC.** The review found no critical flaws in the Plan8 changes or in how the merge was done. It did find real defects, but all of them are in code that came in from main and is unchanged by this merge.

I ran the built-in `code-review` skill at medium effort on the uncommitted merge. HEAD is `41d16440` and MERGE_HEAD is `5db4c0c2`. It returned 10 findings, and none are in the Plan8 `wsd` daemon, CLI or ctl code. I then compared each flagged file against the merge tip and HEAD. All six files are byte-identical to accepted main `5db4c0c` and differ from HEAD only because they came in with the merge: `fulfillment_projection.py`, `fulfillment.py`, `fulfillment_cli.py`, `management.py`, `daemon.py` and `store.py`. That fits the freeze record: the incoming files are unchanged and there were no manual edits.

So these defects already exist on main and were not introduced here. They should be tracked against main. I have not independently confirmed each one; that would mean running tests, which this read-only scope rules out.

**Main-side findings worth tracking, most serious first:**
1. **Capacity fallback on a store that hasn't been migrated** (`fulfillment_projection.py:221`): this one matters before activation. If `fulfillment migrate --apply` hasn't been run, asks with valid receipts keep counting toward capacity. Once enough fulfilled asks pile up, every `post_ask` and approval post is refused with `ASK_TOO_MANY`. `fulfilled_asks()` also returns nothing.
2. **Receipt size mismatch** (`fulfillment.py:416`): stored receipts are accepted up to 128 KiB but parsed with a 64 KiB limit. Receipts between those sizes can't be viewed or re-applied, even though the background worker marks them valid.
3. **Reconcile reports conflicts as `already-applied`** (`fulfillment_cli.py:167`): if an ask changed after its receipt was applied, the operator is told everything is fine when the receipt is actually in conflict.
4. **`duplicate()` replies "fulfilled" for receipts marked invalid** (`fulfillment.py:553`): this happens after an ask is edited or a new answer is added, while the ask still counts toward capacity.
5. **`!details` refused on full-size management cards** (`management.py:155`): `!details` adds one line, so a card exactly at the size limit posts fine but `!details` on it is refused.
6. **Lower severity:**
   - `ask list` can show an ask twice or drop it, because its two queries can see different verification states (`daemon.py:1046`).
   - Every post or list runs about 30 extra schema queries.
   - On stores that haven't been migrated, the verification worker logs a warning every 30 seconds forever.
   - The binding-digest code is duplicated, and only one copy caps answers at 50.
   - `Lifecycle.refresh()` and `CAPACITY_ACTIVE_SQL` are now unused.

**What this doesn't change:**
- The ten MED9 findings stay open as you specified.
- Main's 148 quality gates covering 317 regressions are still unwaived.
- Also still open before activation: the MissingEffectStore hold, the shutdown issues and the probe issues.
- Finding 1 should be added to that pre-activation list. Combined with the MissingEffectStore hold and the requirement for an explicit first install, it means a deployment that skips the migration step would block posting asks.
- There is still no authority to deploy, change systemd ownership, accept as native, waive permissions, start the release clock or act as the scanner.

`★ Insight ─────────────────────────────────────`
- A cumulative review of an uncommitted merge shows every file that changed relative to HEAD, including files that came in from main. A quick blob-hash check (`git hash-object` against `MERGE_HEAD:path`) tells you whether a finding is in your branch's code or was carried in from main. Here all ten were carried in.
- Findings 1, 3 and 4 have the same cause. Main changed the meaning of "valid receipt" to projection stamps (an epoch/generation counter written by the worker), but some code paths still use the old checks. That is why the dead `refresh()` and `CAPACITY_ACTIVE_SQL` (finding 6) are worth deleting.
`─────────────────────────────────────────────────`

I didn't edit any files, run any tests, or touch the queue, services, databases, network or GitHub. The only commands I ran were read-only git hash comparisons.
