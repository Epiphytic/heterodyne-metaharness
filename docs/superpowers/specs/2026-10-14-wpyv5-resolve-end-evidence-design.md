# btq-wpyv5: keep a generation's end across `wsctl resolve` (g08sd amendment 1)

Bead: btq-wpyv5. Base: main 7cacb83. This amends the approved g08sd spec (`2026-10-13-g08sd-crash-loop-accounting-design.md`) and touches the xb56t resolve (`2026-10-13-xb56t-resolve-unlanded-design.md`) only where that resolve carries the end.

**The ADR.** The governing ADR is 0001 at design-repo commit `93d0e90b` (revision 16). The bead's metadata still names `28e3df4` (revision 15), which g08sd and xb56t were written on. Revision 16 changes §4.4 D7, §6.3, §7, §11, §12, §15 and §17 and adds Appendix A.3, all for managed installs. It doesn't touch §9 or §10, which set the bound this design keeps. So no ADR amendment is needed. This repository's `docs/adr/` copy is still revision 15, and this design doesn't sync it.

## Problem

`wsctl resolve` removes the session directory, and the record goes with it (xb56t step 3). g08sd's guard (`Parker._account`) reads a generation's end from `runtime.ended(key)`. After a resolve that gives None, and None is a death. So every resolved generation counts one death toward `crash_loop`, however it ended. xb56t made that explicit (dc318c9, docs/wsd.md §5, `test_a_resolved_generation_counts_one_death_unless_a_release_resets_first`).

A healthy session can end unlanded. A park or lifetime stop of a live session lands its commits, and the land can be refused because the branch moved under it. T14 hit this case. Today such a session counts as a death once it is resolved. Two such resolves in a row escalate a healthy bead `crash_loop`. A resolved lifetime end also doesn't reset the count, though it would have without the land failure.

The manual path in docs/wsd.md §5 step 4 has the same gap: the operator removes the directory by hand, and the next launch counts one death.

## The end goes with the resolve: record, intent, journal

The end moves through three wsd-owned stores. Each copy is made in the same atomic write that the next step depends on, so there is no point at which the record is gone and the end is not yet stored further along.

