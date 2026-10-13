# btq-g08sd: persistent crash-loop accounting

Bead: btq-g08sd. Base: main 850d531. ADR 0001 r15 (28e3df4) is unchanged. §9 (reconcile) and §10 ("Agent or runner crash") already say that a bead whose session keeps dying is resumed, and that after 2 failures it becomes `needs-human`. This design implements that rule, so it needs no amendment. Plan 3's P1 (plan-3 doc, "Prerequisites for later plans") and `docs/wsd.md` §5 and §6 name this as the last thing before a real runtime is enabled.

## Problem

A session that starts and then dies spends no budget anywhere today:

- The launch-failure budget counts only refused launches. `Parker._outcome` (`park.py`) calls `_spend` on a `refused` receipt. A `started` receipt finishes the operation DONE, and the bead is RUNNING.
- When that session dies, the next pickup's `runtime.sessions(ws)` ends its record (`SandboxRuntime.sessions` calls `_end` on a RUNNING record whose pane or sandbox is gone), and stops listing it. The sweep's `_Sweep._running` (`sweep.py`) then finds a RUNNING bead with no session and calls `open_resumes`. That opens a **new** RESUME operation with `attempts = 0`, at step `unlabelled`, so the guard's check `op.attempts >= launch_failures_before_human` starts over.
- The guard launches again, the session starts, the operation finishes, and the loop repeats. Nothing ever reaches the bound.

A lifetime stop goes through the same path on purpose. `Scheduler._pickup` calls `runtime.expire`, and then the sweep resumes the bead. So the sweep can't count every resume as a crash: it needs the runtime to say how the session ended.

## What counts as a crash

A crash is a generation that reached `running` and then ended without being ended by the runtime. The runtime ends a session in three ways, all through `SandboxRuntime._end`:

| How the session ended | Caller | `stop_reason` (new values in bold) | A crash? |
|---|---|---|---|
| wsd stopped it: park, defer, a bead no longer ours, release, an operator stop | `stop()` | **`stopped`** | no |
| Its lifetime was up, or it was found dead past its deadline (the deadline watcher deleted it) | `expire()`, or `_overdue` in `sessions()` and `launch()` | `lifetime` (exists) | no |
| Found dead before its deadline: the agent CLI exited or crashed, the pane died, the sandbox went away | `sessions()`, or `launch()`'s cleanup of `prev` | **`died`** | yes |
| A launch the runtime refused (`_fail`) | `launch()` | **`refused`** | not counted here; the existing launch budget counts it |

The cause is written by `_end` in its first `stopping` write, before anything is torn down, as `lifetime`'s already is. A later `_end` of the same record never changes it. The one exception is the existing rule that a lifetime end overrides the cause, because it owes its WIP commit:

```python
STOPPED, DIED, LIFETIME, REFUSED = "stopped", "died", "lifetime", "refused"

    def _end(self, rec: SessionRecord, *, interrupt: bool, lifetime: bool = False, cause: str = DIED) -> bool:
        owed: dict[str, str] = {}
        if lifetime or rec.stop_reason == LIFETIME:
            owed = {"stop_reason": LIFETIME, "wip_mark": rec.wip_mark or f"lifetime:{rec.key}:{rec.generation}"}
        elif not rec.stop_reason:
            owed = {"stop_reason": cause}               # g08sd: the first cause recorded stands
        ...
            rec = self._phase(layout, rec, phase=Phase.ENDED, ended_at=self.c.clock())
```

`stop()` passes `cause=STOPPED`, and `_fail` passes `cause=REFUSED`. `sessions()` and `launch()` keep the default. `SessionRecord` gains `ended_at: int = 0`. `started_at` already exists.

**Why this evidence is trusted.** `session.json` sits in the session directory's root. Only `home`, `bridge`, `git` and the generation's folder are bound into the sandbox (`docs/wsd.md` §5), so the agent can't write the record. The pane's exit status is not used, because the agent controls it. Hook-spool events and transcripts are not used either: they stay untrusted observations (ADR §3.3).

