# btq-7hokw: outstanding-create evidence across teardown and replacement

Bead: btq-7hokw, the Plan 4 Task 12 follow-up. Base: main 7cacb83. ADR 0001 r16 Amendment 1 (design repo `b2c9895`). The finding is Codex T12 code review r2, finding 1, path 2 (`docs/reviews/p4-t12-code-r2.md`), which the controller deferred here at cycle 3. Review r3 (`docs/reviews/p4-t12-code-r3.md`) approved Task 12 with this deferral.

**Status:** draft r2 for review. It needs a cross-model design review, then operator approval, before any code.

**Revision r2.** This answers design review r1 (REVISE, 2026-10-14), which found three blocking issues and one non-blocking one:
1. **Settlement isn't teardown.** Settling a create no longer ends protection. Each create now has a third piece of evidence, `<name>.gone.json`, a confirmed deletion made after the create settled.
   - A watcher is needed until an entry has both its settlement and its gone record.
   - Cleanup needs both, and a watcher confirmed no longer running.
   - A settled sandbox that hasn't been torn down gets its lifetime backstop back after a lost watcher.
2. **Watcher liveness.**
   - A watcher counts as running only if its session is on tmux **and** its pane isn't dead. The cause is `remain-on-exit`, which keeps a dead watcher's session name.
   - Every start or re-arm waits for a readiness file the new watcher writes after its startup, and checks its pane again.
   - If protection can't be confirmed, the result is `RuntimeUnavailable`.
3. **No automatic `observed` settlement.**
   - `observed` is now evidence in a file of its own, `<name>.observed.json`. It never settles anything.
   - Assumption A1 is withdrawn, and a timed-out create settles only through the operator.
   - Because the settlement file holds only settling kinds, an observation can't take the first-writer slot an operator settlement needs.
4. **Durable unlink order** (non-blocking). Cleanup fsyncs the directory after every unlink, in a fixed order.

The review also noted that R2's crash explanation was reversed. That is corrected below.

**The ADR.** §7 says a session stops before its login expires, and the lifetime backstop (plan 4 D13) is plan-level. This design needs no ADR change. Two clarifications are listed under "ADR open items" and are not edited here.

## Problem

`backend.create` (`sandbox/openshell.py`) runs `openshell sandbox create` with a 300 s timeout, then waits up to 60 s for the sandbox to answer. If the command is killed, times out or fails, OpenShell gives no proof that the request won't still complete on the gateway. So a sandbox, with this generation's login bound in, may appear later.

The watcher (`sandbox/reaper.py`, the `wsd-<sid>-r<gen>` pane) is the only thing that catches that. It exits once a deletion is confirmed, `LINGER_SECONDS` (72 h) have passed since the deadline, and `settled(record, name)` is true. Today `settled` reads the session record (`<state>/sessions/<sid>/session.json`). It is true when:

1. `created` is true (the create returned);
2. `create_started` is not true (nothing was submitted);
3. `phase == "ended"`;
4. the record names another sandbox (a later generation replaced it);
5. the record is missing (`FileNotFoundError`).

Rules 3 to 5 don't prove anything about an outstanding create:

- **Teardown (r2 path 2a).** wsd dies inside the create, or the create times out. A restarted wsd's `sessions()` finds the sandbox absent, and `_end` records `ended`. `_end` keeps the watcher (`created` is false), but rule 3 already reads the record as settled, so the watcher exits at deadline + 72 h. A create that lands after that runs unwatched. `test_a_crash_during_the_create_leaves_a_watcher_that_waits_for_the_record` (`tests/test_wsd_lifetime.py`) asserts exactly this.
- **Replacement (r2 path 2b).** The next launch of the session key writes a new record for generation N+1. Rule 4 settles generation N's watcher at once.
- **Removal.** A resolve (`wsctl resolve`, btq-xb56t) moves the session directory to trash and removes it, and rule 5 then settles. `_running` refuses a resolve while any reaper of the session is on tmux, so today this happens only after the watcher has gone. Even so, the evidence lives in a directory that a later step deletes.
- **Reboot.** A host reboot kills wsd's tmux server and every watcher with it. Nothing starts them again, so every outstanding create is unwatched from then on, and so is every running sandbox's lifetime backstop.
- **A dead watcher looks alive.** `Tmux.new_session` sets `remain-on-exit on` (`src/heterodyne/tmux.py`, `new_session`). So a watcher that crashed keeps its session name, with a dead pane. A presence check alone would read it as watching.

