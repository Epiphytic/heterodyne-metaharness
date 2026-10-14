# btq-7hokw: outstanding-create evidence across teardown and replacement

Bead: btq-7hokw, the Plan 4 Task 12 follow-up. Base: main 7cacb83. ADR 0001 r16 Amendment 1 (design repo `b2c9895`). The finding is Codex T12 code review r2, finding 1, path 2 (`docs/reviews/p4-t12-code-r2.md`), which the controller deferred here at cycle 3. Review r3 (`docs/reviews/p4-t12-code-r3.md`) approved Task 12 with this deferral.

**Status:** draft for review. It needs a cross-model design review, then operator approval, before any code.

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
- **Removal.** A resolve (`wsctl resolve`, btq-xb56t) moves the session directory to trash and removes it. Rule 5 then settles. `_running` refuses a resolve while any reaper of the session is on tmux, so today this only happens once the watcher has already gone. But it means the evidence lives in a directory that a later step deletes.
- **Reboot.** A host reboot kills wsd's tmux server and every watcher with it. Nothing starts them again, so every outstanding create is unwatched from then on.

Path 1 of the same finding (a stall between the deadline check and the `create_started` write) was fixed in 7880c46 and is unchanged here.

## Evidence: a create ledger outside the session directory

A new directory, `<state>/creates/`, is mode 0700, owned by wsd, and outside every bind and every session directory. Nothing removes it except wsd's own cleanup (below). Each create has two files, each written once and never rewritten:

- **`<name>.json`, the submission record**, where `<name>` is the sandbox name (`hz<12 hex>g<gen>`, `NAME` in `spec.py`). It holds `{"sandbox", "key", "ws", "bead", "generation", "deadline", "at"}` and is written before the record's `create_started` write.
- **`<name>.settled.json`, the settlement**. It holds `{"sandbox", "how", "at"}`, plus `"by"` for an operator settlement. `how` is one of:
  - `returned`: `backend.create` returned. This is the same evidence as today's `created`.
  - `never_submitted`: the launch stopped at its deadline check, before the create was submitted (the T11 r3 path).
  - `observed`: the backend listed the sandbox, so the create completed. It is written before the sandbox is deleted. See assumption A1.
  - `operator`: the operator settled it after the escalation (below).

Both files use `reaper.write_once`'s procedure: a private temporary file, fsync, link to the final name (an existing file is kept, so the first writer wins), fsync the directory.

**The reader.** `settled(ledger, name)` is true only if `<name>.settled.json` is a regular file (opened `O_NOFOLLOW | O_NONBLOCK`, at most 4 KiB) holding a JSON object whose `sandbox` is `name` and whose `how` is one of the four kinds. Everything else is not settled: a missing file, both files missing, a link, a FIFO, oversize, malformed JSON, another sandbox, or an unknown kind. So the record's phase, its replacement or its removal never settles a create, and damage to the ledger keeps the watcher (fail closed).