1. **Record to intent, in the `confirmed` write.** `Intent` gains three fields, with defaults so that an intent written before wpyv5 still decodes:

   ```python
       end_cause: str = ""              # wpyv5: the record's end, as `ended()` maps it; "" is no evidence
       reached_running: bool = False
       ran_seconds: int | None = None   # the operator's detail only, as in g08sd
   ```

   `resolve_unlanded` fills them from the record that `_resolvable` has just validated (ENDED, unlanded, this workstream's, this generation). It does so in the one `_write_intent` that writes `step=confirmed`. The mapping is `ended()`'s, factored out as `_end_of(rec) -> Ended`, so a resolve and the guard can't read a record differently. That includes a legacy record: an empty `end_cause` with a lifetime `stop_reason` gives `legacy_lifetime`. The record isn't moved until step 3, after `imported`, so the end is in the intent before the record can disappear.

2. **Intent to Resolution.** `Resolution` gains `key: str` and `end: Ended | None`, read from the intent like every other field. `end` is None when the intent's `end_cause` is "" (an intent from before wpyv5). A replay never reads the end again. From step 3 on, the record is in the trash or gone, and before step 3 the intent already holds it. On a replay at `confirmed` or `imported`, `_resolvable` re-reads the record to check the resolve's identity, as it does today, but its end isn't used.

3. **Resolution to journal, in the `unlanded_resolved` transaction.** The daemon's `resolve_one` calls `Journal.emit_resolved(ws, bead, detail, ref, key, generation, end)` in place of `emit_once`. This new method runs one transaction that:
   - inserts the `unlanded_resolved` event unless one of that kind and `ref` exists, as `emit_once` does;
   - if `end` is not None, runs `INSERT OR IGNORE` on the meta row `crash_end:<ws>:<bead>:<key>:<generation>`. The value is `msgspec` JSON of `{"cause": ..., "reached_running": ..., "ran_seconds": ...}`.

   It returns whether it wrote the event. The row is inserted or ignored whether or not the event already exists, so a replay is a no-op either way. `finish_resolution` still runs only after this commits. The intent, and so the hold, stays until the end is in the journal.

**The hold covers the gap.** From the intent's first write until `finish_resolution`, `sessions(ws)` raises `RuntimeUnavailable` (xb56t). The guard runs only after `_reconcile` has listed sessions, so no guard of the workstream can account the generation while its end is only in the intent.

**Why the end can be trusted.** The record sits in the session directory's root, outside every bind (g08sd). The intent is in `<state>/resolutions/`, 0600, wsd-owned and never bound (xb56t). The journal is wsd's. The agent can write none of them, so the end keeps the trust it had in the record. A resolve copies it and never derives anything from the snapshot.

## The guard reads the journal's end

`Journal` gains `resolved_end(entry: LaunchEntry) -> ResolvedEnd | None`. It is an exact-key read of `crash_end:<entry.ws>:<entry.bead>:<entry.session_key>:<entry.generation>`. A row that doesn't decode gives None, which means no evidence. `ResolvedEnd` is a small `msgspec.Struct` in `journal.py` (`cause: str`, `reached_running: bool`, `ran_seconds: int | None`), so the journal doesn't import the runtime's types.

`_account` changes in one place, where it picks a generation's end:

```python
                end = evidence[e.session_key]
                if j.rebuilt_at(*e.ident) is not None:
                    klass = "reset"
                elif e.generation != latest[e.session_key]:
                    klass = "legacy"
                else:
                    if end is None or end.generation != e.generation:
                        end = _resolved(e, j.resolved_end(e)) or end    # wpyv5: carried by its resolve
                    if end is None or end.generation != e.generation or not end.reached_running:
                        klass = "died"
                    else:
                        klass = CLASSES.get(end.cause, "died")
```

`_resolved(e, r)` builds `Ended(e.generation, EndCause(r.cause), r.reached_running, r.ran_seconds)`. An unknown cause becomes `DIED`, as `ended()` maps one. It returns None for None.

- **The order is g08sd's.** A rebuilt entry is still `reset`, and an older generation is still `legacy`, even if it has a row. The journal's end is read only for the latest generation of its key, and only when the runtime has no ended record of that generation. A resolve removes that record, so in practice exactly one of the two exists.
- **The classes are g08sd's.** A resolved live stop is `stopped`, not counted and not a reset. A resolved live lifetime end is `lifetime` and resets. A resolved death is `died` and counts. A generation that never reached running is a death. `_died`'s detail gets `ran_seconds` from the carried end.
- **Escalation stays in the guard.** The guard alone classifies, and it checks the bound in the transaction that accounts, as before. A resolved death that brings the count to the bound therefore escalates before the next launch, like any other death.
- **Crash safety is g08sd's.** The class rows, the counter and the escalation are still one transaction, and `resolved_end` is a read inside it. The `crash_end` row is never changed or removed by accounting, so a replay of the guard reads the same end and decides the same way.

### Rejected: accounting at resolve time

The bead proposed accounting the generation in the `unlanded_resolved` transaction, with the class `_account` would have used. This design stores the end and leaves accounting to the guard, for two reasons:

1. **It skips escalation.** `_account` escalates only when it has something to account (`if not todo: return None`). Suppose launch 1 died, so the count is 1, and launch 2 died unlanded. A resolve that accounted launch 2 would bring the count to 2. The next guard would find nothing to account and would launch a third time, against g08sd's "launch 3 never happens". Moving the bound check outside `todo` would fix that, but it would change the guard for every path.
2. **It splits the rules.** The resolve would need its own copy of the rebuilt, legacy and latest-generation rules, run outside the operation that launches, with its own crash points.

## The manual path (docs/wsd.md §5, step 4)

With wsd not running, nothing can carry the end: there is no intent, and a hand edit of the journal is never asked of an operator. Removing the directory by hand still counts one death, as g08sd says. This design narrows when that happens:

- **If wsd can be started, finish with `wsctl resolve`.** After the manual steps 1 to 3, start wsd and resolve the session instead of removing its directory by hand. If step 3 landed the work, use `--land --tip <that tip>`. The branch is already at the tip, so `_act` imports nothing and `_can_land` passes (`landed == tip`). If you skipped step 3, use `--discard --tip <tip>`. Either way the end is carried. This needs no new code: it is xb56t's resolve, run after a manual land.
- **If wsd can't be started**, step 4 stays the removal by hand, and the next launch counts one death. If that death later makes the bead `crash_loop`, a release clears it. This is accepted (open question 1). The alternatives either give an operator a journal write to make by hand or need an offline resolve, which xb56t's open question 3 declined.

## What the g08sd spec says now

These passages of the approved g08sd spec are replaced. Everything else stands.

- **"Interaction with other flows", "Unlanded commits"**, now: "`sessions()` raises, and the workstream holds, so nothing launches or is accounted. A `wsctl resolve` carries the generation's end from its record, through its intent, into the journal (`crash_end:`), and the next guard accounts it from there as it would have from the record (btq-wpyv5). A session directory removed by hand (`docs/wsd.md` §5, step 4, with wsd not running) has no end, so the next guard counts one death. The operator's release, if any, resets first."
- **Policy clarification 1** gains: "A resolve's copy of the record's end is the record's evidence, not new evidence."
- **"Storage and retention"** gains the `crash_end:<ws>:<bead>:<key>:<generation>` row, one per resolved generation, written once. Its retention is plan 8's, under the same rule as `crash_gen:`.

## Tests (offline)

**`tests/test_sandbox_runtime.py`**:

1. `test_the_confirmed_intent_carries_the_records_end`, parameterized over `land`, `keep` and `discard`, and over records ended `stopped`, `lifetime`, `died`, legacy lifetime (empty cause, lifetime `stop_reason`) and legacy death (both empty). A crash at `imported` leaves an intent whose end fields equal `ended(key)` read before the resolve. The replay's `Resolution.end` equals it too.
2. `test_a_replay_takes_the_end_from_the_intent`, parameterized over every point in `RESOLVE_POINTS`. After the crash the record is rewritten with another `end_cause` (before step 3) or is gone (after it). The replay's `Resolution.end` is still the intent's.
3. `test_an_intent_from_before_wpyv5_has_no_end`: an intent file without the three fields, at each step, decodes. The replay finishes with `Resolution.end` None, and the hold clears as before.
4. `test_a_resolved_generation_has_no_end_to_read` (existing) also checks that `Resolution.key` is the record's key and `Resolution.end` matches.
5. `test_a_manual_land_then_resolve_land_carries_the_end`: the bead branch is moved to the session's tip with `update-ref`, as in manual step 3. Then `--land --tip <tip>` imports nothing (the object count in the repository is unchanged), finishes, and carries `stopped`.

**`tests/test_wsd_journal.py`**:

6. `test_emit_resolved_writes_the_event_and_the_end_once`:
   - the first call writes both;
   - a repeat writes neither and returns False;
   - an event that exists without a row, written before wpyv5, gets the row on a repeat that carries an end;
   - `end=None` writes only the event;
   - a failure injected after the event's insert rolls both back;
   - the schema check passes with the rows present.
7. `test_resolved_end_is_an_exact_key`: a row is found only for its own ws, bead, key and generation, never for a neighbour. A row that doesn't decode gives None.

**`tests/test_wsd_daemon.py`**:

8. `test_resolve_journals_its_end_once`, with crashes at `resolve.resolved` and `resolve.journaled`. After the retry there is one event and one `crash_end:` row, and the hold has cleared.
9. The existing failed-journal-write test also checks that there is no `crash_end:` row and the intent is still at `removed`.

**`tests/test_wsd_park.py`**, through `Wsd.resolve_one` with `FakeRuntime`. `FakeRuntime.resolve_unlanded` builds `Resolution.end` from `ends[key]` before it deletes it, as `SandboxRuntime` does from the record.

10. `test_a_resolved_generation_is_accounted_from_its_end` replaces `test_a_resolved_generation_counts_one_death_unless_a_release_resets_first`. It starts with a count of 1 and is parameterized over the end:
    - `STOPPED` gives `stopped`, and the count stays 1;
    - `LIFETIME` gives `lifetime`, and the count goes to 0;
    - `DIED` gives `died`, and the count goes to 2, which escalates (see 11);
    - a generation that never reached running gives `died`.
    
    The released variant keeps its assertions: the class is `reset` and the count is 0.
11. `test_a_resolved_death_at_the_bound_escalates_before_the_next_launch`: launch 1 dies (count 1), and launch 2 dies unlanded and is resolved. The next pickup's guard escalates `crash_loop` with `needs-human`, and `runtime.launches` has exactly 2 entries. This is the case that accounting at resolve time would get wrong.
12. `test_two_resolved_live_stops_do_not_escalate`: two launches in a row are stopped live with unlanded commits and resolved. A third launch happens, and the count is 0.
13. `test_a_resolved_end_is_only_for_its_own_generation`: a `crash_end:` row for an older generation, another key or another bead doesn't change a latest generation with no end, which is still `died`. A row on a rebuilt entry still gives `reset`, and a row on an older generation still gives `legacy`.
14. `test_a_hand_removed_directory_still_counts_one_death`: manual step 4 (the fake's ended record is dropped with no resolve) gives `died`, and a release first gives `reset`.
15. `test_crash_at_every_accounting_point_counts_once` (existing) gains a resolved-end variant. A crash in `_account`'s transaction or at `<kind>.accounted`, then a replay, gives one class row from the carried end.

## Docs

- `docs/wsd.md` §5, the paragraph from dc318c9 ("A resolve, whether it lands, keeps or discards, removes the session's record…"), becomes: "A resolve carries the generation's end from its record into the journal before the hold clears, so the next launch's guard accounts it as it would have from the record. A live stop isn't counted, a live lifetime end resets the count, and a death counts."
- `docs/wsd.md` §5, manual step 4 gains: "If wsd can be started, do this step with `wsctl resolve` instead: after step 3, `--land --tip <the landed tip>` imports nothing, and otherwise use `--discard --tip <tip>`. Either keeps how the session ended. Removing the directory by hand counts one death."
- `docs/wsd.md` §5 "How a session ended" gains: "A resolve keeps the end (`crash_end:` in `meta`)."
- The g08sd spec isn't edited, since it is approved and digest-pinned. This document lists its replaced passages, and the plan-4 doc's g08sd row gains a pointer here.

## Open questions for Liam

1. **Does a hand removal still count one death?** With wsd not running, there is nothing to carry the end. Recommendation: accept it. It costs at most one counted death per hand removal, a release clears a resulting `crash_loop`, and the docs point operators to `wsctl resolve` whenever wsd can start.
2. **Store the end, rather than account at resolve time as the bead proposed?** Recommendation: store it (above). Accounting at resolve time can let a third launch through at the bound.