**Host restarts count.** After a reboot, the tmux server and the sandboxes are gone, so a session that was running is found `died`. The table above has no separate cause for this (see Open question 2).

## The seam: `AgentRuntime.ended`

`heterodyne.wsd.runtime` adds:

```python
class EndCause(StrEnum):
    DIED = "died"
    STOPPED = "stopped"
    LIFETIME = "lifetime"
    REFUSED = "refused"


@dataclass(frozen=True)
class Ended:
    """How the runtime recorded a session's latest generation ending (g08sd)."""
    generation: int
    cause: EndCause
    ran_seconds: int | None     # None: the generation never reached running


class AgentRuntime(Protocol):
    def ended(self, session_key: str) -> Ended | None:
        """The latest generation's end, once the runtime has confirmed it, or None when the runtime
        has no ended record for the key. Raises RuntimeUnavailable if the record can't be read."""
```

- **`SandboxRuntime.ended`** reads the key's record with `read_record`, which raises `RuntimeUnavailable` on an unreadable record. It returns None if there is no record or the phase is not `ended`. An ended record whose `stop_reason` is `""` was written before this change, and it maps to `DIED`. `ran_seconds` is `ended_at - started_at` when both are set, and None otherwise.
- **`NoRuntime.ended`** raises `RuntimeUnavailable`. It is never reached, because `sessions()` raises first.
- **`FakeRuntime`** (`tests/fakes/fake_runtime.py`) keeps `ends[key]`. `end(key, cause=EndCause.DIED, ran=60)` records a session that ended on its own. `stop()` records `STOPPED`, and a new `lifetime(key)` control records `LIFETIME`. The generation comes from the spec of the key's last launch.

## Where the count lives

The journal's `meta` table holds one row per counted death: `crash:<ws>:<bead>:<session_key>:<generation>`, with the time it was counted as the value. This follows the precedent of `deferral_over:` (AU-4 §2.1).

- **No migration.** The schema is unchanged, so `_check`'s exact schema match and `SCHEMA_VERSION = "2"` stand. A v1 journal upgrades exactly as it does today. An older wsd ignores the rows. Workstream names and bead IDs are slugs (`ids.SLUG`, no `:`), so the prefix is unambiguous. Rows are matched by `substr(key, 1, n) = prefix`, not `LIKE`: `_` is legal in a slug, and `LIKE` would treat it as a wildcard.
- **Per bead.** The ADR's consequence falls on the bead ("the bead becomes `needs-human`"). A bead has one session key per profile, and a profile change goes through the operator anyway, which resets the count (below).
- **Idempotent by generation.** `INSERT OR IGNORE` on `(session_key, generation)`, so seeing the same death twice counts it once.

`Journal` gains three methods, each `@_locked`:

```python
CRASH = "crash:"     # g08sd: a generation of the bead that died, until a reset

    def crash_add(self, ws: str, bead: str, session_key: str, generation: int) -> int:
        """Count one death (idempotent per generation); the bead's count after it."""

    def crashes(self, ws: str, bead: str) -> int: ...

    def crash_clear(self, ws: str, bead: str) -> None: ...
```

## The check: in `open_resumes`, before any relaunch

The sweep reads the runtime's evidence while it reads everything else. `open_resumes` counts and decides in the same transaction that would open the resume. In recovery's deferred sweep, that is the one transaction that opens every resume.

