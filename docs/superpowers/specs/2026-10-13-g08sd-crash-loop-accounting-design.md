# btq-g08sd: persistent crash-loop accounting

Bead: btq-g08sd. Base: main 850d531. ADR 0001 r15 (28e3df4). Revision 2 addresses Codex r1 (`/tmp/review-g08sd-r1.md`): findings 1 and 2 were blocking, and 3 to 5 were not.

**The ADR.** §9 (reconcile) and §10 ("Agent or runner crash") say that a bead whose session dies is resumed, and that after 2 failures it becomes `needs-human`. This design implements that bound, so it needs no amendment. The ADR doesn't say what proves a run was healthy, what resets the count, or whether a reboot counts. This design decides those as plan-level policy, listed under "Policy clarifications" below. Approving the design approves them. They are not presented as something the ADR already establishes.

Plan 3's P1 (plan-3 doc, "Prerequisites for later plans") and `docs/wsd.md` §5 and §6 name this as the last thing before a real runtime is enabled.

## Problem

A session that starts and then dies spends no budget anywhere today:

- The launch-failure budget counts only refused launches. `Parker._outcome` (`park.py`) calls `_spend` on a `refused` receipt. A `started` receipt finishes the operation DONE, and the bead is RUNNING.
- When that session dies, `runtime.sessions(ws)` ends its record and stops listing it. Then either of two things happens. The sweep's `_Sweep._running` (`sweep.py`) opens a **new** RESUME operation with `attempts = 0` (`open_resumes`). Or a PARK or defer operation that was already open finds the session gone, finishes PARKED or DEFERRED, and a later resume or undefer launches again.
- Either way the next launch starts, the operation finishes, and the loop repeats without limit.

There is a second problem. `SandboxRuntime._end` calls a session `lifetime` whenever it is found past its deadline (`_overdue`). A session that died at once and was found late, after an outage, looks like a healthy lifetime end.

## Evidence: an end cause, recorded once

`SessionRecord` (`sandbox/runtime.py`) gains three fields:

```python
    end_cause: str = ""      # g08sd: how this generation ended; written once, never changed
    ended_at: int = 0        # the `ended` write's clock
    # started_at (exists) is per generation: each launch writes a new record, and it is set at `running`
```

`stop_reason` and `wip_mark` stay exactly as they are. They are the lifetime WIP obligation (D13), not evidence of health. A session found dead past its deadline still owes its lifetime WIP commit, as today, but its end cause is `died`.

`_end` decides the cause before its first `stopping` write. It writes the cause in that same write, and it never changes a cause that is already recorded:

```python
DIED, STOPPED, LIFETIME, REFUSED = "died", "stopped", "lifetime", "refused"

    def _end(self, rec: SessionRecord, *, interrupt: bool, lifetime: bool = False, cause: str = DIED) -> bool:
        changes: dict[str, str] = {}
        if lifetime or rec.stop_reason == LIFETIME:                     # the WIP obligation, unchanged
            changes = {"stop_reason": LIFETIME, "wip_mark": rec.wip_mark or f"lifetime:{rec.key}:{rec.generation}"}
        if not rec.end_cause:
            changes["end_cause"] = self._cause(rec, cause)
        ...                                                             # stopping write, teardown, land
            rec = self._phase(layout, rec, phase=Phase.ENDED, ended_at=self.c.clock())

    def _cause(self, rec: SessionRecord, asked: str) -> str:
        """g08sd: a stop proves health only if the runtime starts it on a generation it confirms live,
        here, before the stop. Anything else, including absence found late, is a death."""
        if asked == REFUSED:
            return REFUSED
        if asked in (STOPPED, LIFETIME) and rec.phase is Phase.RUNNING and self._alive(rec, self._names()):
            return asked
        if not rec.end_cause and rec.phase is Phase.STOPPING:           # a record written before g08sd
            return "legacy_lifetime" if rec.stop_reason == LIFETIME else DIED
        return DIED
```