Path 1 of the same finding (a stall between the deadline check and the `create_started` write) was fixed in 7880c46 and is unchanged here.

## Evidence: a create ledger outside the session directory

A new directory, `<state>/creates/`, is mode 0700, owned by wsd, and outside every bind and every session directory. Only wsd's own cleanup removes anything from it (below). Each create has up to four files, and each one is written once and never rewritten:

- **`<name>.json`, the submission record.** `<name>` is the sandbox name (`hz<12 hex>g<gen>`, `NAME` in `spec.py`).
  - It holds `{"sandbox", "key", "ws", "bead", "generation", "deadline", "pane", "at"}`. `pane` is the agent's tmux session, which the watcher's `mark_live` needs (g08sd).
  - It is written before the record's `create_started` write.
- **`<name>.settled.json`, the settlement.** It holds `{"sandbox", "how", "at"}`, plus `"by"` for an operator settlement. `how` is one of three kinds:
  - `returned`: `backend.create` returned. This is the same evidence as today's `created`.
  - `never_submitted`: the launch stopped at its deadline check, before the create was submitted (the T11 r3 path).
  - `operator`: the operator settled it after the escalation (below).
- **`<name>.gone.json`, the teardown.** It holds `{"sandbox", "by", "at"}`, where `by` is `teardown`, `watcher` or `sweep`.
  - It records a deletion the backend confirmed **after** the settlement was durable. The writer reads a valid settlement first, then confirms the deletion, then writes this file.
  - A deletion confirmed while the create was unsettled is never written here, because a late create could follow it.
  - A `never_submitted` entry counts as gone without this file, since no sandbox could exist.
- **`<name>.observed.json`, an observation (evidence only).** It holds `{"sandbox", "by", "at"}`. It is written the first time the backend lists the sandbox, before that sandbox is deleted, by the writer named in `by`.
  - It settles nothing.
  - It is shown to the operator in the escalation and by `wsctl sandbox settle`, as the evidence they can weigh.

Every file uses `reaper.write_once`'s procedure. It writes a private temporary file and fsyncs it, then links it to the final name, keeping an existing file so the first writer wins, then fsyncs the directory. The settlement file can hold only a settling kind, so the first settlement that wins is always a real one.

**The readers.** Each reader opens its file with `O_NOFOLLOW | O_NONBLOCK`, reads at most 4 KiB, and requires a regular file holding a JSON object whose `sandbox` is `name`. Anything else counts as absent: a missing file, a link, a FIFO, an oversize file, malformed JSON, or another sandbox.
- `settled(ledger, name)` also requires `how` to be one of the three kinds.
- `gone(ledger, name)` also requires a valid settlement, or else the file means nothing.

An entry is in one of three states:

| State | Files | Needs a watcher? |
|---|---|---|
| **unsettled** | a submission, no valid settlement | yes, without limit |
| **settled** | a valid settlement, not gone | yes, as the lifetime backstop, until gone |
| **done** | settled and gone (or `never_submitted`) | no; cleanup removes it |

So no record phase, replacement or removal changes an entry's state. Damage to the ledger makes an entry need a watcher; it never makes one done (fail closed).

**Why it is trusted.** `<state>/creates/` is never bound into any sandbox, the same as the session record (`docs/wsd.md` §5). It is written only by wsd, the watchers, and `wsctl` through wsd's control socket.

## Who writes what

In `_start` (`sandbox/runtime.py`), in this order:

1. The submission record is written. A write failure is a `_StepFailed`, raised before anything is submitted.
2. The record gets `seeded`, `create_started` and the deadline (unchanged).
3. The watcher is armed (see "Arming a watcher"). An arm that can't be confirmed is a `_StepFailed`, so nothing is submitted without a confirmed watcher.
4. If the deadline has passed, the settlement is written with `never_submitted`, then the record gets `create_started=False` (unchanged), and the launch fails.
5. `backend.create`.
6. The settlement is written with `returned`, then the record gets `created=True`.

**Recovery** (`_end`, from `sessions()`, `launch()`'s cleanup of `prev` and `_fail`) settles only from the record of the same generation, that is, a record whose `sandbox` equals the entry's:

- **R1.** If `create_started` is false, the record says nothing was submitted. The create is submitted only after `create_started=True` and the deadline check, and the only path that writes it false again is step 4. So this proves `never_submitted`.
- **R2.** If `created` is true, the create returned, which proves `returned`.
  - Step 6 writes the settlement before `created`, so a crash between those two writes leaves the settlement present and `created` false: the entry is already settled, and R2 isn't needed for it.
  - R2 covers two other cases: a record from before the ledger existed (Migration), and a settlement write that failed in step 6 after the create returned.
  - In the second case the launch goes on. `created=True` is written as today, and the next recovery pass writes the settlement from R2.
- Any other state leaves the entry unsettled. In particular, `ended` alone, a record of another generation, or no record never settles anything.

**Teardown.** `gone` is written only from a confirmed deletion that follows a valid settlement:

- `_end`. When `_teardown` returns confirmed and the entry is settled, `_end` writes `gone` (`by: teardown`) **before** it kills the watcher, as it does today when `created`. If the entry isn't settled, the watcher stays, as today.
- The watcher. When its exit rule holds (below), it writes `gone` (`by: watcher`) before it returns.
- `sessions()`, for a settled entry that isn't gone, whose record is ended, of another generation, or missing. It asks the backend to delete the sandbox, and if that is confirmed it writes `gone` (`by: sweep`). The evidence is the same as `_teardown`'s, so a settled entry whose session has ended doesn't keep a watcher until its linger.

If a `gone` write fails, the watcher stays, and the next pass retries it. Nothing becomes done without the file.

**Observed.** Before deleting a sandbox the backend listed, whoever deletes it writes `observed`:

- the watcher, at each pass: `names()` first, then `observed` if the name is listed, then kill and delete as today;
- `_teardown`, for each of `mine` (the session's listed sandboxes), before its `delete`;
- the stale-generation sweep (below).

If the `observed` write fails, the deletion still goes ahead, because deleting is always safe. A listing that fails writes nothing. Being evidence, `observed` is written once (the first observation). Later observations aren't recorded, and the escalation says it was observed at least once.

**The stale-generation sweep.** Before this design, `sessions()` ignored a listed sandbox `<sid>g<M>` whose session record names a different generation, because its short ID is "known".
- A new generation launches only after the earlier one's teardown has confirmed that no sandbox of the key exists, so any such sandbox is a late create.
- `sessions()` now writes `observed` for it and deletes it.
- A delete that isn't confirmed raises `RuntimeUnavailable` ("a late sandbox of an earlier generation can't be deleted"). That holds the workstream until a later pass confirms the sandbox gone. Its watcher still covers it meanwhile.
- A sweep deletion of an unsettled entry is never `gone`.

## The watcher

`reaper.main` gains three arguments: `--ledger <state>/creates`, `--alerts <state>/alerts`, and `--ready <path>` (below). `settled` and `gone` read the ledger as above. `--record` stays, for `mark_live` only (g08sd).

`reap` keeps its loop. Its exit rule becomes: a confirmed deletion, `clock() >= deadline + LINGER_SECONDS`, and `settled()`. Then it writes `gone` and returns. If the `gone` write fails, it doesn't return, and the next pass tries again. Three things change:

- **No exit while unsettled.** Rules 3 to 5 are gone, so a watcher whose create is unsettled never exits. It keeps killing and deleting every `POLL_SECONDS` (30 s) for as long as it runs. A create that lands at any time is deleted within one pass.
- **The escalation.** At the first pass at or after `deadline + LINGER_SECONDS` with `settled()` false, the watcher writes one alert, `<state>/alerts/create-<name>.json`.
  - The format is admind's alert-file contract (`admind/alerts.py`): a dotfile first, then a rename, with `{"id", "created_at", "text"}`.
  - The text names the sandbox, its session's short ID, the generation, the workstream and the bead.
  - It says that the create's outcome is unknown, and that the watcher keeps deleting the sandbox.
  - It says whether the sandbox was ever observed, and when. This is evidence for the operator, not a settlement.
  - It gives the command that settles the entry, `wsctl sandbox settle WS SESSION --generation N`.

  Every value in the text is checked against admind's sensitivity scan (`config.secret_scan`). A short ID is 14 characters, never 64-hex. A failed write is retried at every later pass until it succeeds. The escalation never ends the watch. It is written once: an existing file is kept.
- **The close-out.** When an escalated entry later becomes done, the watcher writes `create-<name>-settled.json` ("settled: `<how>`; deleted") before it exits, so the operator sees the end of what they were told about.

**Readiness.** After it has parsed its arguments, built its backend and opened the ledger, and before its first sleep, `main` writes the file named by `--ready` with `write_once`. The file goes in `<state>/creates/` as `.ready-<name>-<nonce>`, where the nonce is from the arming call. A watcher that crashes on startup (a bad argument, an import failure, a ledger it can't open) never writes it.

`LINGER_SECONDS` keeps its value and its comment's reasoning, but its meaning narrows. It is no longer how long a watcher may wait. It is how long a watcher waits for a settled create to finish deleting, and the point at which an unsettled one escalates.

## Arming a watcher, and checking it runs

**Running** means `has_session(name)` is true **and** `pane_dead(name)` is false. A session with a dead pane (`remain-on-exit`) is not running. A failing `has_session`/`pane_dead` call (TmuxError) proves nothing.

`_arm(entry)` is used by `_start` step 3, by the re-arm below, and by `launch()`. It works in this order:

1. If the session is present with a dead pane, it is killed (`kill-session`, our own reaper name). Then `session_absent(name)` must confirm it gone; if it can't, that is `RuntimeUnavailable`.
2. `new_session(name, …)`, with `--ready .ready-<name>-<nonce>`.
3. **Startup is confirmed** once the ready file exists and the pane is running. The arm waits for that condition, polling every 0.2 s up to `ARM_READY_SECONDS` (10 s) on the injected clock.
4. The ready file is removed, and the directory is fsynced.
5. If the condition isn't met in time, or the pane is dead once the file appears, the session is killed and the arm raises `RuntimeUnavailable` ("the watcher of `<name>` did not start"). The entry is unchanged, so the next pass tries again.

**What this proves and what it doesn't.** It proves the watcher got past startup and that its process was alive afterwards. Every `sessions()` pass checks running again, so a watcher that dies later is found within one pickup backstop (60 s) and re-armed. A watcher that hangs while its pane stays alive isn't detected. That is an accepted residual, listed under "Policy".

## Recovery: watchers are re-armed from the ledger

A watcher that is gone or dead (a reboot, a killed tmux server, an operator's `tmux kill-session`, a crash) is armed again from the ledger. `sessions(ws)` runs at every pickup and reconcile, and wsd runs it at startup. Before anything else, it does this for each entry of the workstream that is unsettled or settled:

- If `wsd-<sid>-r<gen>` isn't running, it calls `_arm` with the entry's sandbox name, generation, deadline and pane, and with the session's record and marker paths, whether or not they exist. If they don't exist, `mark_live` writes no marker, as today.
  - For a **settled** entry this restores the lifetime backstop: a running session's sandbox is still killed and deleted at its deadline.
  - For an **unsettled** one it restores the late-create watch.
- A tmux listing that fails, a liveness check that fails, an unreadable ledger, or an arm that fails raises `RuntimeUnavailable`, and the workstream holds. Nothing is assumed to be watched.

`launch()` does the same for its own key before it writes the new record, so a replacement generation never starts while an earlier generation's entry is unwatched.

**Cleanup.** In the same pass, wsd removes the files of a **done** entry once its watcher is confirmed not running. If its session is present with a dead pane, the session is killed first and `session_absent` must confirm it. Each unlink is followed by an fsync of its directory, in this order:

1. the alerts (`create-<name>.json`, `create-<name>-settled.json`), then fsync `<state>/alerts/`;
2. `<name>.json` (the submission), then fsync `<state>/creates/`;
3. `<name>.observed.json`, then fsync;
4. `<name>.gone.json`, then fsync;
5. `<name>.settled.json`, then fsync.

**Why this order.** The fsyncs make the order durable across a power loss, not only across a process crash. At any point the directory then holds one of three things:
- a done entry, possibly without its submission, which cleanup finishes;
- a settlement alone, which counts as gone for `never_submitted` and is otherwise a settled entry without a submission;
- nothing.

It never holds a submission without its settlement, which would be re-armed and refuse resolve for nothing. A settlement or gone record without a submission is removed by the next cleanup. For a non-`never_submitted` settlement without `gone`, the sweep confirms the deletion first, and an unconfirmed deletion makes no file count as done.

**Resolve.** `_running(sid)` also refuses while the session has an entry that is not done ("an earlier create of generation N is not settled and deleted"), whether or not its watcher is running. An unreadable ledger is `RuntimeUnavailable`, as a failed listing is today.

## After LINGER_SECONDS: fail closed, then escalate

Nothing is assumed about a create that hasn't settled, however long ago it was submitted:

- its watcher stays (and is re-armed if it is lost or dies), and every late sandbox is deleted within one pass;
- the operator gets one alert at `deadline + LINGER_SECONDS`, showing the observation evidence if there is any;
- the session's directory can't be resolved away until the entry is done;
- the entry settles only on evidence (`returned` or `never_submitted`), or on the operator's decision. An observation is never enough.

The workstream is not held only because a create is unsettled. Its watcher is the safety, and holding the queue would add nothing to it. The workstream holds only when that safety can't be confirmed: the watcher can't be listed, isn't running and can't be re-armed, or the ledger can't be read.

**The operator's settlement.** `wsctl sandbox settle WS SESSION --generation N` is a new wsd control operation. It writes `operator`, with `by` naming `wsctl`. It refuses:

- before `deadline + LINGER_SECONDS` ("its watcher is still within its linger"), so it can't cut a linger short;
- when the entry is unknown, or is already settled by another kind. A replay of the same request on an `operator` entry succeeds and changes nothing;
- when the backend lists the sandbox, or the listing fails ("the sandbox may exist; its watcher will delete it first").

Its reply shows the observation evidence: whether the sandbox was ever observed, and by whom and when, or "never observed". This is the operator accepting that no late create will land, after checking OpenShell. `docs/wsd.md` says what to check: the gateway's sandbox list, and whether the gateway has restarted since the submission. After the settlement, the watcher's next confirmed deletion writes `gone` and it exits; cleanup then removes the entry.

## Migration

The first wsd start after this lands handles each session record as follows, if the ledger has no entry for it:
- `create_started` true and `created` false, in any phase: it writes a submission record. The entry is unsettled.
- `created` true, in a phase other than `ended`: it writes a submission record and a `returned` settlement (R2). The entry is settled, so its running sandbox keeps a backstop.
- An `ended` record with `created` true, or a record with `create_started` false: it writes nothing. Their watchers, under the old rules, end as before.

Watchers started before the upgrade still read the record under the old rules, and they have no readiness file. Each is checked for running (session and pane) like any other. When one exits or is found dead, the next `sessions()` pass re-arms a new watcher for its entry.

**Accepted residual:**
- A create whose record was already replaced or removed before the upgrade has no evidence left, so it can't be migrated.
- A late create can land in the window between an old watcher's exit and the next pass. That window is at most the 60 s pickup backstop, and the sandbox is deleted on that pass.

## Policy decided here (not in the ADR)

1. An `ended` record, a replacement generation, a removed record and an observation never settle a create.
2. A create settles only on `returned`, `never_submitted` or `operator`.
3. An entry needs a watcher until it is settled **and** its sandbox's deletion was confirmed after settlement. Settlement alone ends nothing.
4. Past `LINGER_SECONDS`, an unsettled create's watcher stays without limit, and the operator is alerted once.
5. The ledger is authoritative for watchers. A watcher counts only if its session is present and its pane alive. A missing or dead watcher is re-armed, a re-arm counts only once the new watcher has confirmed its startup, and a watcher that can't be confirmed is a hold.
6. **Load.** Each unsettled create keeps one watcher process, polling every 30 s. Every timed-out create now ends in an operator step, since an observation no longer settles. Their number is bounded by the launch-failure budget: a create that times out is a refused or uncertain launch, and the bead escalates once `launch_failures_before_human` is reached.
7. **Residual: a hung watcher.** A watcher process that hangs while its pane stays alive isn't detected. A heartbeat check would close this, and is filed as a follow-up.

**A1 is withdrawn.** r1 assumed that one `openshell sandbox create` request makes at most one sandbox, and that once listed and deleted it never makes it again. Review r1 found that neither the backend wrapper nor the ADR establishes this, including across a gateway recovery, and a live example can't prove it. `observed` is therefore evidence only. If the pinned OpenShell implementation is later shown to give a completion guarantee, that becomes a settling kind in its own bead ("Out of scope").

## ADR open items (not edited here)

1. **Alert writer.** ADR §6.2 says `wsd` writes the local alert file. Here a watcher writes it: a wsd-owned process in wsd's tmux server, so that the alert doesn't depend on wsd running. A clarification would read "wsd or one of its watchers".
2. **Accepted risk on an unknown create.** §7's residual-risk list doesn't name a create whose outcome OpenShell can't report. A clarification would add it:
   - such a create is watched without limit;
   - it is settled only by the operator, after an alert, on their own check of OpenShell, and possibly after the sandbox was observed;
   - protection has gaps: from a watcher's death to the next `sessions()` pass, while `RuntimeUnavailable` holds the workstream because a watcher can't be armed, and while a watcher is hung;
   - and the operator's override is accepted.

Neither changes behavior the ADR requires, so implementation doesn't wait on them.

## Tests (offline)

All of these run with the existing fakes: `tests/test_wsd_lifetime.py`'s injected clock, sleep and delete, and `RuntimeRig`'s fake backend and tmux. The fake tmux gains `pane_dead` per session, and a scripted watcher that writes or skips its ready file. None needs OpenShell, podman or the network.

`tests/test_sandbox_reaper.py`:

1. `test_the_ledger_states_come_only_from_valid_files`, parameterized:
   - Each of these gives `settled` False: no files, a submission only, a settlement for another sandbox, an unknown `how` (including `"observed"`), malformed JSON, a JSON list, a symlink, a FIFO (the read must not block), and an oversize file.
   - Each of the three kinds gives True. A settlement without its submission record also gives True.
   - `gone` is False without a valid settlement, even if `<name>.gone.json` is valid. An `observed` file changes neither `settled` nor `gone`.
2. `test_an_unsettled_watcher_never_exits_and_escalates_once`. The clock is driven to 3 × `LINGER_SECONDS`, deletion is always confirmed, and nothing settles. The watcher is still looping, exactly one `create-<name>.json` exists, and every pass killed and deleted.
3. `test_a_late_create_is_deleted_and_observed_but_never_settled`. The sandbox appears at 2 × `LINGER_SECONDS`. The next pass writes `observed` before its delete, and the watcher keeps looping. An operator settlement afterwards wins, even though `observed` was written first. The watcher then writes `gone`, writes the close-out, and exits.
4. `test_the_alert_meets_admind_contract`. `admind.alerts.scan` reads the escalation (with and without observation evidence) and the close-out as valid, neither malformed nor withheld. A failed alert write (a read-only directory) is retried at every pass and never ends the watch.
5. `test_an_observed_write_failure_still_deletes`.
6. `test_a_failed_gone_write_keeps_the_watcher` (the exit rule holds, but the write fails: it doesn't return, and it retries).
7. `test_the_watcher_writes_its_ready_file_after_startup_only`. `--ready` is written after argument parsing and ledger opening. A bad argument or a ledger that can't be opened writes none.
8. Extended `test_the_reaper_reads_the_argv_the_backend_names`: `--ledger`, `--alerts` and `--ready` reach `settled`/`gone`, the escalation and readiness, and `--record` reaches only `mark_live`.

`tests/test_wsd_lifetime.py` (the runtime, with `RuntimeRig`):

9. Replaced `test_a_crash_during_the_create_leaves_a_watcher_that_waits_for_the_record` (r2 path 2a). wsd crashes inside the create, and the restarted wsd's `sessions()` records `ended`. Now `settled` is False, and the watcher is still running past 2 × `LINGER_SECONDS`. It deletes a create that lands then, and keeps running after `observed`, until an operator settlement.
10. `test_a_replacement_generation_never_settles_the_earlier_create` (r2 path 2b). Generation 1's create times out (the fake backend records it pending), and generation 2 launches and runs. Generation 1's entry stays unsettled and its watcher stays. A late `g1` sandbox:
    - while generation 2 runs, is deleted by the stale-generation sweep, with `observed` written first, and stays unsettled;
    - after generation 2 has also ended, is deleted by the watcher.
11. `test_a_running_sessions_backstop_is_restored_after_a_lost_watcher` (review r1 finding 1, case a). A launch returns (settled `returned`), the session runs, and the tmux server is killed (a reboot). The next `sessions()` re-arms the watcher with the entry's deadline and pane. At the deadline, the sandbox is killed and deleted, and `mark_live` records it as live. The entry is never cleaned up while it is settled but not gone.
12. `test_a_crash_after_observed_before_delete_leaves_it_watched` (review r1 finding 1, case b). The watcher (or `_teardown`) writes `observed` and crashes before the delete, so the sandbox is still listed. The record is `ended`, which `sessions()` skips as a session. The entry is still unsettled, so it is re-armed and the sandbox deleted on the next pass. No cleanup happens.
13. `test_teardown_writes_gone_only_after_settlement_and_confirmed_delete`. `_end` of a returned session writes `gone` before it kills the watcher. An unconfirmed `_teardown` writes nothing and keeps the watcher. An unsettled entry's confirmed teardown writes no `gone`.
14. `test_the_sweep_finishes_a_settled_entry_of_an_ended_session`. A settled entry, not gone, whose record is ended or missing: a confirmed delete writes `gone` (`by: sweep`), and cleanup follows. An unconfirmed delete leaves it settled and watched.
15. `test_a_resolve_waits_until_the_entry_is_done`. An unlanded session with an unsettled entry, and its watcher killed. `wsctl resolve` refuses with the new reason, and still refuses after `observed` alone. After an operator settlement plus `gone`, it goes ahead. The session directory's removal never changes the entry.
16. `test_ledger_writes_come_in_order`. A spy on the record writes, the ledger writes and the arm shows this order: submission record, `create_started`, the arm confirmed, `returned`, `created`. For the deadline path it shows `never_submitted` before `create_started=False`. A failed submission write, or an arm that can't be confirmed, submits nothing.
17. `test_recovery_settles_only_from_the_same_generation`, parameterized over a crash at each step of `_start`.
    - A crash between step 6's writes leaves the settlement and `created` false, and the entry is settled.
    - A failed step-6 settlement write followed by `created=True` is settled by R2 on the next pass.
    - R1 settles as stated.
    - An `ended` record of the same generation, with `create_started` true and `created` false, stays unsettled.
    - A record of another generation, or none, settles nothing.
18. `test_a_dead_watcher_pane_is_not_a_watcher` (review r1 finding 2). The watcher's pane is dead but its session is retained (`remain-on-exit`). `sessions()` kills the dead session, re-arms, and confirms the new watcher's readiness. A failing `pane_dead` call raises `RuntimeUnavailable`.
19. `test_a_watcher_that_fails_at_startup_holds` (review r1 finding 2). The scripted watcher exits at once, without its ready file, or writes the file and dies. The arm kills the session and raises `RuntimeUnavailable`, and the entry is unchanged. In `_start`, that is a `_StepFailed` with nothing submitted. A later pass whose arm succeeds clears the hold.
20. `test_a_lost_watcher_is_rearmed`. The watcher's tmux session is killed (a simulated reboot), and `sessions()` arms it again with the entry's name, generation, deadline and pane. A failing tmux listing, or an arm that fails, raises `RuntimeUnavailable` and holds the workstream. `launch()` of the same key re-arms before it writes the new record.
21. `test_cleanup_order_is_durable` (review r1 finding 4). A spy on `os.unlink` and `os.fsync` (the directory fds) shows each unlink followed by an fsync of its directory, in the order given under "Cleanup". A crash injected after each unlink, before its fsync and after it, leads the next pass to re-arm nothing for a done entry and to finish the cleanup. An entry that is unsettled, or settled and not gone, is never removed.
22. `test_the_operator_settles_only_after_the_linger_and_when_absent`:
    - refused before `deadline + LINGER_SECONDS`, while the sandbox is listed, and when the listing fails;
    - accepted afterwards, with the reply showing the observation evidence or "never observed"; the watcher then writes `gone` and exits at its next confirmed deletion;
    - a replay is a no-op, and an unknown entry, or one already settled by another kind, refuses.
23. `test_migration_writes_entries_for_outstanding_records`:
    - records with `create_started` true and `created` false, in `creating`, `stopping` and `ended`, gain unsettled entries;
    - a running record with `created` true gains a settled entry and a re-armed backstop;
    - others gain nothing;
    - a second start writes nothing.
24. `test_teardown_observes_before_it_deletes`. `_teardown` writes `observed` for each listed sandbox of the key before its `delete`, and that settles nothing.
25. Unchanged and still passing: `test_a_launch_stalled_past_its_deadline_creates_nothing`, now also asserting `never_submitted`, and `test_a_stall_after_the_deadline_check_never_lets_the_watcher_go`.

**Static checks:** Ruff and Pyright on the touched modules, and a test that `<state>/creates/` is outside every bind of `build_spec`, as g08sd's test 4c does for the marker.

## Docs

- `docs/wsd.md` §5, the launch paragraph: the create ledger and its three states, what settles a create, what makes it done, the watcher's escalation, what counts as a running watcher, and re-arming.
- `docs/wsd.md` command table: `wsctl sandbox settle`, with what to check first and what the observation evidence means and doesn't mean.
- `docs/wsd.md` §5, the resolve procedure: the new refusal.
- The `reaper.py` module docstring and the `settled`/`gone` docstrings: the rules above, replacing record-based settlement.

## Out of scope

- **A completion or cancellation proof from OpenShell.** If the pinned OpenShell can be shown to report a create's outcome, cancel it, or never recreate a sandbox that was listed and deleted, that becomes a fourth settling kind, in its own bead. A live check (create, kill the CLI, wait for the landing, delete, watch it stay gone, across a gateway restart) belongs to that bead's evidence, not this one.
- **A watcher heartbeat** (Policy 7), filed as a follow-up.
- **Marmot access to `wsctl sandbox settle`.** It is a host command, like `wsctl resolve` today. Under Marmot-only operation it needs an admind command, or the admin agent's passthrough, as `wsctl release` gets in plan 5i part 2. That is filed as a follow-up and kept consistent with whatever resolve gets.

## Open questions for Liam

1. **Every timed-out create ends in an operator settlement.** This follows from withdrawing A1, as review r1 recommended. Recommendation: accept it for the PoC. Timed-out creates are rare and each one escalates once. The watcher deletes any late sandbox meanwhile.
2. **The watcher stays without limit past the linger.** The alternative is that the watcher exits after some multiple of the linger and the workstream holds instead. Recommendation: stay without limit. The watcher is what deletes a late sandbox, and a hold would stop work without deleting anything. Review r1 agreed.
3. **`wsctl sandbox settle` stays host-only for now** (see "Out of scope"). Recommendation: yes, with the late-create risk accepted explicitly as in "ADR open items" 2. Review r1 agreed.