```python
@dataclass(frozen=True)
class Dead:
    bead: str
    session_key: str
    generation: int      # the session's latest launched generation in the journal; 0 if none
    crashed: bool
    detail: str


    def _death(self, rec: SessionRecord) -> Dead:            # _Sweep, from `_running`
        launched = [e for e in self.j.launches(rec.session_key) if e.outcome == LAUNCHED]
        latest = launched[-1].generation if launched else 0
        end = self.d.runtime.ended(rec.session_key)          # RuntimeUnavailable propagates: a hold
        if end is None or end.generation != latest:
            return Dead(rec.bead, rec.session_key, latest, True, f"generation {latest}: no end recorded")
        if end.cause in (EndCause.STOPPED, EndCause.LIFETIME) and end.ran_seconds is not None:
            return Dead(rec.bead, rec.session_key, latest, False, f"generation {latest} ended: {end.cause}")
        ran = "" if end.ran_seconds is None else f" after {end.ran_seconds}s"
        return Dead(rec.bead, rec.session_key, latest, True, f"generation {latest} died{ran}")


def open_resumes(parker: Parker, dead: tuple[Dead, ...]) -> int:
    j, name = parker.d.journal, parker.ws.name
    limit = parker.ws.limits.launch_failures_before_human
    escalations: list[Op] = []
    with j.transaction():
        for d in dead:
            if not d.crashed:
                j.crash_clear(name, d.bead)                  # a healthy end resets the run
            elif (n := j.crash_add(name, d.bead, d.session_key, d.generation)) >= limit:
                escalations.append(parker.escalation_for(d.bead, Reason.CRASH_LOOP, f"{d.detail}; {n} in a row"))
                continue
            op = j.op_open(OpKind.RESUME, name, d.bead, {"ref": ""})
            j.op_step(op.op_id, "unlabelled")
            j.set_state(name, d.bead, BeadState.RESUMING, Reason.SESSION_DEAD, d.detail)
    for esc in escalations:
        parker.handoff(esc)          # `_handoff` made public: cp("escalate.intent"), then the label
    return len(dead) - len(escalations)
```

- **Evidence is never inferred from absence.** No ended record, or a record for a generation other than the latest launched one, counts as a crash. That is the conservative direction: toward a human, never toward relaunching without limit. A record that can't be read is `RuntimeUnavailable`, which holds the workstream as everywhere else (plan 3 D9) and counts nothing.
- **Crash safety.** The count, the escalation's journal half and the resume operation commit together, before any launch. A crash before the commit leaves nothing: the next sweep reads the same ended record and decides the same way. After the commit, the bead has an open operation (RESUME or ESCALATE), so the sweep skips it and the operation's replay owns it. An ESCALATE cut short before its label is replayed like any open escalation (`replay_escalate`). The `(session_key, generation)` key makes a second count of one death impossible, even on a path that sees it twice.

## The bound and what happens at it

- **The bound is `Limits.launch_failures_before_human` (default 2).** No new key. Its comment already cites §9 reconcile and §10 agent crash, and the ADR gives one number for both. With 2, a session's first death is resumed. The second consecutive death makes the bead `needs-human` instead of resuming it.
- **The count is separate from the launch budget.** Refused launches stay on the operation's `attempts`, exactly as today. A resume whose launches are refused twice escalates `launch_failed`, and crash accounting doesn't change that.
- **At the bound:** `Parker.escalation_for` ends nothing, because the sweep only sees beads with no open operation. It opens an ESCALATE and adopts STUCK with a new fixed reason, `Reason.CRASH_LOOP = "crash_loop"`. The detail holds numbers only, never a path, for example `generation 7 died after 41s; 2 in a row`. Then `needs-human` goes on the bead. This is plan 3's escalation pattern (finding 11: journaled with the STUCK row in one transaction, then labelled). The only way out is `Parker.release` (plan 3 D19).

## When the count resets

The count resets when there is evidence that the session ran healthily, or when the operator steps in:

1. **A healthy end seen by the sweep:** the bead's latest generation ended `lifetime` (or `stopped`) after reaching `running`. `open_resumes` clears the count in the transaction that opens the resume. A lifetime end means the generation lived its whole lifetime (default 120 minutes) without dying, which is the strongest evidence of health the runtime has, and it recurs on every healthy bead.
2. **The operator's release** clears the count in `Parker.release`'s intent transaction, so a released bead gets the full bound again.
3. **A new claim** (`Scheduler.start_new`'s intent transaction, where a returned bead's old row is dropped) clears it. A bead given back to the queue and claimed again is new work.