- `stop()` asks for `STOPPED`, and `expire()` asks for `LIFETIME`. `sessions()` and `launch()`'s cleanup of `prev` keep the default `DIED`. `_fail` asks for `REFUSED`.
- `_alive` raising `RuntimeUnavailable` propagates as it does today, and nothing is recorded. The cause is decided only when liveness was actually read.
- **Delayed discovery.** A session that dies, then stays unseen through an outage until after its deadline, is `died`, with the lifetime WIP owed. A replayed cleanup that crosses the deadline keeps the cause it recorded first.
- **The reaper.** The deadline watcher (`sandbox/reaper.py`) deletes the sandbox at the deadline and records nothing. A session it killed is later found absent, which makes it `died`. Healthy sessions must therefore be stopped by wsd before the reaper acts. `expire()`'s hard stop moves from `now >= deadline` to `now >= deadline - hard_lead`. Here `hard_lead = min(120, stop_margin_seconds // 2)` seconds. `build_runtime` refuses a configuration whose `wsd.backstop_seconds` is not shorter than `hard_lead`, so a pickup always runs inside that window. This is a refinement of plan 4 D13: the ADR §7 rule ("a hard interrupt", stopped before the token expires) is unchanged.
- **Why it is trusted.** `session.json` sits in the session directory's root. Only `home`, `bridge`, `git` and the generation's folder are bound into the sandbox (`docs/wsd.md` §5), so the agent can't write the record. The pane's exit status, hook-spool events and transcripts are not used (ADR §3.3).

## The seam: `AgentRuntime.ended`

```python
class EndCause(StrEnum):
    DIED = "died"
    STOPPED = "stopped"                  # wsd stopped it, confirmed live when the stop began
    LIFETIME = "lifetime"                # the runtime's lifetime stop, confirmed live when it began
    REFUSED = "refused"
    LEGACY_LIFETIME = "legacy_lifetime"  # a pre-g08sd lifetime end: liveness was never checked


@dataclass(frozen=True)
class Ended:
    generation: int
    cause: EndCause
    reached_running: bool               # this generation's `started_at` is set
    ran_seconds: int | None             # None when either time is missing (a legacy record has no ended_at)


class AgentRuntime(Protocol):
    def ended(self, session_key: str) -> Ended | None:
        """The latest generation's end, once the runtime has confirmed it, or None when the runtime has
        no ended record for the key. Raises RuntimeUnavailable if the record can't be read."""
```