**Why it is trusted.** `<state>/creates/` is never bound into any sandbox, the same as the session record (`docs/wsd.md` §5). Only wsd, the watchers and `wsctl` (through wsd's control socket) write it.

## Who writes what

In `_start` (`sandbox/runtime.py`), in this order:

1. The submission record is written. A write failure is a `_StepFailed`, before anything is submitted.
2. The record gets `seeded`, `create_started` and the deadline (unchanged).
3. The watcher starts (unchanged, but with the new arguments below).
4. If the deadline has passed, the settlement is written with `never_submitted`, then the record gets `create_started=False` (unchanged), and the launch fails.
5. `backend.create`.
6. The settlement is written with `returned`, then the record gets `created=True`.

**Recovery** (`_end`, from `sessions()`, `launch()`'s cleanup of `prev` and `_fail`) settles only from the record of the same generation, that is, the record's `sandbox` equals the entry's:

- **R1.** `create_started` is false, so the record says nothing was submitted. The submission happens only after `create_started=True` and the deadline check, and the only path that writes it false again is step 4. So this proves `never_submitted`.
- **R2.** `created` is true, a crash between step 6's two writes. This proves `returned`.
- Any other state leaves the entry unsettled. In particular `ended` alone, a record of another generation, or no record never settles anything.

**Observed.** Before a sandbox is deleted, whoever deletes it writes `observed` if the backend listed it:

- the watcher, at each pass: `names()` first, then `observed` if the name is listed, then kill and delete as today;
- `_teardown`, for each of `mine` (the session's listed sandboxes), before its `delete`;
- the stale-generation sweep (below).

If the `observed` write fails, the deletion still goes ahead, since deleting is always safe. It is retried the next time the sandbox is seen. A listing that fails writes nothing.

**The stale-generation sweep.** Before this design, `sessions()` ignored a listed sandbox `<sid>g<M>` whose session record names a different generation, because its short ID is "known". A new generation launches only after the earlier one's teardown has confirmed no sandbox of the key exists, so any such sandbox is a late create. `sessions()` now writes `observed` for it and deletes it. A delete that isn't confirmed raises `RuntimeUnavailable` ("a late sandbox of an earlier generation can't be deleted"), which holds the workstream until a later pass confirms it gone. The watcher still covers it meanwhile.

## The watcher

`reaper.main` gains `--ledger <state>/creates` and `--alerts <state>/alerts`. `settled` reads the ledger as above. `--record` stays, for `mark_live` only (g08sd).

`reap` keeps its loop and its exit rule: a confirmed deletion, `clock() >= deadline + LINGER_SECONDS`, and `settled()`. Two things change:

- **No exit while unsettled.** Rules 3 to 5 are gone, so a watcher whose create is unsettled never exits. It keeps killing and deleting every `POLL_SECONDS` (30 s) for as long as it runs, and a create that lands at any time is deleted within one pass.
- **The escalation.** At the first pass at or after `deadline + LINGER_SECONDS` with `settled()` false, the watcher writes one alert, `<state>/alerts/create-<name>.json`. The format is admind's alert-file contract (`admind/alerts.py`): a dotfile first, then a rename, with `{"id", "created_at", "text"}`. The text names:
  - the sandbox, its session's short ID, the generation, the workstream and the bead;
  - that the create's outcome is unknown, and that the watcher keeps deleting it;
  - the command that settles it, `wsctl sandbox settle WS SESSION --generation N`.

  Every value in the text is checked against admind's sensitivity scan (`config.secret_scan`): a short ID is 14 characters, never 64-hex. A failed write is retried at every later pass until it succeeds. The escalation never ends the watch. It is written once: an existing file is kept.
- **The close-out.** When an escalated entry later settles, the watcher writes `create-<name>-settled.json` ("settled: `<how>`") before it exits, so the operator sees the end of what they were told about.

`LINGER_SECONDS` keeps its value and its comment's reasoning, but its meaning narrows. It is no longer how long a watcher may wait. It is how long a watcher waits for a settled create to finish deleting, and when an unsettled one escalates.

## Recovery: watchers are re-armed from the ledger

A watcher that is gone (a reboot, a killed tmux server, an operator's `tmux kill-session`) is started again from the ledger. `sessions(ws)` runs at every pickup and reconcile, and wsd runs it at startup. Before anything else, it does this for each unsettled entry of the workstream:

- If `wsd-<sid>-r<gen>` isn't on wsd's tmux server, it starts the watcher with the entry's sandbox name, generation and deadline. It uses the session's record and marker paths, whether they exist or not. If they don't, `mark_live` writes no marker, as today.
- A tmux listing that fails, an unreadable ledger, or a re-arm that fails raises `RuntimeUnavailable`, and the workstream holds. Nothing is assumed watched.

`launch()` does the same for its own key before it writes the new record. So a replacement generation never starts while an earlier generation's unsettled create has no watcher.

**Cleanup.** In the same pass, wsd removes the files of a settled entry once its watcher is no longer on tmux. It removes the alerts first, then `<name>.json`, then `<name>.settled.json` last. A crash at any point then leaves either a settled entry or nothing, never a submission record without its settlement, which would be re-armed for nothing. An entry with only a settlement is removed the same way.

**Resolve.** `_running(sid)` also refuses while the session has an unsettled entry ("an earlier create of generation N has not settled"), whether or not its watcher is on tmux. An unreadable ledger is `RuntimeUnavailable`, as a failed listing is today.

## After LINGER_SECONDS: fail closed, then escalate

Nothing is assumed about a create that hasn't settled, however long ago it was submitted:

- its watcher stays (and is re-armed if it is lost), and every late sandbox is deleted within one pass;
- the operator gets one alert at `deadline + LINGER_SECONDS`;
- the session's directory can't be resolved away while the create is unsettled;
- the entry settles only on evidence (`returned`, `never_submitted` or `observed`), or on the operator's decision.

The workstream is not held only because a create is unsettled. Its watcher is the safety, and holding the queue would add nothing to it. The workstream holds only when that safety can't be confirmed: the watcher can't be listed or re-armed, or the ledger can't be read.

**The operator's settlement.** `wsctl sandbox settle WS SESSION --generation N` is a new wsd control operation. It writes `operator`, with `by` naming `wsctl`. It refuses:

- before `deadline + LINGER_SECONDS` ("its watcher is still within its linger"), so it can't cut a linger short;
- when the entry is unknown or already settled. A replay of the same request on an `operator` entry succeeds and changes nothing;
- when the backend lists the sandbox, or the listing fails ("the sandbox may exist; its watcher will delete it first").

It is the operator accepting that no late create will land, after checking OpenShell. `docs/wsd.md` says what to check: the gateway's sandbox list, and whether the gateway has restarted since the submission (see A1 and question 2). The watcher then exits under its normal rule at its next pass, and cleanup removes the entry.

## Migration

The first wsd start after this lands writes a submission record for each session record that has `create_started` true and `created` false, in any phase, if the ledger has no entry for it. A record with `create_started` false or `created` true is settled by R1 or R2 at once. Watchers started before the upgrade still read the record under the old rules. When one exits, the next `sessions()` pass re-arms a new watcher for the entry.

**Accepted residual:** a create whose record was already replaced or removed before the upgrade has no evidence left, so it can't be migrated. A late create landing in the window between an old watcher's exit and the next pass (at most the 60 s pickup backstop) is deleted on that pass. It is not left unwatched for longer than that.

## Policy decided here (not in the ADR)

1. An `ended` record, a replacement generation and a removed record never settle a create.
2. A create settles only on `returned`, `never_submitted`, `observed` (A1) or `operator`.
3. Past `LINGER_SECONDS`, an unsettled create's watcher stays without limit, and the operator is alerted once.
4. The ledger is authoritative for watchers: a missing watcher is re-armed, and one that can't be is a hold.
5. **Load.** Each unsettled create keeps one watcher process, polling every 30 s. Their number is bounded by the launch-failure budget: a create that times out is a refused or uncertain launch, and the bead escalates once `launch_failures_before_human` is reached.

**Assumption A1.** One `openshell sandbox create` request makes at most one sandbox. Once that sandbox has been listed and then deleted, the same request doesn't make it again. `observed` depends on this. If review or Liam rejects it, `observed` is dropped from the settling kinds (but still written, as evidence), and a timed-out create settles only through the operator.

## ADR open items (not edited here)

1. **Alert writer.** ADR §6.2 says `wsd` writes the local alert file. Here a watcher, which is a wsd-owned process in wsd's tmux server, writes it, so that it doesn't depend on wsd running. A clarification would read "wsd or one of its watchers".
2. **Accepted risk on an unknown create.** §7's residual-risk list doesn't name a create whose outcome OpenShell can't report. A clarification would add it: watched without limit, and settled by the operator only after an alert.

Neither changes behavior the ADR requires, so implementation doesn't wait on them.

## Tests (offline)

All of these run with the existing fakes: `tests/test_wsd_lifetime.py`'s injected clock, sleep and delete, and `RuntimeRig`'s fake backend and tmux. None needs OpenShell, podman or the network.

`tests/test_sandbox_reaper.py`:

1. `test_the_ledger_settles_only_on_a_valid_settlement`, parameterized:
   - no files, submission only, a settlement for another sandbox, an unknown `how`, malformed JSON, a JSON list, a symlink, a FIFO (the read must not block) and an oversize file each give False;
   - each of the four kinds gives True;
   - a settlement without its submission record gives True.
2. `test_an_unsettled_watcher_never_exits_and_escalates_once`: the clock is driven to 3 × `LINGER_SECONDS`, deletion is always confirmed and nothing settles. The watcher is still looping, exactly one `create-<name>.json` exists, and every pass killed and deleted.
3. `test_a_late_create_long_after_the_linger_is_deleted_and_observed`: the sandbox appears at 2 × `LINGER_SECONDS`. The next pass writes `observed` before its delete, and the watcher exits on a later pass. The close-out alert is written because the entry had escalated.
4. `test_the_alert_meets_admind_contract`: `admind.alerts.scan` reads the escalation and the close-out as valid, not malformed and not withheld. A failed alert write (a read-only directory) is retried at every pass and never ends the watch.
5. `test_an_observed_write_failure_still_deletes`.
6. Extended `test_the_reaper_reads_the_argv_the_backend_names`: `--ledger` and `--alerts` reach `settled` and the escalation, and `--record` reaches only `mark_live`.

`tests/test_wsd_lifetime.py` (the runtime, with `RuntimeRig`):

7. Replaced `test_a_crash_during_the_create_leaves_a_watcher_that_waits_for_the_record` (r2 path 2a): wsd crashes inside the create, and the restarted wsd's `sessions()` records `ended`. Now `settled` is False, and the watcher is on tmux past 2 × `LINGER_SECONDS`, deletes a create that lands then, and exits only after `observed`.
8. `test_a_replacement_generation_never_settles_the_earlier_create` (r2 path 2b): generation 1's create times out (the fake backend records it pending), and generation 2 launches and runs. Generation 1's entry stays unsettled and its watcher stays. A late `g1` sandbox:
   - while generation 2 runs, is deleted by the stale-generation sweep, with `observed` written first;
   - after generation 2 has also ended, is deleted by the watcher.
9. `test_a_resolve_waits_for_an_unsettled_create`: an unlanded session with an unsettled entry, and its watcher killed. `wsctl resolve` refuses with the new reason. After `observed`, it goes ahead. The session directory's removal never settles the entry.
10. `test_ledger_writes_come_in_order`: a spy on the record writes, the ledger writes and the watcher start shows submission record, then `create_started`, then the watcher, then `returned`, then `created`. For the deadline path it shows `never_submitted` before `create_started=False`. A failed submission write submits nothing.
11. `test_recovery_settles_only_from_the_same_generation`, parameterized over a crash at each step of `_start`: R1 and R2 settle as stated. An `ended` record of the same generation with `create_started` true and `created` false stays unsettled. A record of another generation, or none, settles nothing.
12. `test_a_lost_watcher_is_rearmed`: kill the watcher's tmux session (a simulated reboot). `sessions()` starts it again with the entry's name, generation and deadline. A failing tmux listing, or a re-arm that fails, raises `RuntimeUnavailable` and holds the workstream. `launch()` of the same key re-arms before it writes the new record.
13. `test_cleanup_never_leaves_a_submission_without_its_settlement`: a crash injected before each unlink. The next pass re-arms nothing for a settled entry and finishes the cleanup. An unsettled entry is never removed.
14. `test_the_operator_settles_only_after_the_linger_and_when_absent`:
    - refused before `deadline + LINGER_SECONDS`, while the sandbox is listed, and when the listing fails;
    - accepted afterwards, after which the watcher exits at its next pass;
    - a replay is a no-op, and an unknown or already settled entry refuses (except the replay).
15. `test_migration_writes_entries_for_outstanding_records`: records with `create_started` true and `created` false, in `creating`, `stopping` and `ended`, gain unsettled entries. Others gain settled ones, or none. A second start writes nothing.
16. `test_teardown_observes_before_it_deletes`: `_teardown` writes `observed` for each listed sandbox of the key before its `delete`.
17. Unchanged and still passing: `test_a_launch_stalled_past_its_deadline_creates_nothing`, now also asserting `never_submitted`, and `test_a_stall_after_the_deadline_check_never_lets_the_watcher_go`.

Static checks: Ruff and Pyright on the touched modules, and a test that `<state>/creates/` is outside every bind of `build_spec` (as g08sd's test 4c does for the marker).

## Docs

- `docs/wsd.md` §5, the launch paragraph: the create ledger, what settles a create, the watcher's escalation, and re-arming.
- `docs/wsd.md` command table: `wsctl sandbox settle`, with what to check first.
- `docs/wsd.md` §5, the resolve procedure: the new refusal.
- The `reaper.py` module docstring and the `settled` docstring: the rules above, replacing record-based settlement.

## Out of scope

- A cancellation proof from OpenShell. If a later OpenShell can report a create's outcome or cancel it, that becomes a fifth settling kind, in its own bead.
- Marmot access to `wsctl sandbox settle`. It is a host command, like `wsctl resolve` today. Under Marmot-only operation it needs an admind command, filed as a follow-up and kept consistent with whatever resolve gets.

## Open questions for Liam

1. **A1, `observed` settles.** Recommendation: yes. It is the common way a timed-out create ends, and without it every timeout ends in an operator step. A live check (create, kill the CLI, wait for the landing, delete, watch it stay gone) is filed as a follow-up bead for the live suite.
2. **The watcher stays without limit past the linger.** The alternative is that the watcher exits after some multiple of the linger and the workstream holds instead. Recommendation: stay without limit. The watcher is what deletes a late sandbox, and a hold would stop work without deleting anything.
3. **`wsctl sandbox settle` stays host-only for now** (see "Out of scope"). Recommendation: yes, with the admind command as a follow-up.