Rejected options:

- **A run of N seconds.** This would need a new tunable, and any value is arbitrary. A slow crash loop (each run dying after N+1 seconds) would never be bounded.
- **A landed commit.** It is evidence of progress, not of health. A healthy session can run for hours without committing. A session can also commit and then die on every launch.

A park or a defer neither counts nor resets. `stop()` records `stopped`, but the bead is then PARKED or DEFERRED and never reaches `_running`, so the sweep doesn't see that end. A bead that died once, then parked and resumed, and then died again has died twice since its last proof of health, and it escalates. That is the intent.

## Interaction with other flows

- **Lifetime stop (plan 4 D13):** not a crash, and it resets the count. A session found dead after its deadline is `lifetime` through `_overdue`, as today, because the deadline watcher deleting the sandbox looks exactly like that.
- **Resume after a refused launch:** unchanged. That is the operation's own budget, and the record's `refused` end is never matched, because the sweep only compares against the latest *launched* generation.
- **Park, defer, undefer, release:** their stops record `stopped`. A release resets the count. An undefer or a resume of a parked bead launches a new generation, and its death counts against whatever count the bead already had.
- **Unlanded commits:** `sessions()` raises, the workstream holds, and the sweep never runs, so nothing is counted. After the operator resolves the hold by removing the session directory (`docs/wsd.md` §5, step 4), `ended()` returns None, and the next resume counts one death. That is acceptable at the default bound, and it is noted in §5 (see Open question 3).
- **Handoff or swap to another profile:** it gets a new session key, but the count is per bead, so a swap does not reset it. The only swaps today are operator actions through release, which does reset it.
- **Restarts:** the rows live in the journal, so they survive wsd restarts and reboots. A lost journal escalates the bead `journal_lost` anyway (plan 3 D8).
- **Recovery never launches:** this is unchanged. The deferred sweep collects `Dead` values, and `open_resumes` runs once at the end of the pass.

## The setup default

I recommend a separate step, after this lands, with Liam's go. The change itself is one line: `platform._BACKENDS["linux"]["sandbox"] = "openshell"`. `heterodyne setup` never overwrites an existing `config.toml`, so it affects only new hosts. It still changes what a fresh install does: wsd would then build the OpenShell runtime, which needs a prepared host (OpenShell 0.1.2, rootless podman 5, the image, `ptrace_scope = 1`). Until the host is prepared, workstreams hold `runtime_unavailable` or wsd fails its config check. That is an operator-facing decision, not part of crash accounting.

## Tests

`tests/test_sandbox_runtime.py`:

1. `test_end_records_how_a_session_ended`: `stop()` records `stopped`. `expire()` at the deadline records `lifetime`. A pane killed in the fake backend and then found by `sessions()` records `died`. One found dead past its deadline records `lifetime`. A refused launch records `refused`. Each sets `ended_at`.
2. `test_the_first_end_cause_stands`: a record already in `stopping` with `stopped` is found dead and stays `stopped`. One with `died` that is then overdue becomes `lifetime` (the WIP rule).
3. `test_ended_reports_the_latest_generation`: `ended()` gives `None` before the record is ended, the generation, cause and `ran_seconds` once it is, `DIED` for a pre-change ended record with no cause, and `RuntimeUnavailable` for an unreadable record.

`tests/test_wsd_journal.py`:

4. `test_crash_rows_count_per_bead_and_once_per_generation`: adding the same generation twice counts 1. Another bead's rows are not counted. A workstream or bead name containing `_` doesn't match its neighbours. `crash_clear` removes only that bead's rows. The schema check still passes on a journal with crash rows.