- **`SandboxRuntime.ended`** reads the record. It gives None if there is no record or the phase is not `ended`. Whether the generation ran (`reached_running`, from this generation's `started_at`) is reported separately from how long it ran. `ran_seconds` is only for the operator's detail and never decides anything. `ran` is not used, because it persists across generations.
- **Legacy records** (ended before g08sd, with no `end_cause`) map as follows:
  - `stop_reason == "lifetime"` gives `LEGACY_LIFETIME`, which is neither counted nor a reset;
  - `stop_reason == ""` gives `DIED`;
  - a legacy STOPPING record is listed `unknown` until a new `_end` finishes it with `_cause` above.
- **`NoRuntime.ended`** raises `RuntimeUnavailable`. **`FakeRuntime`** keeps `ends[key]`: `end(key, cause=EndCause.DIED)` for a session that ended on its own, `stop()` for STOPPED, and a `lifetime(key)` control for LIFETIME.

## Accounting in the launch guard, for every launched generation

Every launch goes through `Parker.launch`: pickup, resume (from the sweep, a parked bead or a release), undefer, and their replays. Before the guard pins a new generation (step 2, `_pin`), it accounts for every launched generation of the bead that isn't accounted yet. It escalates there if the bound is reached. So no path launches a replacement without first counting the deaths before it, whoever ended the dead session (the sweep, a park, a defer, or a replay of either).

Journal state, in the existing `meta` table (no schema change; see "Storage"):

- `crash_gen:<ws>:<bead>:<session_key>:<generation>` = the accounted class (`died`, `stopped`, `lifetime`, `legacy`, `reset`). There is one row per launched generation, written once.
- `crash_run:<ws>:<bead>` = the count of deaths since the last reset.

```python
    def _account(self, op: Op, own: list[Session]) -> Launch | None:      # Parker; after step 1, before step 2
        """g08sd: every launched generation of the bead is classified once, in order, before any new one
        is pinned; the bound escalates here, so the launch that would follow never happens."""
        j, ws, bead = self.d.journal, self.ws.name, op.bead
        todo = [e for e in j.launches_of(ws, bead) if e.outcome == LAUNCHED and not j.crash_accounted(e)]
        if not todo:
            return None
        latest = {e.session_key: max(x.generation for x in todo if x.session_key == e.session_key) for e in todo}
        evidence = {k: self.d.runtime.ended(k) for k in latest}          # RuntimeUnavailable: a hold
        details: list[str] = []
        with j.transaction():
            for e in sorted(todo, key=lambda e: (e.journaled_at, e.generation)):
                end = evidence[e.session_key]
                if e.generation != latest[e.session_key]:
                    klass = "legacy"         # launched before g08sd: its record is long overwritten
                elif end is None or end.generation != e.generation or not end.reached_running:
                    klass = "died"           # no trusted end evidence: a death, never a healthy end
                else:
                    klass = {EndCause.STOPPED: "stopped", EndCause.LIFETIME: "lifetime",
                             EndCause.LEGACY_LIFETIME: "legacy"}.get(end.cause, "died")
                n = j.crash_account(ws, bead, e, klass)   # died: +1; lifetime: 0; else unchanged
                if klass == "died":
                    details.append(_died(e, end))
            if n >= self.ws.limits.launch_failures_before_human:
                esc = self._stuck(op, Reason.CRASH_LOOP, f"{'; '.join(details)}; {n} in a row")
            else:
                esc = None
        self.d.cp(f"{op.kind.value}.accounted")
        if esc is None:
            return None
        self._handoff(esc)
        return Launch.ENDED
```

The guard calls `_account` in `launch()` after `_reconcile` (step 1) and after the own-session checks, so no session of the bead is listed. It calls it immediately before `_pin_and_dispatch`. `_own_dispatch` (step 0) returns earlier for an operation whose own generation is already dispatched, so a replay of a dispatched launch never accounts again.

- **Why only the latest generation per key has evidence.** The runtime keeps one record per key, and a new launch overwrites it. Because accounting runs before every pin, each key has at most one launched generation that isn't accounted, and it is the latest. An older unaccounted generation can only predate g08sd. It is accounted `legacy`, not counted: no evidence remains, and counting it would escalate existing beads at upgrade.
- **The bound and escalation.** The bound is `Limits.launch_failures_before_human` (default 2), whose comment already cites §9 and §10, so there is no new key. With the default:
  1. launch 1 dies, and before launch 2 the count is 1;
  2. launch 2 dies, and before launch 3 the count is 2, so the bead escalates and **launch 3 never happens**.

  The escalation is STUCK with a new fixed reason, `Reason.CRASH_LOOP = "crash_loop"`, and a numbers-only detail (for example `generation 7 died after 41s; generation 8 died; 2 in a row`). The STUCK row and the escalation's intent commit in the same transaction as the accounting, then `needs-human` goes on the bead, as `_spend` does (plan 3 finding 11). The only exit is `Parker.release` (plan 3 D19).
- **Crash safety.** The class rows, the counter and the escalation's journal half are one transaction. A crash before it commits leaves nothing, and the replay reads the same ended record (nothing has overwritten it, because nothing was pinned) and decides the same way. After the commit, the rows make the replay a no-op. An ESCALATE cut short before its label is replayed like any open escalation.
- **The refused-launch budget is separate and unchanged.** Refused generations are never LAUNCHED, so they are never accounted here. They stay on the operation's `attempts` (`_spend`).
- **The sweep is unchanged.** `open_resumes` opens resumes as today. The guard counts.

## Resets

- **A lifetime stop of a live generation** (`EndCause.LIFETIME` from `_cause`) sets the counter to 0 when it is accounted. It is the only runtime evidence of health: the runtime itself stopped a generation that it had just confirmed live, at the end of its lifetime.
- **An intentional stop** (`STOPPED`: park, defer, not ours, release, an operator stop) of a live generation is not counted and does not reset. It proves the session was alive when wsd stopped it, not that it was healthy.
- **Operator resets**: `Parker.release`'s intent transaction, and a new claim in `Scheduler.start_new`'s intent transaction (a bead returned to the queue). Each sets the counter to 0 and accounts every unaccounted launched generation of the bead as `reset` (not counted). The class rows are kept, because a reclaimed bead keeps its session key and its generation numbers.

## Storage and retention

- **No migration.** Both kinds of row live in `meta`, following the precedent of `deferral_over:` (AU-4 §2.1). The schema and `SCHEMA_VERSION = "2"` are unchanged, so `_check`'s exact match stands and a v1 journal upgrades as today. An older wsd ignores the rows. Workstream names and bead IDs are slugs (`ids.SLUG`, no `:`). Every lookup is by exact key, so there are no prefix scans.
- **`Journal`** gains `crash_accounted(entry) -> bool`, `crash_account(ws, bead, entry, klass) -> int` (an insert-or-ignore of the class row, and the counter changed only when that row is new) and `crash_reset(ws, bead)`.
- **Retention is deferred** to the journal-retention work (plan 8, beside `Journal.backup`). It is not designed here. There is at most one row per launched generation, plus one counter per bead. When retention is designed, it must keep the rows of any bead with a non-terminal row or an open operation, and must never reset the count of a bead that can still be relaunched. The rows of a closed or dropped bead are its only candidates.

## Interaction with other flows

- **Park and defer:** a death they find (their `_sessions()` ends the record as `died`) is counted by the next resume's or undefer's guard, before that launch. Their own stop of a live session is `STOPPED`.
- **Lifetime (D13):** a live lifetime stop resets. A session found dead after its deadline is a death that still owes its WIP commit. The earlier hard stop keeps healthy sessions away from the reaper.
- **Reboots and outages:** sandboxes and panes are gone, so a running session is `died` and counts (policy 3). Each outage-and-death cycle counts once, so repeated cycles are bounded like any other.
- **Unlanded commits:** `sessions()` raises, and the workstream holds, so nothing launches or is accounted. After the operator removes the session directory (`docs/wsd.md` §5, step 4), `ended()` gives None, and the next guard counts one death. The operator's release, if any, resets first.
- **Handoff or swap to another profile:** a new key, but the counter is per bead. Swaps today are operator actions through release.
- **Restarts:** everything is in the journal. A lost journal escalates the bead `journal_lost` anyway (plan 3 D8).

## Policy clarifications (decided here, not in the ADR)

1. A crash is a launched generation that ended without the runtime stopping it while it was confirmed live, including when no trusted end evidence exists.
2. The count resets only on a live lifetime stop, a release, or a new claim. Intentional stops neither count nor reset.
3. A reboot or a backend loss that kills running sessions counts as a death.
4. A session the reaper deleted counts as a death. wsd's hard stop comes `hard_lead` before the deadline so that a healthy session never reaches the reaper.
5. The bound reuses `launch_failures_before_human` with a separate counter.

## The setup default

This is a separate step after this lands, and it needs Liam's go. The change is one line: `platform._BACKENDS["linux"]["sandbox"] = "openshell"`. `heterodyne setup` never overwrites an existing `config.toml`, so it affects only new hosts. Those hosts then need host preparation (OpenShell 0.1.2, rootless podman 5, the image, `ptrace_scope = 1`) before any agent runs.

## Tests

`tests/test_sandbox_runtime.py`:

1. `test_end_cause_needs_a_live_generation`: `stop()` of a live session records `stopped`, and `expire()` of a live one records `lifetime`. A dead pane found by `sessions()` records `died`, and a refused launch records `refused`. `stop()` and `expire()` on a RUNNING record whose pane is dead record `died`.
2. `test_an_end_cause_is_never_changed` (replaces r1's test 2): a record in `stopping` with `died`, replayed after the deadline, stays `died`, gains `stop_reason = "lifetime"` and its `wip_mark`, and the WIP lands once. A `stopped` one crossing the deadline stays `stopped`.
3. `test_delayed_discovery_after_the_deadline_is_a_death`: the session dies, the backend is unavailable until past the deadline, and then `sessions()` gives `died` with the lifetime WIP owed.
4. `test_the_hard_stop_comes_before_the_reaper`: `expire()` interrupts at `deadline - hard_lead`, not later. `build_runtime` refuses `backstop_seconds >= hard_lead`.
5. `test_ended_reports_generation_evidence`:
   - `ended()` gives None before the end;
   - `reached_running` is true with `ran_seconds` None when `ended_at` is missing;
   - legacy records: a lifetime end gives `LEGACY_LIFETIME`, an empty cause gives `DIED`, and a legacy STOPPING record is finished by `_cause`;
   - an unreadable record gives `RuntimeUnavailable`.

`tests/test_wsd_journal.py`:

6. `test_crash_accounting_is_once_per_generation`:
   - accounting a generation twice changes the counter once;
   - counters are per bead;
   - `lifetime` zeroes the counter, and `stopped` and `legacy` leave it alone;
   - `crash_reset` zeroes it and keeps the class rows;
   - the schema check passes with the rows present.

`tests/test_wsd_park.py` (the guard):

7. `test_a_session_that_dies_on_every_launch_escalates_before_a_third_launch`: through the sweep's resumes, the runtime's `launches` has exactly 2 entries, and the bead is STUCK / `crash_loop` with `needs-human`.
8. `test_a_death_consumed_by_a_park_is_counted_at_resume`:
   - the session dies, then a PARK runs before any sweep and ends the record `died`;
   - the blockers close, and the resume launches (count 1);
   - it dies, is parked the same way, and the next resume escalates with no third launch.
9. `test_a_death_consumed_by_a_defer_is_counted_at_undefer`: the same sequence through a defer and an undefer.
10. `test_a_crash_during_park_or_defer_replay_still_counts`, parameterized over every `park.*` and `defer.*` point: crash there, replay, then resume or undefer. The death is counted once.
11. `test_crash_at_every_accounting_point_counts_once`: a crash inside `_account`'s transaction, at `<kind>.accounted`, `escalate.intent` and `escalate.labelled!`, for pickup, resume and undefer operations. After replay there is one class row per generation, at most one ESCALATE, and no launch past the bound.
12. `test_repeated_outage_and_death_cycles_are_bounded`: launch, death, runtime unavailable until after the deadline, then recovery, repeated. The bead escalates at the second death, and no cycle is counted as `lifetime`.
13. `test_a_live_lifetime_stop_resets_and_an_intentional_stop_does_not`: death, live lifetime stop, death gives a resume (count 1). Death, live park stop, resume, death gives an escalation.
14. `test_release_resets_and_accounts_pending_generations_as_reset`: a `crash_loop` bead is released, and its next death is resumed. A bead released with an unaccounted death doesn't count it.
15. `test_refused_launches_keep_their_own_budget`: two refused launches still escalate `launch_failed` with no class rows.
16. `test_generations_before_g08sd_are_legacy`: a journal with launched generations and no class rows. Only the latest per key is classified from evidence, and the rest are `legacy`.
17. `test_the_count_survives_a_restart`: one death, a new `Wsd` on the same journal, and the second death escalates.

`tests/test_wsd_pickup.py`:

18. `test_a_new_claim_resets_the_count`: a bead returned to the queue with a count of 1 is claimed again. The counter is 0 in the claim's transaction, and its old generations stay accounted.

Existing tests that call `FakeRuntime.end(key)` twice in a row on one bead and expect two relaunches, and the never-idle property, are audited. Each either expects `crash_loop` before the third launch or ends with `lifetime(key)`. The property's oracle accepts STUCK for a bead whose fault is "dies at once".

## Docs

- `docs/wsd.md` §1: `launch_failures_before_human` also bounds consecutive deaths of a bead's session, counted before each launch. Resets: a live lifetime stop, a release, a new claim.
- `docs/wsd.md` §5:
  - "Enabling it": drop "wait for crash-loop accounting";
  - the lifetime paragraph: the hard stop comes `hard_lead` before the deadline, and a session found dead late is a death that still owes its WIP;
  - a short paragraph on `end_cause` and `crash_loop`;
  - the unlanded procedure's step 4: the next launch counts one death.
- `docs/wsd.md` §6: replace the "Plan 4 must add persistent crash-loop accounting…" sentences with the `ended()` seam. Keep "setup does not select it" until the setup step is approved.
- `docs/install.md` (line 12): drop "should wait for crash-loop accounting (btq-g08sd) first".
- The plan-4 doc: a D13 note for `hard_lead`, and a row pointing here. The plan-3 doc's P1 is a record and is not edited.

## Open questions for Liam

1. **Setup default.** Should setup record `openshell` on Linux after this lands? Recommendation: yes, as its own small bead, with your go.
2. **Reboots count** (policy 3). Two reboots with no live lifetime stop between them escalate a healthy bead. Recommendation: accept this. A release clears it, and exempting reboots would let a workload that crashes the host loop without limit.
3. **`hard_lead`.** It is `min(120 s, margin / 2)`, and the config is refused when the backstop is not shorter. Recommendation: accept this. The defaults (60 s backstop, 15 min margin) give a 120 s lead.
4. **A separate limit.** Should crashes get their own `wsd.crashes_before_human`? Recommendation: no. The ADR names one number, and splitting it later needs no migration.