`tests/test_wsd_sweep.py`:

5. `test_a_session_that_dies_on_every_launch_becomes_needs_human`: the first death is resumed (row RESUMING / `session_dead`, a new generation launched). The second death is STUCK / `crash_loop` with `needs-human` and no third launch. The runtime's `launches` has exactly 2 entries.
6. `test_a_lifetime_end_is_not_a_crash_and_resets_the_count`: death, then a resume, then a lifetime end, then a resume, then a death: still resumed. The count is 1, not 2.
7. `test_missing_end_evidence_counts_as_a_crash`: `ended()` gives None, or a generation other than the latest launched one. The death is counted.
8. `test_unreadable_end_evidence_holds_and_counts_nothing`: `ended()` raises. The workstream holds `runtime_unavailable`, no resume is opened, and no row is written.
9. `test_crash_at_every_point_counts_once`, parameterized over a crash inside `open_resumes`' transaction, at `escalate.intent` and at `escalate.labelled!`, in pickup and in recovery: after replay, exactly one row per generation, at most one ESCALATE, and `needs-human` present once.
10. `test_the_count_survives_a_restart`: one death, then a restart (a new `Wsd` on the same journal), then a second death escalates.

`tests/test_wsd_park.py`:

11. `test_release_resets_the_crash_count`: a `crash_loop` bead is released, and its next death is resumed, not escalated.
12. `test_a_park_neither_counts_nor_resets`: death (count 1), then park and resume, then a death, which escalates.
13. `test_refused_launches_keep_their_own_budget`: two refused launches still escalate `launch_failed` with no crash rows.

`tests/test_wsd_pickup.py`:

14. `test_a_new_claim_starts_with_no_crashes`: a bead returned to the queue with crash rows is claimed again, and the rows are gone in the claim's transaction.

Existing tests that call `FakeRuntime.end(key)` twice in a row on one bead and expect two relaunches, and the never-idle property, are audited. Each either expects `crash_loop` at the second death or ends with `cause=LIFETIME`. The property's oracle accepts STUCK for a bead whose fault is "dies at once".

## Docs

- `docs/wsd.md` §1: `launch_failures_before_human` also bounds consecutive deaths of a bead's session (resets: a lifetime end, a release, a new claim).
- `docs/wsd.md` §5, "Enabling it": drop "wait for crash-loop accounting". Add a short paragraph: a session found dead before its deadline counts against the bead; the second death in a row (by default) makes the bead `needs-human` with `crash_loop`; and the record's `stop_reason` says how each generation ended. Step 4 of the unlanded procedure notes that the next resume counts one death.
- `docs/wsd.md` §6: replace the "Plan 4 must add persistent crash-loop accounting…" sentences with the `ended()` seam. Keep "setup does not select it" until the setup step is approved.
- `docs/install.md` (line 12): drop "should wait for crash-loop accounting (btq-g08sd) first". Keep the explicit operator edit until the setup step lands.
- The plan-3 doc's P1 is not edited (it is a record). The plan-4 doc gets a row pointing here.

## Open questions for Liam

1. **Setup default.** Should `heterodyne setup` record `openshell` on Linux once this lands? Recommendation: yes, but as its own small bead after this merges, with your go. It changes only fresh installs, but those then need host preparation before any agent runs.
2. **Host restarts.** A reboot is counted as a death, so two reboots without a lifetime end between them escalate a healthy bead. Recommendation: accept this for now. It is rare, a release clears it, and telling a reboot apart (recording `boot_id` at `running`) would let a workload that crashes the host loop without limit.
3. **Operator removal after unlanded.** The next resume counts one death. Recommendation: accept this and document it, rather than add an operator-reset path before plan 6's commands exist.
4. **A separate limit.** Should crashes get their own `wsd.crashes_before_human`? Recommendation: no. The ADR names one number for both, and a new key can be split out later without a migration.
