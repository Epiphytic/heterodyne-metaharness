# heterodyne-metaharness Plan 3: wsd core A, queue and state — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the deterministic core of `wsd`, the workstream daemon:

- a SQLite journal and inbox (§3.3);
- a beads adapter that works through btq's `Queue` as agent `wsd`, with one workstream-session worker per workstream and one per-bead worker per claimed bead (§4.3);
- the shared pause gate and the per-workstream claim lock (§4.3);
- pickup, which never leaves a workstream idle while an unblocked bead exists (§5.2);
- the park, resume, release and escalation journals, and the one launch guard every launch goes through (§3.3, §4.3);
- the startup recovery order (§3.3), which never launches anything;
- the seams that plans 4, 5, 6 and 8 plug into: `AgentRuntime`, `ActionReconciler`, `Parker.park`, `Parker.release`, the inbox, the progress events and `Journal.backup`.

**Architecture:** every multi-step change to the queue, a worktree or an agent session is an *operation* in the journal (`ops`): intent first, then one recorded step at a time, each step idempotent (check, write, read back). A crash anywhere is replayed from the step reached. Every external effect is followed by a `<op>.<step>!` checkpoint and every journal write by `<op>.<step>`, so tests can crash on either side of each write, or pause there to force an interleaving. Beads stay the source of truth: what was launched for a bead is recorded on the bead itself (`metadata.wsd_session`) before the launch, sessions come from the runtime's own list, and anything wsd can't read, or that beads and the journal disagree on, holds rather than proceeds. Every launch goes through one guard (`Parker.launch`) that re-checks the runtime, the holds, the coder role and btq's post-claim checks immediately before it; recovery never launches. Pickup, park, release and recovery are blocking code run per workstream under one lock (`Parker.entry`); a small asyncio daemon runs them on startup, on timers and on control-socket requests.

**Tech Stack:** Python 3.12+, asyncio, sqlite3 (stdlib), msgspec (the only runtime dependency), btq's `Queue` loaded from `$BTQ_REPO/bin/btq`, git; pytest, hypothesis, ruff, pyright (strict on `src/`).

**Spec:** ADR 0001 revision 13 is design-repo commit `66b3aecb639e6ec56f108e2f55d483d4dedda485` in `$DESIGN_REPO`, approved in bead `btq-5ky39`. Section numbers (§) refer to it. Roadmap row 3 (`docs/superpowers/plans/2026-09-29-heterodyne-v1-roadmap.md`) scopes this plan. The progress-event requirement (waiting-on-input, held and stuck with concrete reasons, and per-message progress) follows the pending amendment `btq-xv48a`; this plan only records them, plan 6 renders them.

**Status:** revision 2 of this plan, answering the r1 cross-model review (19 blocking and 5 non-blocking findings; see "Simplifications vs r1"). **Design approval is not set.** Four questions need the operator before approval (see "Operator decisions"); D3 and D20 stay provisional until then.

## Global Constraints

- **Variables:** `$HZ` is a fresh clone of `Epiphytic/heterodyne-metaharness` (never the live harness or a symlink to it), `$BTQ_REPO` the beads-task-queue checkout providing `bin/btq`, `$DESIGN_REPO` the design repo. "Install-agnostic: no install paths, npubs, tokens or relay URLs. Use the roadmap variables ($HZ, $BTQ_REPO, $DESIGN_REPO)." `scripts/check_install_agnostic.py` stays clean.
- "Python 3.12+, matching the repo's tooling (pytest, hypothesis, ruff, pyright)." Managed with `uv`. Runtime dependencies stay exactly `msgspec`. Async tests use `asyncio.run` inside sync test functions; no pytest-asyncio.
- "Tests never touch the network, a real wn-agent, the real systemctl, the real claude or codex binaries, ~/.claude, or the real beads database. Use fake executables and temp dirs." The queue is `tests/fakes/fake_btq.py`; the one contract test that loads the real `$BTQ_REPO/bin/btq` replaces its `bd` with a fake executable and points `HOME` at `tmp_path`, and is skipped when `BTQ_REPO` is unset.
- "Nothing may be specific to Claude or Codex." No adapter, model or CLI name appears in `src/heterodyne/wsd/`. Test profiles are made-up names (`p-one`, `p-two`).
- "Every recovery path fails closed. Never infer absent, complete or safe from missing or unreadable evidence." Concretely: an unreadable pause flag reads as paused; an unreadable claim holds the workstream; a session the runtime lists is treated as running until the runtime confirms it ended, and `Liveness.UNKNOWN` never counts as dead; a runtime that can't list its sessions holds the workstream; a `dependencies` list that is missing or disagrees with `dependency_count` is a malformed answer, not "no blockers"; a missing or unreadable launched-session record escalates, never falls back to the current configuration; a bead the journal has no row for is held, never relaunched; closing a bead never settles its action; a corrupt journal stops wsd and is left in place.
- "Use crash-window and interleaving tests (deterministic checkpoints) for every journaled transition." Every external effect is followed by a barrier checkpoint (`<op>.<step>!`) and every journal write by its step checkpoint (`<op>.<step>`): `POINTS` (pickup), `PARK_POINTS`, `RESUME_POINTS`, `RELEASE_POINTS`, `ESCALATE_POINTS`, `RECOVERY_POINTS`, `gate.checked`, `gate.pause.waiting` and `lock.waiting` (the operation lock's door). Tests crash at each one (`CrashAt`) and replay, or pause at one (`PauseAt`, `Seen`, `Many`) to force an interleaving.
- **The beads adapter goes through btq's interfaces, not raw bd.** wsd loads btq's `Queue` in-process. Labels, dependencies, comments and metadata have no `Queue` method, so they go through `Queue.bd()` of the per-bead worker, after `Queue.owned()` confirms the claim, under that worker's `Queue.exclusive()` lock. wsd never execs `bd` itself.
- **wsd never unclaims** (§4.3). A parked, held or stuck bead stays `in_progress` under its per-bead worker. This departs from btq's `PICKUP.md` step 7 and its "only restructure tasks nobody holds" rule; see Operator decisions (c).
- **`src/` style:** ruff line length 110; pyright strict; `sys.platform` only in `src/heterodyne/platform.py`; error messages name keys, never secret values.
- **Review rule:** every task ends with a review by a **different LLM than the implementer** (cross-model), or by a fresh-context adversarial agent when only one LLM is available. The brief names the diff range and the ADR sections and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings; fix or rebut every blocking one. Close evidence includes `Code-Review: reviewer=<model> author=<model> mode=<cross-model|adversarial> range=<BASE>..<HEAD>`.
- **Beads** (workstream `heterodyne`): Tasks 1–9 are `kind:task`, each with `metadata.design_approval=btq-5ky39`, `metadata.adr_revision=66b3aecb639e6ec56f108e2f55d483d4dedda485` and a blocking dependency on `btq-5ky39`. They run in order 1 → 9, each blocked by the one before.
- **Branches:** integration branch `plan-3-wsd` in `$HZ`, from `main`. One commit (or more) per task; never amend a pushed commit.

---

## Decisions made in this plan (within the ADR; reviewers should check them)

| # | Where the ADR is silent or loose | Choice | Why |
|---|---|---|---|
| D1 | §4.1/§4.3 use `uuid5(NS, …)` without fixing `NS`. | `NS = uuid5(NAMESPACE_URL, "urn:heterodyne:wsd")`, a constant in `ids.py` that must never change. | Deterministic and install-independent; a changed NS would orphan every claim and session. |
| D2 | btq's `Queue` has no call for labels, dependencies, comments or metadata. | `Queue.bd()` of the per-bead worker, after `Queue.owned()`, under `Queue.exclusive()`; every write is check → write → read back. | Keeps to btq's interface (the scope says "not raw bd"), and S6 shows `bd label add` exits 0 even on a missing bead, so only a read-back is evidence. |
| D3 | How `/stop` is represented on the bead. **Provisional: operator decision (a).** | A park with `hold=True`: `v2:held` (written first) plus `v2:parked`, no blocker needed. A `v2:held` bead is never resumable; only `Parker.release` (plan 6) moves it on. | Keeps "parked" one mechanism; the operator hold is visible on the bead, not only in the journal. It departs from §4.3's "parking keeps a blocking edge" invariant, hence the decision. |
| D4 | When a parked bead is "waiting on input" rather than "blocked on a bead". | When an open blocker carries `kind:approval`, `kind:question` or `kind:confirm` (`OPERATOR_INPUT_LABELS`). | Those are the operator-ask bead kinds of §5; plans 5–7 create them. |
| D5 | Which repository a bead's worktree comes from; which profile runs it. | `metadata.repo` names one of the workstream's `[repos]` (default `default`); a single `role:<role>=<profile>` label overrides the configured coder profile (§15 layer 4). | Both are per-bead data in beads, so recovery can recompute them. |
| D6 | Plan 5 owns action reconciliation (§5.4), but recovery step 3 runs now. | `HoldingReconciler`: any bead labelled `ws:<ws>`, **closed ones included**, whose `metadata.action_state` is set to anything but `pending`, `succeeded` or `failed` (`SETTLED_ACTIONS`) holds the workstream (`actions_unreconciled`). The hold stops every launch: the launch guard refuses while any hold applies, replays included. Plan 5 replaces the reconciler. | Fails closed: plan 3 can't check targets, so it never assumes there is nothing to reconcile, and closing a bead says nothing about its action. |
| D7 | Recovery scope. | Recovery runs per workstream. A workstream whose recovery fails stays `held` and is recovered again before its next pickup; the others carry on. | One unreachable dependency must not stop unrelated workstreams. |
| D8 | A lost journal (the file deleted or moved aside). | Any bead held by our per-bead workers that the journal has no row for is escalated `journal_lost` (STUCK, `needs-human`): never relaunched, never parked or unparked, whatever its labels and sessions say. Its sessions keep the coder role until the runtime confirms they ended. Only the operator's release moves it on. | Beads can't say whether a park, `/stop` or escalation was cut short, so recovery doesn't guess. Simpler than r1's "finish the park" rule, which inferred from partial evidence. |
| D9 | Until plan 4 there is no agent runtime. | `NoRuntime` reports unavailable and can't list sessions; every workstream is held `runtime_unavailable` and nothing is claimed. `RuntimeUnavailable` anywhere is a workstream hold: the open operation stays at its step, no failure budget is spent and nothing escalates. | Never claim work that can't run; an absent runtime is not the bead's fault. |
| D10 | One coder session per workstream (§4.3) when a stop can't be confirmed. | The role is taken by any coder session in `AgentRuntime.sessions(ws)`, which lists every session launched until the runtime confirms it ended, whatever its bead's state. A session of a bead that is no longer ours is stopped; while the stop is unconfirmed the bead is STUCK `stop_unconfirmed` and its session keeps the role. Nothing is recomputed from current labels or configuration. | "Never infer safe": a session that may be live is treated as live, and the runtime, not wsd's derivation, says which sessions exist. |
| D11 | What "never idle" means operationally (§5.2). | Pickup tries every candidate in turn (resumable first, then ready) until one starts; a refused claim, a lost claim or a **confirmed** launch failure moves on to the next candidate. An **uncertain** launch keeps the coder role and holds `launch_uncertain` until the session list settles it. A claim that did not land becomes a `beads_unreachable` hold, not idle. | Tested by a hypothesis property over random queues and per-bead faults, with an independent oracle (below Task 7). |
| D12 | Pause and resumable beads. | Pause blocks new claims only; a parked bead whose blockers closed still resumes while paused. | §4.3: "Pausing stops new claims only. Running and parked beads continue." |
| D13 | `[integrations.beads]` keys. | `btq` (the checkout) plus btq's optional locations (`config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`, `tls_cert`) and `credentials = { file = … }`. Unset ones fall back to btq's `BTQ_*` environment. | Uses the table plan 1 reserved; credentials are only ever a file reference. |
| D14 | btq's state directory (`paused` flag, worker state) is under `Path.home()`. | wsd uses btq's location unchanged; tests that load the real btq set `HOME` to `tmp_path`. | btq owns that convention; the pause flag must be the one `btq pause` sets. |
| D15 | Callers of `Parker.park` (plans 4 and 6). | `park` takes the workstream's operation lock (`Parker.entry`, shared with pickup, release and recovery). It raises `OpConflict` while another operation is open on the bead and `BeadsUnavailable` when beads can't be reached; an open park journal is replayed by the next pickup. Callers keep their request and report it as pending. | No two operations on a workstream interleave, and an outage never escalates a bead. |
| D16 | Approval beads for `HoldingReconciler` (plan 5). | Found by the `ws:<ws>` label. | Plan 5 must keep that label on approval beads (flagged in the seams doc). |
| D17 | Where launch-time safety lives. | One launch guard, `Parker.launch(op, new)`, is the only caller of `AgentRuntime.launch`. In order: runtime available; no hold but the operation's own `launch_uncertain`; no other bead's coder session; no `needs-human`; `BeadsAdapter.validate` reruns btq's post-claim checks (ownership, routing, design approval); not held, parked or blocked (else shelved back to parked, or escalated if it has a session); the launched-session record written (first launch) and read back; the worktree verified; any own session not confirmed live, or under another key, is uncertain; then the launch. | Every replay and resume goes through the same checks immediately before the launch, so no path launches on stale evidence. |
| D18 | §3.3's session registry before plan 4. | The launched-session record (`SessionRecord`: role, profile, session key, repository, worktree) is written to the bead as `metadata.wsd_session` before the first launch, with read-back. Resume, release and the sweep use it, never the current configuration. A pickup replay launches what its journal recorded at the worktree step. | The record survives a lost journal, and a configuration change never redirects a running bead. Plan 4 may add fields; unknown fields are ignored. |
| D19 | How the operator's `/stop` and escalations end (§6.3, plan 6). | `Parker.release(bead)`: a journaled operation that removes `v2:held` and `needs-human`, then puts a parked bead back to waiting or hands an unparked one to a resume through the launch guard. HELD and STUCK rows leave only through it; removing the labels by hand changes nothing in wsd. | One audited way out, safe to replay. |
| D20 | Journal backups (§3.3: "backed up with the beads backups"). **Provisional: operator decision (b).** | `Journal.backup(dest)`: a consistent online copy using SQLite's backup API (safe under WAL), written under a temporary name (mode 0600) and renamed into place. Scheduling it next to the beads backups is left to plan 8. | A WAL database can't be copied as a file; the interface is here so plan 8 only schedules it. |
| D21 | How recovery finds "our" beads. | By assignee: every unclosed bead held by one of the workstream's per-bead workers, whatever its labels. A routing change under a claim is found by `validate` and escalated `routing_changed`. | Labels are routing, not ownership (§4.3); a relabelled bead and its session must stay visible. |
| D22 | An existing worktree at a bead's path (§4.3). | It is used only if btq's provenance (worker, repository and base) on the bead matches, or its git common directory is the repository's. Otherwise `worktree_failed`. Resume verifies the recorded worktree the same way. | A leftover or replaced directory never receives another repository's work. |
| D23 | When sessions are reconciled. | The sweep (`sweep.py`: stop sessions of beads no longer ours, give a running bead whose session ended a resume operation, hold disagreements) runs at recovery step 5 **and** at the start of every pickup. | The runtime and beads change between restarts too; one code path covers both. |

## ADR conflicts and gaps, flagged for the operator (not silently resolved)

Six numbered entries (r1's count was wrong: entry 3 covers two decisions). Two conflicts with btq's protocol follow them, and the questions the operator must answer are in "Operator decisions" below.

1. **`/pause` in §6.3 vs §4.3.** §6.3's command table says `/pause` "stops or restarts pickup. The running task finishes its turn and is then parked." §4.3 says "Pausing stops new claims only. Running and parked beads continue, unless the operator uses `/stop`." This plan implements §4.3. Plan 6 owns `/pause` and should get the ADR text reconciled first.
2. **The park sequence.** §4.3 lists "record intent, commit WIP (recording the SHA), apply the label, add a bead comment". This plan adds two steps: **stop the session** before the WIP commit (otherwise the agent keeps writing into the worktree being committed), and **add the blocking edges** before the label (so a bead is never `v2:parked` without what keeps it from resuming). It is an elaboration, not a change of outcome, but a reviewer may want it written into the ADR.
3. **`/stop` representation** (D3) and **waiting-on-input detection** (D4) are not in the ADR. They are plan-level choices that plans 6 and 7 will depend on.
4. **btq state under `HOME`** (D14). btq's per-worker state (and so the shared pause flag) lives under the service user's home. That is fine for one host user, but plan 4's synthetic home for agents must not be the home wsd runs with.
5. **Runtime availability.** Until plan 4 lands, a running `wsd` claims nothing (D9). That is intended, but it means no end-to-end run is possible between plans 3 and 4.
6. **Sandbox backend.** The roadmap's plan 4 names bubblewrap; the operator has since chosen OpenShell (ADR revision 14, pending, not treated as approved here). Nothing in this plan depends on the backend: `AgentRuntime` is backend-neutral.

Conflicts with btq's `$BTQ_REPO/docs/PICKUP.md`:

- **Step 7, line 81 ("Release what you can't progress")** vs §4.3's "wsd never unclaims". A parked bead stays claimed under its per-bead worker.
- **Line 119 ("Only restructure tasks nobody holds")** vs the park sequence, which adds blocking edges to a bead wsd still holds.

## Operator decisions (needed before design approval; recommendations, not decisions)

**(a) Blocker-free operator holds (review finding 18, D3).** §4.3 says a parked bead keeps a blocking edge; `/stop` as designed parks with `v2:held` and no edge.
- Option 1: amend §4.3 so `v2:held` is a second park reason that needs no edge (no code change).
- Option 2: `/stop` creates a hold bead (`kind:hold`, `ws:<ws>`) and blocks on it; release closes it. The invariant holds literally, at the cost of an extra bead per stop and a release that writes two beads.
- Option 3: `/stop` doesn't park: it stops the session, commits WIP and adds only `v2:held` (no `v2:parked`, no edge). The guard already refuses a held bead, so nothing launches, but "stopped" becomes a second shape that the sweep must accept (today `v2:held` without `v2:parked` is escalated `unexpected_state`), and §4.3's park sequence no longer covers it.
- **Recommendation: option 1.** It is what the code does, `v2:held` is checked before every launch (D17), and release is already the only way out. Option 2 is the fallback if the invariant must hold literally.

**(b) Journal backup ownership (review finding 19, D20).**
- Option 1: a task here that adds `wsctl backup DEST` (a control request calling `Journal.backup`) and documents it next to the beads backups.
- Option 2: plan 8 owns it as a named dependency: plan 8's backup unit calls `Journal.backup(dest)` through a control request it adds, timed with the beads backups.
- **Recommendation: option 2.** The interface (`Journal.backup`, tested here for coherence, mode and leftovers) is in plan 3, but when to call it belongs to whatever schedules the beads backups, which is plan 8. Roadmap row 8 should list it.

**(c) btq protocol conflicts (PICKUP.md lines 81 and 119).**
- Option 1: amend PICKUP.md with a narrow wsd exception: an `agent:wsd` per-bead worker never releases (the claim is how a parked bead keeps its place and its session), and may add blocking edges, the `v2:parked`/`v2:held` labels and its own comment and metadata to a bead it holds. All other rules stay unchanged.
- Option 2: change wsd to release on park and re-claim on resume. That contradicts §4.3 and loses the claim to another worker while the bead's session state still exists.
- **Recommendation: option 1**, as a btq docs change reviewed in btq's own queue before plan 3 is implemented.

**(d) `/pause` in §6.3 vs §4.3** (conflict 1).
- Option 1: amend §6.3 to match §4.3 (pause stops new claims only); `/stop` is the way to park the running bead.
- Option 2: amend §4.3 to match §6.3 (the running task finishes its turn and is parked). Pause then needs a park with an operator hold, and resuming it needs a release.
- **Recommendation: option 1.** It is what this plan implements and tests (`test_pause_does_not_stop_the_running_bead`), and it keeps pause from doing two jobs. Plan 6 owns `/pause`; the ADR text should be settled before plan 6.

## Simplifications vs r1

Every r1 blocking finding, its fix, and the test that covers it. Where r1 had clever recovery, r2 holds instead (fail closed). Crash barriers (`<op>.<step>!`) sit after each external effect and before the journal write that records it.

| # | Finding | Fix | Covering test(s) |
|---|---|---|---|
| 1 | Recovery can't adopt a closed bead | `Journal.adopt` writes any state from any row, CLOSED included, without the transition check | `test_adopt_closes_a_bead_from_any_row` |
| 2 | Recovery launches before establishing role occupancy | Recovery never launches. Every launch goes through the guard, which reads the whole session list first (D17) | `test_recovery_never_launches`, `test_unknown_session_is_reserved_before_any_launch`, `test_two_dead_sessions_never_run_together` |
| 3 | Recovery discards session obligations | Sessions come from `AgentRuntime.sessions`, listed until confirmed ended; the sweep stops sessions of beads no longer ours and holds STUCK `stop_unconfirmed` until the stop is confirmed; a parked bead with a session is escalated | `test_session_of_a_bead_not_ours_is_stopped`, `test_unconfirmed_stop_of_a_closed_bead_is_stuck_until_confirmed`, `test_closed_bead_with_unconfirmed_stop_keeps_the_role`, `test_parked_bead_with_a_listed_session_is_escalated`, `test_lost_claim_stops_the_session` |
| 4 | Current configuration substituted for launch evidence | The launched-session record on the bead (D18); a missing or unreadable record escalates `launch_unrecorded`; pickup replays launch what the journal recorded | `test_resume_uses_the_recorded_identity_after_a_config_change`, `test_replayed_pickup_launches_what_it_chose`, `test_config_change_never_touches_a_running_bead`, `test_park_without_a_session_record_escalates`, `test_park_with_an_unreadable_record_escalates`, `test_running_bead_without_a_record_is_held` |
| 5 | Uncertain-claim recovery bypasses btq's routing and digest gate | `BeadsAdapter.validate` reruns btq's own post-claim checks (`matches`, `design_allowed`) as the bead's worker, in the guard, before every launch | `test_validate_reruns_btqs_post_claim_checks`, `test_contract_with_real_btq`, `test_replayed_pickup_revalidates_before_launch`, `test_guard_revalidates_routing_and_design_approval` |
| 6 | Launch replay doesn't revalidate ownership or eligibility | The guard (D17) on every path: ownership, routing, `needs-human`, held, parked, blockers; unknown own session is uncertain | `test_guard_refuses_a_lost_claim`, `test_hold_added_after_the_unlabel_shelves_the_bead`, `test_resume_abandoned_when_blocked_again`, `test_guard_never_launches_beside_an_unknown_session_of_its_bead`, `test_unknown_liveness_never_starts_a_second_session` |
| 7 | Resume loses its intent across the unlabel gap | A resume replay at `intent` that finds `v2:parked` gone treats it as its own completed unlabel; barrier `resume.unlabelled!` | `test_crash_at_every_resume_point_launches_once` |
| 8 | Action holds don't stop replay or recovery launches | Actions are read before any replay; the guard refuses while any hold applies; recovery never launches | `test_unsettled_action_blocks_a_replayed_launch`, `test_unsettled_actions_hold_after_recovery`, `test_unsettled_action_holds_pickup` |
| 9 | Closed status treated as a settled action | `with_metadata` includes closed beads; only `pending`, `succeeded`, `failed` are settled, unknown values are not | `test_unsettled_actions_include_closed_beads_and_unknown_values`, `test_closed_bead_with_an_unsettled_action_holds` |
| 10 | `NoRuntime` can escalate recoverable work | `RuntimeUnavailable` is a workstream hold everywhere: no budget, no escalation, operation left at its step | `test_no_runtime_waits_without_budget_or_escalation`, `test_no_runtime_never_spends_launch_budget`, `test_unconfirmed_stop_holds_and_never_spends_budget`, `test_runtime_that_cannot_list_holds_recovery` |
| 11 | Lost-journal recovery infers permission from absent evidence | A bead with no journal row is escalated `journal_lost` and never relaunched, parked or unparked (D8); escalation is journaled with the STUCK row in one transaction, then labelled | `test_lost_journal_holds_a_running_bead`, `test_lost_journal_never_relaunches_a_dead_session`, `test_lost_journal_never_finishes_a_cut_short_park`, `test_lost_journal_parked_bead_resumes_only_after_release`, `test_crash_at_every_escalate_point_labels_once` |
| 12 | Ownership discovery depends on routing labels | `ours()` by assignee (D21); routing changes escalate | `test_ours_is_found_by_assignee_whatever_the_labels`, `test_ownership_is_found_after_a_label_change` |
| 13 | Public parking bypasses the serialization lock | `Parker.entry` is the one lock for park, release, pickup and recovery; `OpConflict` for a second operation | `test_pickup_waits_at_the_door_while_a_park_runs`, `test_crash_at_every_park_point_completes_once` (`lock.waiting`) |
| 14 | The release seam strands held beads | `Parker.release` (D19), journaled; external label removal changes nothing | `test_release_of_a_held_parked_bead_makes_it_resumable`, `test_crash_at_every_release_point_completes_once`, `test_release_of_a_stuck_running_bead_resumes_through_the_guard`, `test_release_stops_unrecorded_sessions_before_writing_a_record`, `test_only_held_or_stuck_beads_are_releasable`, `test_external_label_removal_is_not_a_release` |
| 15 | Existing worktree doesn't prove repository identity | btq provenance or a matching git common directory (D22); resume verifies the recorded worktree | `test_worktree_needs_btqs_provenance`, `test_park_into_a_foreign_worktree_escalates`, `test_crash_between_worktree_and_provenance_escalates` |
| 16 | A failed launch reported as success and blocks other candidates | `Launch` outcomes: confirmed failure moves to the next candidate; uncertain keeps the role and holds; an operation's end settles its own uncertain hold | `test_confirmed_launch_failure_tries_the_next_candidate`, `test_uncertain_launch_keeps_the_role_and_holds`, `test_confirmed_launch_failure_retries_then_escalates`, `test_uncertain_launch_keeps_the_role_until_the_list_settles_it`, `test_uncertain_hold_ends_with_its_operation` |
| 17 | Contradictory dependency evidence | `parse` rejects a `dependencies` list whose length disagrees with `dependency_count` (an empty or cut-short list is not "no blockers"), or a count that is not a non-negative integer | `test_parse_rejects_a_dependency_count_that_disagrees` |
| 18 | Blocker-free hold is an ADR deviation | Not resolved here: operator decision (a); D3 provisional | `test_operator_hold_is_not_resumable` (current behaviour) |
| 19 | No journal backup | `Journal.backup` (D20); scheduling is operator decision (b) | `test_backup_is_a_coherent_private_copy` |

Non-blocking findings:

| Finding | Fix | Covering test(s) |
|---|---|---|
| Barriers after external effects | `<op>.<step>!` after every external write (claim, worktree, record, stop, commit, edges, labels, comment, unlabel, launch, escalation label), before its journal write; the crash tests iterate over them | `test_crash_at_every_{pickup,park,resume,release,escalate}_point_*` |
| Property oracle and uncertain launches | The property's oracle is independent of the scheduler: at most one coder session ever; STARTED, RESUMED and BUSY mean exactly one; HELD means a hold is recorded; NOTHING means nothing is ready and no coder runs. Faults are per bead; `launch_uncertain` is one of the generated faults | `test_never_idle_while_an_unblocked_bead_exists` |
| Real-btq contract coverage | The contract test now also runs btq's real post-claim checks through `validate` (`matches` and the design gate): a research bead passes, a task without an approval or with a non-approval `design_approval` raises `RoutingChanged`; it checks reads run as the workstream worker and validation as the bead's own worker. btq's `claim`, `owned` and `worktree` against a real Dolt stay out of scope (no real database in tests) and are covered by the fake, which follows btq's rules | `test_contract_with_real_btq` |
| Pause barrier | `gate.pause.waiting` is reached before the pauser takes the claim lock; the test waits for it, then checks the pause is not acknowledged | `test_pause_waits_for_in_flight_claim` |
| Structured reason details | State events carry `{"reason", "detail"}` (blocker IDs for a parked bead); a failed recovery publishes its hold; an acknowledged pause shows at once | `test_set_state_records_reason_and_event`, `test_failed_recovery_publishes_its_hold`, `test_acknowledged_pause_shows_at_once` |

**Known limit:** a relaunch after a dead session opens a new resume operation, so its launch-failure budget starts again. A bead that crashes its session on every launch keeps relaunching; crash-loop detection belongs to plan 4 with the runtime's exit evidence (§10).

## File map

| File | Responsibility | Task |
|---|---|---|
| `src/heterodyne/fsutil.py` | `private_dir` (moved from `admind/store.py`, shared by admind and wsd) | 1 |
| `src/heterodyne/admind/store.py` | imports `private_dir` from `fsutil` | 1 |
| `src/heterodyne/wsd/__init__.py` | package | 1 |
| `src/heterodyne/wsd/ids.py` | deterministic worker and session IDs (§4.1, §4.3) | 1 |
| `src/heterodyne/wsd/checkpoints.py` | the `Checkpoint` hook type | 1 |
| `src/heterodyne/wsd/states.py` | bead and workstream states, reasons, legal transitions | 2 |
| `src/heterodyne/wsd/journal.py` | the SQLite journal: inbox, operations, bead states, holds, progress events | 3 |
| `src/heterodyne/wsd/btq.py` | loading btq's `Queue`; the `QueueLike` protocol | 4 |
| `src/heterodyne/wsd/gitwip.py` | WIP commits for parks | 4 |
| `src/heterodyne/wsd/beads.py` | `BeadsAdapter`: reads, claim, owned writes, worktrees, pause flag | 4 |
| `docs/spikes/S6-beads-json.md` | the bd 1.1 JSON shapes the adapter accepts | 4 |
| `src/heterodyne/wsd/gate.py` | `ClaimGate` (claim lock, pause), `instance_lock` | 5 |
| `src/heterodyne/wsd/runtime.py` | `AgentRuntime`, `NoRuntime`, `ActionReconciler`, `HoldingReconciler` | 5 |
| `src/heterodyne/wsd/workstream.py` | per-workstream settings, `place()`, `Deps` | 6 |
| `src/heterodyne/wsd/park.py` | `Parker`: the operation lock; park, resume, release and escalation journals; the launch guard | 6 |
| `src/heterodyne/wsd/sweep.py` | the session sweep, shared by pickup and recovery | 7 |
| `src/heterodyne/wsd/scheduler.py` | `Scheduler`: pickup and the pickup journal | 7 |
| `src/heterodyne/wsd/recovery.py` | `recover()`: the startup recovery order | 8 |
| `src/heterodyne/wsd/settings.py` | `[wsd]`, `[integrations.beads]` and workstream settings | 9 |
| `src/heterodyne/wsd/ctl.py` | the control socket | 9 |
| `src/heterodyne/wsd/daemon.py` | `Wsd`: startup, timers, control requests | 9 |
| `src/heterodyne/wsd/cli.py` | `wsd` and `wsctl` | 9 |
| `src/heterodyne/defaults/defaults.toml`, `pyproject.toml` | `[wsd]` defaults; console scripts | 9 |
| `docs/wsd.md`, `docs/configuration.md`, `docs/install.md`, `examples/config.toml` | operator docs | 9 |
| `tests/fakes/checkpoints.py` | `Recorder`, `CrashAt`, `PauseAt`, `Seen`, `Many`, `SimulatedCrash` | 1 |
| `tests/fakes/fake_btq.py` | in-memory queue with btq's `Queue` interface and bd 1.1 shapes | 4 |
| `tests/fakes/fake_runtime.py` | recording `AgentRuntime` | 5 |
| `tests/wsd_env.py` | shared helpers (Task 4), the test rig (Task 6), rig pickup (Task 7) | 4, 6, 7 |
| `tests/test_wsd_*.py` | one test file per module, plus `test_wsd_pause.py` | 1–9 |

---
### Task 1: Scaffolding, identities and checkpoints

**Files:**
- Create: `$HZ/src/heterodyne/fsutil.py`, `$HZ/src/heterodyne/wsd/__init__.py`, `$HZ/src/heterodyne/wsd/ids.py`, `$HZ/src/heterodyne/wsd/checkpoints.py`, `$HZ/tests/fakes/checkpoints.py`
- Modify: `$HZ/src/heterodyne/admind/store.py` (move `private_dir` out)
- Test: `$HZ/tests/test_wsd_ids.py`

**Interfaces:**
- Consumes: nothing from this plan. `admind/store.py`'s existing `private_dir`.
- Produces:
  - `heterodyne.fsutil.private_dir(path: Path) -> None`.
  - `heterodyne.wsd.ids`: `NS: uuid.UUID`, `SLUG: re.Pattern`, `PROFILE: re.Pattern`, `ROLE_LABEL = "role:"`, `class BadName(ValueError)`, `slug(value: str, what: str) -> str`, `ws_session(ws: str) -> str`, `bead_session(ws: str, bead: str) -> str`, `role_session(bead: str, role: str, profile: str) -> str`, `profile_for(labels: tuple[str, ...], role: str, default: str) -> str`.
  - `heterodyne.wsd.checkpoints`: `Checkpoint = Callable[[str], None]`, `nothing(_name: str) -> None`.
  - `tests/fakes/checkpoints.py`: `SimulatedCrash(BaseException)`, `Recorder` (`.seen: list[str]`), `CrashAt(point)`, `PauseAt(point)` (`.reached`, `.go`: `threading.Event`), `Seen(point)` (`.reached` set when the point is passed, without stopping), `Many(*recorders)` (forwards every point to each). A recorder records the calling thread's name with each point, so an interleaving test can tell which caller reached which door.

- [ ] **Step 1: Create `$HZ/tests/fakes/checkpoints.py`**

Checkpoints are how every crash-window and interleaving test reaches an exact point. `SimulatedCrash` is a `BaseException`, so no `except Exception` in wsd can swallow it.

```python
"""Deterministic checkpoints for crash-window and interleaving tests.

wsd calls `cp(name)` right after each external effect (`<op>.<step>!`) and each journal write
(`<op>.<step>`). `CrashAt` raises `SimulatedCrash` (a BaseException,
so no `except Exception` in wsd can swallow it) the first time a named point is reached, which models the
process dying at exactly that point. `PauseAt` parks the calling thread at a point until the test lets it go.
"""

import threading


class SimulatedCrash(BaseException):
    pass


class Recorder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, name: str) -> None:
        self.seen.append(name)


class CrashAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.fired = False

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.fired:
            self.fired = True
            raise SimulatedCrash(name)


class PauseAt(Recorder):
    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.reached = threading.Event()
        self.go = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point and not self.reached.is_set():
            self.reached.set()
            if not self.go.wait(10):
                raise TimeoutError(name)


class Seen(Recorder):
    """Signals, without blocking, the first time a thread reaches the point: an interleaving test waits on
    `reached` to know the other thread is at that point (for example at a lock's door)."""

    def __init__(self, point: str) -> None:
        super().__init__()
        self.point = point
        self.reached = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == self.point:
            self.reached.set()


class Many(Recorder):
    """Several checkpoints at once, called in order."""

    def __init__(self, *parts: Recorder) -> None:
        super().__init__()
        self.parts = parts

    def __call__(self, name: str) -> None:
        super().__call__(name)
        for part in self.parts:
            part(name)
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_ids.py`**

```python
import uuid

import pytest

from heterodyne.wsd import ids


def test_session_ids_are_deterministic_uuid5() -> None:
    assert ids.ws_session("alpha") == str(uuid.uuid5(ids.NS, "alpha"))
    assert ids.bead_session("alpha", "btq-1") == str(uuid.uuid5(ids.NS, "alpha:btq-1"))
    assert ids.role_session("btq-1", "coder", "p-one") == str(uuid.uuid5(ids.NS, "btq-1:coder:p-one"))
    assert ids.bead_session("alpha", "btq-1") != ids.bead_session("beta", "btq-1")


def test_namespace_is_pinned() -> None:
    # Changing NS orphans every claim and session: this value must never change.
    assert str(ids.NS) == str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd"))


@pytest.mark.parametrize("bad", ["", "Alpha", "a b", "../x", "a/b", "-x", "x" * 129])
def test_bad_slugs_are_refused(bad: str) -> None:
    with pytest.raises(ids.BadName):
        ids.ws_session(bad)


def test_role_label_overrides_profile() -> None:
    assert ids.profile_for(("role:coder=p-two", "role:review=p-one"), "coder", "p-one") == "p-two"
    assert ids.profile_for((), "coder", "p-one") == "p-one"


def test_conflicting_role_labels_are_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=p-one", "role:coder=p-two"), "coder", "p-one")


def test_bad_profile_in_label_is_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=../x",), "coder", "p-one")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_ids.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd'`.

- [ ] **Step 4: Create `$HZ/src/heterodyne/fsutil.py`**

This is `admind/store.py`'s `private_dir`, moved unchanged so wsd can share it.

```python
"""Filesystem helpers shared by the daemons (admind, wsd)."""

import os
import stat
from pathlib import Path


def private_dir(path: Path) -> None:
    """Create `path` (and missing parents) and make it a 0700 directory owned by this user. A symlink in
    the final component, or a directory someone else owns, is refused: `path` is lstat'ed, opened with
    O_NOFOLLOW | O_DIRECTORY and the two are compared, so the chmod lands on the directory that was
    checked. Parents above `path` are not inspected."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = os.lstat(path)
    if not stat.S_ISDIR(before.st_mode):
        raise PermissionError(f"{path} is not a plain directory (a symlink is refused)")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PermissionError(f"{path} changed while it was checked")
        if opened.st_uid != os.geteuid():
            raise PermissionError(f"{path} is not owned by the service user")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
```

- [ ] **Step 5: Modify `$HZ/src/heterodyne/admind/store.py`**

Delete the whole `def private_dir(path: Path) -> None:` function (the code now in `fsutil.py`), and add this import after `from heterodyne.admind.redact import redact, redact_continuation`:

```python
from heterodyne.fsutil import private_dir
```

Keep `import os` and `import stat`: the rest of `store.py` still uses them. `Store` and everything else that calls `private_dir` keep working through the import.

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/__init__.py`**

```python
"""wsd, the workstream daemon (ADR 0001 §3, §4.3, §5.2): queue, state, pickup, parking and recovery."""
```

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/ids.py`**

```python
"""Deterministic identities (ADR 0001 §4.1, §4.3). Every key here is recomputed, never stored as truth,
so `wsd` finds its own claims and sessions again after a restart or a lost journal."""

import re
import uuid

# The namespace of every wsd uuid5. Changing it orphans every claim and session wsd holds: never change it.
NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd")
SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # btq's routing and session slug rule
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ROLE_LABEL = "role:"


class BadName(ValueError):
    """A workstream, bead, role or profile name that is not a valid slug."""


def slug(value: str, what: str) -> str:
    if not SLUG.fullmatch(value):
        raise BadName(f"{what} is not a valid slug")
    return value


def ws_session(ws: str) -> str:
    """The workstream-session btq worker: it lists ready work and owns the shared pause flag."""
    return str(uuid.uuid5(NS, slug(ws, "workstream")))


def bead_session(ws: str, bead: str) -> str:
    """The per-bead btq worker that claims, owns and parks one bead."""
    return str(uuid.uuid5(NS, f"{slug(ws, 'workstream')}:{slug(bead, 'bead')}"))


def role_session(bead: str, role: str, profile: str) -> str:
    """The logical agent session key for (bead, role, profile) (§4.1)."""
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return str(uuid.uuid5(NS, f"{slug(bead, 'bead')}:{slug(role, 'role')}:{profile}"))


def profile_for(labels: tuple[str, ...], role: str, default: str) -> str:
    """The profile for `role` on a bead: a single `role:<role>=<profile>` label overrides the configured
    default (§4.1, §15 layer 4). Two different overrides for the same role are ambiguous: refused."""
    prefix = f"{ROLE_LABEL}{role}="
    chosen = {label[len(prefix):] for label in labels if label.startswith(prefix)}
    if len(chosen) > 1:
        raise BadName(f"bead has conflicting role:{role} labels")
    profile = chosen.pop() if chosen else default
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return profile
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/checkpoints.py`**

```python
"""Named crash windows. Every journaled flow calls its checkpoint between steps; production passes
`nothing`, and tests pass a callable that raises or blocks at a chosen name (crash-window and
interleaving tests). Each flow lists its names in a `POINTS` tuple, and a test checks that a clean run
passes exactly those, so a new window can't be added without a test that crashes in it."""

from collections.abc import Callable

Checkpoint = Callable[[str], None]


def nothing(_name: str) -> None:
    return None
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_ids.py tests/test_admind_store.py -q`
Expected: `24 passed`.

- [ ] **Step 10: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 11: Commit**

```bash
cd $HZ
git add src/heterodyne/fsutil.py src/heterodyne/admind/store.py src/heterodyne/wsd tests/fakes/checkpoints.py tests/test_wsd_ids.py
git commit -m "feat(wsd): deterministic worker and session IDs, test checkpoints; share private_dir"
```

### Task 2: State model

**Files:**
- Create: `$HZ/src/heterodyne/wsd/states.py`
- Test: `$HZ/tests/test_wsd_states.py`

**Interfaces:**
- Consumes: nothing.
- Produces (`heterodyne.wsd.states`):
  - `class BeadState(StrEnum)`: `CLAIMING, STARTING, RUNNING, PARKING, PARKED, WAITING_INPUT, HELD, STUCK, RESUMING, CLOSED, DROPPED`.
  - `class Reason(StrEnum)`: `CLAIM_UNCERTAIN, CLAIM_LOST, CLAIM_ABANDONED, ROUTING_CHANGED, WORKTREE_FAILED, LAUNCH_FAILED` (the runtime confirmed nothing started), `LAUNCH_UNCERTAIN` (it can't say), `LAUNCH_UNRECORDED` (no readable launched-session record), `STOP_UNCONFIRMED`, `JOURNAL_LOST` (beads show work the journal has no row for), `SESSION_DEAD, RUNTIME_UNAVAILABLE, PARK_FAILED, BLOCKED_ON_BEAD, WAITING_ON_OPERATOR, HELD_BY_OPERATOR, NEEDS_HUMAN, BEADS_UNREACHABLE, ACTIONS_UNRECONCILED, CONFIG_INVALID, UNEXPECTED_STATE`.
  - `class WsState(StrEnum)`: `RUNNING, IDLE, ALL_BLOCKED, PAUSED, HELD, STUCK`.
  - `TERMINAL`, `ACTIVE`, `WAITING`: `frozenset[BeadState]`; `ALLOWED: dict[BeadState | None, frozenset[BeadState]]`.
  - `class IllegalTransition(Exception)`, `allowed(src: BeadState | None, dst: BeadState) -> bool`, `check(src, dst) -> None` (raises `IllegalTransition`).
  - `ws_state(paused: bool, holds: Iterable[Reason], beads: Iterable[BeadState]) -> WsState`.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_states.py`**

```python
import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.wsd.states import (
    ACTIVE,
    ALLOWED,
    TERMINAL,
    BeadState,
    IllegalTransition,
    Reason,
    WsState,
    allowed,
    check,
    ws_state,
)


def test_every_state_has_a_row() -> None:
    assert set(ALLOWED) == set(BeadState) | {None}


def test_every_live_state_can_close() -> None:
    for state in set(BeadState) - TERMINAL:
        assert allowed(state, BeadState.CLOSED)


def test_terminal_states_only_reclaim() -> None:
    for state in TERMINAL:
        assert ALLOWED[state] == {BeadState.CLAIMING}


def test_claiming_never_jumps_to_running() -> None:
    # A claim is never executed until it has been read back as ours and a worktree exists.
    with pytest.raises(IllegalTransition):
        check(BeadState.CLAIMING, BeadState.RUNNING)


def test_only_a_claim_starts_a_row() -> None:
    # wsd's own operations start every bead with a claim; recovery rebuilds rows with `adopt` instead.
    assert ALLOWED[None] == {BeadState.CLAIMING}
    with pytest.raises(IllegalTransition):
        check(None, BeadState.CLOSED)


@pytest.mark.parametrize(("paused", "holds", "beads", "expected"), [
    (False, [], [], WsState.IDLE),
    (False, [], [BeadState.CLOSED, BeadState.DROPPED], WsState.IDLE),
    (False, [], [BeadState.PARKED], WsState.ALL_BLOCKED),
    (False, [], [BeadState.PARKED, BeadState.STUCK], WsState.STUCK),
    (False, [], [BeadState.STUCK, BeadState.RUNNING], WsState.RUNNING),
    (True, [], [BeadState.RUNNING], WsState.PAUSED),
    (True, [Reason.BEADS_UNREACHABLE], [BeadState.RUNNING], WsState.HELD),
])
def test_ws_state_priority(paused: bool, holds: list[Reason], beads: list[BeadState],
                           expected: WsState) -> None:
    assert ws_state(paused, holds, beads) is expected


@given(st.booleans(), st.lists(st.sampled_from(list(BeadState))))
def test_idle_only_when_nothing_is_live(paused: bool, beads: list[BeadState]) -> None:
    state = ws_state(paused, [], beads)
    if state is WsState.IDLE:
        assert not set(beads) - TERMINAL
    if set(beads) & ACTIVE and not paused:
        assert state is WsState.RUNNING
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_states.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.states'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/states.py`**

```python
"""The bead and workstream state model (ADR 0001 §4.3, §5.2) as data plus pure functions.

The journal records one `BeadState` per bead with a reason from a fixed vocabulary, so the Marmot
surface (plan 6) always has a concrete answer to "what is this bead doing, and why". Beads stay the
source of truth (§3.3): these states are rebuilt from beads when the journal is lost.
"""

from collections.abc import Iterable
from enum import StrEnum


class BeadState(StrEnum):
    CLAIMING = "claiming"            # a claim is in flight; never executed until read back as ours
    STARTING = "starting"            # claimed; worktree and launch in progress
    RUNNING = "running"              # an agent session is working on it
    PARKING = "parking"              # the park journal is in progress
    PARKED = "parked"                # waits on blocking beads (claimed, in_progress, v2:parked)
    WAITING_INPUT = "waiting_input"  # parked on an operator question, picker or permission prompt
    HELD = "held"                    # parked by the operator (/stop); resumes only when they release it
    STUCK = "stuck"                  # needs a human; the reason says why
    RESUMING = "resuming"            # the resume journal is in progress
    CLOSED = "closed"
    DROPPED = "dropped"              # not ours: the claim was lost or abandoned


class Reason(StrEnum):
    """Why a bead or a workstream is in its state. Fixed wording lives with the renderer (plan 6)."""
    CLAIM_UNCERTAIN = "claim_uncertain"
    CLAIM_LOST = "claim_lost"
    CLAIM_ABANDONED = "claim_abandoned"
    ROUTING_CHANGED = "routing_changed"
    WORKTREE_FAILED = "worktree_failed"
    LAUNCH_FAILED = "launch_failed"            # the runtime confirmed nothing is running
    LAUNCH_UNCERTAIN = "launch_uncertain"      # the runtime can't say whether the launch started
    LAUNCH_UNRECORDED = "launch_unrecorded"    # no readable launched-session record on the bead
    STOP_UNCONFIRMED = "stop_unconfirmed"      # a session that must end could not be confirmed ended
    JOURNAL_LOST = "journal_lost"              # beads show work wsd's journal has no record of
    SESSION_DEAD = "session_dead"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    PARK_FAILED = "park_failed"
    BLOCKED_ON_BEAD = "blocked_on_bead"
    WAITING_ON_OPERATOR = "waiting_on_operator"
    HELD_BY_OPERATOR = "held_by_operator"
    NEEDS_HUMAN = "needs_human"
    BEADS_UNREACHABLE = "beads_unreachable"
    ACTIONS_UNRECONCILED = "actions_unreconciled"
    CONFIG_INVALID = "config_invalid"
    UNEXPECTED_STATE = "unexpected_state"


class WsState(StrEnum):
    RUNNING = "running"
    IDLE = "idle"
    ALL_BLOCKED = "all_blocked"
    PAUSED = "paused"
    HELD = "held"      # pickup is held for a workstream-level reason (see `holds`)
    STUCK = "stuck"    # nothing runs and at least one bead needs a human


TERMINAL = frozenset({BeadState.CLOSED, BeadState.DROPPED})
ACTIVE = frozenset({BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.PARKING,
                    BeadState.RESUMING})
WAITING = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD})
_PARKED_LIKE = WAITING | {BeadState.STUCK}

# Every non-terminal state may also go to CLOSED: anyone with the right can close a bead at any time.
# Recovery and the per-pickup observation rebuild rows from beads with `Journal.adopt`, which skips this
# table: these are the transitions wsd's own operations make.
ALLOWED: dict[BeadState | None, frozenset[BeadState]] = {
    None: frozenset({BeadState.CLAIMING}),      # wsd's own operations start every bead with a claim
    BeadState.CLAIMING: frozenset({BeadState.STARTING, BeadState.DROPPED, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.STARTING: frozenset({BeadState.RUNNING, BeadState.PARKING, BeadState.STUCK, BeadState.CLOSED}
                                  | WAITING),    # WAITING: shelved, not runnable when its launch came
    BeadState.RUNNING: frozenset({BeadState.PARKING, BeadState.RESUMING, BeadState.STUCK, BeadState.CLOSED}),
    BeadState.PARKING: frozenset({BeadState.PARKED, BeadState.WAITING_INPUT, BeadState.HELD,
                                  BeadState.STUCK, BeadState.CLOSED}),
    BeadState.PARKED: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},
    BeadState.WAITING_INPUT: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},
    BeadState.HELD: _PARKED_LIKE | {BeadState.RESUMING, BeadState.CLOSED},   # RESUMING: only by release
    BeadState.RESUMING: frozenset({BeadState.RUNNING, BeadState.PARKING, BeadState.PARKED,
                                   BeadState.WAITING_INPUT, BeadState.HELD, BeadState.STUCK,
                                   BeadState.CLOSED}),
    BeadState.STUCK: _PARKED_LIKE | {BeadState.RESUMING, BeadState.RUNNING, BeadState.CLOSED},
    BeadState.CLOSED: frozenset({BeadState.CLAIMING}),   # a reopened bead can be claimed again
    BeadState.DROPPED: frozenset({BeadState.CLAIMING}),
}


class IllegalTransition(Exception):
    pass


def allowed(src: BeadState | None, dst: BeadState) -> bool:
    """A same-state "transition" only updates the reason, and is always allowed."""
    return src == dst or dst in ALLOWED[src]


def check(src: BeadState | None, dst: BeadState) -> None:
    if not allowed(src, dst):
        raise IllegalTransition(f"{src} -> {dst}")


def ws_state(paused: bool, holds: Iterable[Reason], beads: Iterable[BeadState]) -> WsState:
    """The one-word workstream state for /workstreams (§6.3). Pickup has already run, so "idle" really
    means nothing is ready and nothing is in progress (§5.2)."""
    states = set(beads) - TERMINAL
    if set(holds):
        return WsState.HELD
    if paused:
        return WsState.PAUSED
    if states & ACTIVE:
        return WsState.RUNNING
    if BeadState.STUCK in states:
        return WsState.STUCK
    if states:
        return WsState.ALL_BLOCKED
    return WsState.IDLE
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_states.py -q`
Expected: `13 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/states.py tests/test_wsd_states.py
git commit -m "feat(wsd): bead and workstream states with concrete reasons and legal transitions"
```

### Task 3: The journal (SQLite inbox, operations, states, holds, progress events)

**Files:**
- Create: `$HZ/src/heterodyne/wsd/journal.py`
- Test: `$HZ/tests/test_wsd_journal.py`

**Interfaces:**
- Consumes: `heterodyne.fsutil.private_dir` (Task 1); `BeadState`, `Reason`, `WsState`, `check` (Task 2).
- Produces (`heterodyne.wsd.journal`):
  - `class JournalCorrupt(Exception)`, `class OpConflict(Exception)`.
  - `class InboxStatus(StrEnum)`: `PENDING, COMMITTED, SUPERSEDED, REJECTED, NEEDS_HUMAN`. `class OpKind(StrEnum)`: `PICKUP, PARK, RESUME, RELEASE, ESCALATE`. `class OpStatus(StrEnum)`: `OPEN, DONE, ABANDONED, STUCK`.
  - Frozen dataclasses: `InboxRow(seq, surface, event_id, kind, ws, bead, payload, status, attempts)`, `Op(op_id, kind, ws, bead, step, data: dict[str, str], attempts, status)`, `BeadRow(ws, bead, state, reason, detail, since)`, `Event(seq, at, ws, bead, kind, detail, ref)`, `Snapshot(ws, state: WsState | None, holds: dict[Reason, str], beads: list[BeadRow], ops: list[Op], last_event: int)`.
  - `state_detail(reason, detail) -> str`: the JSON detail of a `state:<value>` event, `{"reason": …, "detail": …}`.
  - `class Journal(path: Path)`, with `.fresh: bool` and: `close()`, `backup(dest: Path)` (a consistent online copy with SQLite's backup API, written under a temporary name with mode 0600 and renamed into place; **the plan 8 seam**), `transaction()` (re-entrant context manager), `inbox_add(surface, event_id, kind, payload, ws=None, bead=None) -> int | None` (None for a duplicate), `inbox_pending()`, `inbox_get(seq)`, `inbox_failed(seq, limit) -> InboxStatus`, `inbox_finish(seq, status)`, `inbox_pending_count()`, `op_open(kind, ws, bead, data=None) -> Op` (one open op per bead, else `OpConflict`), `op_step(op_id, step, data=None) -> Op`, `op_failed(op_id) -> int`, `op_finish(op_id, status)`, `ops_open(ws=None) -> list[Op]` (in the order they were opened), `op_for(ws, bead) -> Op | None`, `set_state(ws, bead, state, reason=None, detail="", ref=None)` (legal transitions only; emits `state:<value>`), `adopt(ws, bead, state, reason=None, detail="")` (recovery only: writes any state, CLOSED included, from any row or none, without the transition check), `state(ws, bead) -> BeadRow | None`, `states(ws)`, `hold(ws, reason, detail="")`, `unhold(ws, reason)`, `holds(ws) -> dict[Reason, str]`, `set_ws_state(ws, state)`, `emit(ws, bead, kind, detail="", ref=None) -> int`, `events_since(seq, limit=500) -> list[Event]`, `snapshot(ws) -> Snapshot`.

The journal is integrity-checked when opened (`PRAGMA integrity_check`, schema version, expected tables); any failure raises `JournalCorrupt` and the file is not modified. The hypothesis test drives random transition sequences and checks that the journal accepts exactly the legal ones.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_journal.py`**

```python
import sqlite3
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import SimulatedCrash
from hypothesis import given, settings
from hypothesis import strategies as st

from heterodyne.wsd.journal import InboxStatus, Journal, JournalCorrupt, OpConflict, OpKind, OpStatus
from heterodyne.wsd.states import BeadState, IllegalTransition, Reason, WsState, allowed


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "wsd.db")


def test_new_journal_is_fresh_and_private(tmp_path: Path) -> None:
    j = Journal(tmp_path / "state" / "wsd.db")
    assert j.fresh
    assert (tmp_path / "state" / "wsd.db").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    j.close()
    again = Journal(tmp_path / "state" / "wsd.db")
    assert not again.fresh


def test_corrupt_journal_is_refused_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    path.write_bytes(b"not a database at all" * 100)
    before = path.read_bytes()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.read_bytes() == before


def test_foreign_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "wsd.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE other (x)")
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(path)
    assert path.exists()


def test_wrong_schema_version_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "wsd.db").close()
    db = sqlite3.connect(tmp_path / "wsd.db")
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_symlinked_journal_is_refused(tmp_path: Path) -> None:
    Journal(tmp_path / "real.db").close()
    (tmp_path / "wsd.db").symlink_to(tmp_path / "real.db")
    with pytest.raises(JournalCorrupt):
        Journal(tmp_path / "wsd.db")


def test_inbox_dedups_on_surface_and_event(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}", ws="alpha")
    assert seq is not None
    assert journal.inbox_add("marmot", "ev1", "approve", "{}") is None
    assert journal.inbox_add("github", "ev1", "approve", "{}") is not None
    assert [r.event_id for r in journal.inbox_pending()] == ["ev1", "ev1"]


def test_inbox_escalates_after_limit(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.PENDING
    assert journal.inbox_failed(seq, 3) is InboxStatus.NEEDS_HUMAN
    assert journal.inbox_pending_count() == 0
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_inbox_finish_is_once(journal: Journal) -> None:
    seq = journal.inbox_add("marmot", "ev1", "approve", "{}")
    assert seq is not None
    journal.inbox_finish(seq, InboxStatus.SUPERSEDED)
    row = journal.inbox_get(seq)
    assert row is not None and row.status is InboxStatus.SUPERSEDED
    with pytest.raises(KeyError):
        journal.inbox_finish(seq, InboxStatus.COMMITTED)


def test_one_open_op_per_bead(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    with pytest.raises(OpConflict):
        journal.op_open(OpKind.RESUME, "alpha", "btq-1")
    journal.op_finish(op.op_id, OpStatus.DONE)
    journal.op_open(OpKind.RESUME, "alpha", "btq-1")


def test_op_step_merges_data_and_counts_failures(journal: Journal) -> None:
    op = journal.op_open(OpKind.PARK, "alpha", "btq-1", {"blockers": "btq-2"})
    op = journal.op_step(op.op_id, "committed", {"sha": "abc"})
    assert (op.step, op.data) == ("committed", {"blockers": "btq-2", "sha": "abc"})
    assert journal.op_failed(op.op_id) == 1
    assert journal.op_failed(op.op_id) == 2
    journal.op_finish(op.op_id, OpStatus.STUCK)
    with pytest.raises(OpConflict):
        journal.op_step(op.op_id, "labelled")
    assert journal.op_for("alpha", "btq-1") is None


def test_transaction_rolls_back_on_crash(journal: Journal) -> None:
    with pytest.raises(SimulatedCrash), journal.transaction():
        journal.op_open(OpKind.PICKUP, "alpha", "btq-1")
        journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
        raise SimulatedCrash("x")
    assert journal.ops_open() == []
    assert journal.state("alpha", "btq-1") is None
    assert journal.events_since(0, 10) == []


def test_set_state_records_reason_and_event(journal: Journal) -> None:
    for step in (BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING, BeadState.PARKING):
        journal.set_state("alpha", "btq-1", step)
    since = journal.events_since(0, 100)[-1].seq
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    journal.set_state("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD, "btq-2", ref="msg1")
    events = journal.events_since(since, 10)
    assert [(e.kind, e.detail, e.ref) for e in events] == [
        ("state:parked", '{"reason":"blocked_on_bead","detail":"btq-2"}', "msg1")]


def test_adopt_skips_the_transition_check(journal: Journal) -> None:
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.adopt("alpha", "btq-1", BeadState.PARKED, Reason.BLOCKED_ON_BEAD)
    row = journal.state("alpha", "btq-1")
    assert row is not None and row.state is BeadState.PARKED


def test_adopt_closes_a_bead_from_any_row(journal: Journal) -> None:
    # Finding 1 (r1): recovery adopting an externally closed bead must not trip the transition table.
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.set_state("alpha", "btq-1", BeadState.STARTING)
    journal.set_state("alpha", "btq-1", BeadState.RUNNING)
    journal.adopt("alpha", "btq-1", BeadState.CLOSED)
    journal.adopt("alpha", "btq-2", BeadState.CLOSED)        # no row at all
    assert [r.state for r in journal.states("alpha")] == [BeadState.CLOSED, BeadState.CLOSED]


def test_backup_is_a_coherent_private_copy(journal: Journal, tmp_path: Path) -> None:
    journal.set_state("alpha", "btq-1", BeadState.CLAIMING)
    journal.op_open(OpKind.PICKUP, "alpha", "btq-1")
    dest = tmp_path / "backups" / "wsd.db"
    dest.parent.mkdir()
    journal.backup(dest)
    assert dest.stat().st_mode & 0o777 == 0o600
    assert not list(dest.parent.glob(".*.part"))
    copy = Journal(dest)            # opens: the integrity and schema checks pass on the copy
    assert copy.state("alpha", "btq-1") is not None and len(copy.ops_open("alpha")) == 1
    copy.close()


def test_holds_and_snapshot(journal: Journal) -> None:
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.hold("alpha", Reason.BEADS_UNREACHABLE, "RuntimeError")
    journal.set_ws_state("alpha", WsState.HELD)
    snap = journal.snapshot("alpha")
    assert snap.holds == {Reason.BEADS_UNREACHABLE: "RuntimeError"}
    assert snap.state is WsState.HELD
    journal.unhold("alpha", Reason.BEADS_UNREACHABLE)
    assert [e.kind for e in journal.events_since(0, 10)] == ["hold", "ws:held", "unhold"]


def test_events_cursor(journal: Journal) -> None:
    first = journal.emit("alpha", None, "a")
    journal.emit("alpha", "btq-1", "b")
    assert [e.kind for e in journal.events_since(first, 10)] == ["b"]


def test_concurrent_writers_serialize(journal: Journal) -> None:
    def work(n: int) -> None:
        for i in range(50):
            journal.emit("alpha", f"b{n}", f"e{i}")
    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(journal.events_since(0, 1000)) == 200


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(list(BeadState)), max_size=25))
def test_journal_only_records_legal_transitions(tmp_path_factory: pytest.TempPathFactory,
                                                steps: list[BeadState]) -> None:
    journal = Journal(tmp_path_factory.mktemp("j") / "wsd.db")
    current: BeadState | None = None
    for step in steps:
        if allowed(current, step):
            journal.set_state("alpha", "btq-1", step)
            current = step
        else:
            with pytest.raises(IllegalTransition):
                journal.set_state("alpha", "btq-1", step)
        row = journal.state("alpha", "btq-1")
        assert (None if row is None else row.state) == current
    kinds = [e.kind for e in journal.events_since(0, 1000)]
    assert len(kinds) == len([k for k in kinds if k.startswith("state:")])
    journal.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_journal.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.journal'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/journal.py`**

```python
"""wsd's SQLite journal (ADR 0001 §3.3, §5.4): one file in WAL mode under the wsd state directory.

What it holds, and what it never does:
- `inbox`: decision events keyed by (surface, event ID), `pending` until committed, superseded,
  rejected or escalated. The decision itself lives on the bead (§5.4); the inbox only orders and dedups.
- `ops`: the pickup, park, resume, release and escalation journals, one row per operation with its last
  completed step, so a crash at any point is replayed from the step it reached (§4.3).
- `beads`, `holds`, `workstreams`: the state every surface shows (plan 6), each with a concrete reason.
- `events`: an append-only progress log with a cursor, for per-message progress reactions (plan 6). A
  state event's `detail` is JSON with the reason and its concrete detail (blocker IDs, the failing step).

Beads stay the source of truth. Opening the journal checks it first: an unreadable, corrupt or
foreign file is refused (JournalCorrupt) and never deleted or recreated, because a lost journal must be
noticed, not papered over. A missing file is created atomically and reported as `fresh`. `backup()`
writes a consistent online copy (SQLite's backup API, safe under WAL) for the beads backups (§3.3).
"""

import contextlib
import functools
import os
import sqlite3
import stat
import threading
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

import msgspec

from heterodyne.fsutil import private_dir
from heterodyne.wsd.states import BeadState, Reason, WsState, check

SCHEMA_VERSION = "1"
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE inbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    surface TEXT NOT NULL,
    event_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ws TEXT,
    bead TEXT,
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed', 'superseded', 'rejected', 'needs_human')),
    attempts INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (surface, event_id));
CREATE TABLE ops (
    op_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('pickup', 'park', 'resume', 'release', 'escalate')),
    ws TEXT NOT NULL,
    bead TEXT NOT NULL,
    step TEXT NOT NULL,
    data TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('open', 'done', 'abandoned', 'stuck')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX ops_one_open ON ops (ws, bead) WHERE status = 'open';
CREATE TABLE beads (
    ws TEXT NOT NULL, bead TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, detail TEXT NOT NULL,
    since TEXT NOT NULL, PRIMARY KEY (ws, bead));
CREATE TABLE holds (
    ws TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT NOT NULL, since TEXT NOT NULL,
    PRIMARY KEY (ws, reason));
CREATE TABLE workstreams (ws TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, ws TEXT NOT NULL, bead TEXT,
    kind TEXT NOT NULL, detail TEXT NOT NULL, ref TEXT);
"""
TABLES = frozenset({"meta", "inbox", "ops", "beads", "holds", "workstreams", "events"})


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JournalCorrupt(Exception):
    """The journal failed its integrity check. wsd refuses to start; the file is left for the operator."""


class OpConflict(Exception):
    """An operation is already open for this bead: finish or replay it first."""


class InboxStatus(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    NEEDS_HUMAN = "needs_human"


class OpKind(StrEnum):
    PICKUP = "pickup"
    PARK = "park"
    RESUME = "resume"
    RELEASE = "release"
    ESCALATE = "escalate"


class OpStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    ABANDONED = "abandoned"
    STUCK = "stuck"


@dataclass(frozen=True)
class InboxRow:
    seq: int
    surface: str
    event_id: str
    kind: str
    ws: str | None
    bead: str | None
    payload: str
    status: InboxStatus
    attempts: int


@dataclass(frozen=True)
class Op:
    op_id: str
    kind: OpKind
    ws: str
    bead: str
    step: str
    data: dict[str, str]
    attempts: int
    status: OpStatus


@dataclass(frozen=True)
class BeadRow:
    ws: str
    bead: str
    state: BeadState
    reason: Reason | None
    detail: str
    since: str


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    ws: str
    bead: str | None
    kind: str
    detail: str
    ref: str | None


@dataclass(frozen=True)
class Snapshot:
    """Everything a status surface needs about one workstream, read in one transaction."""
    ws: str
    state: WsState | None
    holds: dict[Reason, str]
    beads: list[BeadRow]
    ops: list[Op]
    last_event: int


_DATA = msgspec.json.Decoder(dict[str, str])


def state_detail(reason: Reason | None, detail: str) -> str:
    """A state event's detail: the reason and its concrete detail, as JSON (plan 6 renders it)."""
    return msgspec.json.encode({"reason": reason.value if reason else "", "detail": detail}).decode()
_INBOX = "seq, surface, event_id, kind, ws, bead, payload, status, attempts"
_OP = "op_id, kind, ws, bead, step, data, attempts, status"


def _opt(v: object) -> str | None:
    return None if v is None else str(v)


def _inbox(r: tuple[object, ...]) -> InboxRow:
    return InboxRow(int(cast(int, r[0])), str(r[1]), str(r[2]), str(r[3]), _opt(r[4]), _opt(r[5]), str(r[6]),
                    InboxStatus(str(r[7])), int(cast(int, r[8])))


def _op(r: tuple[object, ...]) -> Op:
    return Op(str(r[0]), OpKind(str(r[1])), str(r[2]), str(r[3]), str(r[4]), _DATA.decode(str(r[5])),
              int(cast(int, r[6])), OpStatus(str(r[7])))


def _bead(r: tuple[object, ...]) -> BeadRow:
    reason = _opt(r[3])
    return BeadRow(str(r[0]), str(r[1]), BeadState(str(r[2])), None if reason is None else Reason(reason),
                   str(r[4]), str(r[5]))


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Serialize a Journal method: wsd calls it from the event loop and from worker threads."""
    @functools.wraps(method)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0].lock:  # type: ignore[attr-defined]  # args[0] is the Journal
            return method(*args, **kwargs)
    return wrapper


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _create(path: Path) -> None:
    """Build a complete journal under a temporary name and rename it into place, so `path` either does
    not exist or holds a whole schema: a crash can never leave a half-made journal that looks fresh."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.new")
    db = sqlite3.connect(tmp, isolation_level=None)
    try:
        db.executescript("BEGIN;" + SCHEMA + "COMMIT;")
        db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?), ('created_at', ?)",
                   (SCHEMA_VERSION, now()))
    finally:
        db.close()
    tmp.chmod(0o600)
    os.link(tmp, path)      # fails if `path` appeared meanwhile: never overwrite a journal
    tmp.unlink()


def _check(db: sqlite3.Connection) -> None:
    rows = db.execute("PRAGMA integrity_check").fetchall()
    if [tuple(r) for r in rows] != [("ok",)]:
        raise JournalCorrupt("integrity_check failed")
    tables = {str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not TABLES <= tables:
        raise JournalCorrupt("tables missing")
    row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None or str(row[0]) != SCHEMA_VERSION:
        raise JournalCorrupt("unknown schema version")


class Journal:
    def __init__(self, path: Path) -> None:
        private_dir(path.parent)
        self.fresh = False
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            _create(path)
            self.fresh = True
        else:
            if not stat.S_ISREG(st.st_mode):
                raise JournalCorrupt("the journal path is not a regular file")
        self.lock = threading.RLock()
        self._depth = 0
        try:
            self.db = _connect(path)
            _check(self.db)
        except sqlite3.DatabaseError as exc:
            raise JournalCorrupt(type(exc).__name__) from None

    @_locked
    def close(self) -> None:
        self.db.close()

    @_locked
    def backup(self, dest: Path) -> None:
        """A consistent copy of the journal at `dest` (0600), taken online with SQLite's backup API, so
        it is safe while wsd runs in WAL mode. Written under a temporary name and renamed into place."""
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        os.close(fd)
        try:
            copy = sqlite3.connect(tmp)
            try:
                self.db.backup(copy)
            finally:
                copy.close()
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @contextlib.contextmanager
    def transaction(self) -> Generator[None]:
        """All the block's journal writes commit, or (on any exception, a simulated crash included)
        none do. Re-entrant: a nested block joins the outer one."""
        with self.lock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.db.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    # --- inbox (§5.4) ---

    @_locked
    def inbox_add(self, surface: str, event_id: str, kind: str, payload: str,
                  ws: str | None = None, bead: str | None = None) -> int | None:
        """Insert a `pending` event; None if (surface, event_id) was already seen (a replay)."""
        at = now()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO inbox (surface, event_id, kind, ws, bead, payload, status, received_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (surface, event_id, kind, ws, bead, payload, at, at))
        return None if cur.rowcount == 0 else cur.lastrowid

    @_locked
    def inbox_pending(self) -> list[InboxRow]:
        rows = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE status = 'pending' "  # noqa: S608
                               "ORDER BY seq").fetchall()
        return [_inbox(r) for r in rows]

    @_locked
    def inbox_get(self, seq: int) -> InboxRow | None:
        row = self.db.execute(f"SELECT {_INBOX} FROM inbox WHERE seq = ?", (seq,)).fetchone()  # noqa: S608
        return None if row is None else _inbox(row)

    @_locked
    def inbox_failed(self, seq: int, limit: int) -> InboxStatus:
        """Count one failed attempt; the `limit`th failure escalates the event to `needs_human`."""
        with self.transaction():
            self.db.execute("UPDATE inbox SET attempts = attempts + 1, updated_at = ? "
                            "WHERE seq = ? AND status = 'pending'", (now(), seq))
            self.db.execute("UPDATE inbox SET status = 'needs_human' "
                            "WHERE seq = ? AND status = 'pending' AND attempts >= ?", (seq, limit))
            row = self.db.execute("SELECT status FROM inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            raise KeyError(seq)
        return InboxStatus(str(row[0]))

    @_locked
    def inbox_finish(self, seq: int, status: InboxStatus) -> None:
        if status is InboxStatus.PENDING:
            raise ValueError("finish needs a final status")
        cur = self.db.execute("UPDATE inbox SET status = ?, updated_at = ? "
                              "WHERE seq = ? AND status = 'pending'", (status.value, now(), seq))
        if cur.rowcount != 1:
            raise KeyError(seq)

    # --- operation journals (§4.3) ---

    @_locked
    def op_open(self, kind: OpKind, ws: str, bead: str, data: dict[str, str] | None = None) -> Op:
        op_id = uuid.uuid4().hex
        at = now()
        try:
            self.db.execute(
                "INSERT INTO ops (op_id, kind, ws, bead, step, data, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'intent', ?, 'open', ?, ?)",
                (op_id, kind.value, ws, bead, msgspec.json.encode(data or {}).decode(), at, at))
        except sqlite3.IntegrityError:
            raise OpConflict(f"an operation is already open for {bead}") from None
        return self._op(op_id)

    def _op(self, op_id: str) -> Op:
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE op_id = ?", (op_id,)).fetchone()  # noqa: S608
        if row is None:
            raise KeyError(op_id)
        return _op(row)

    @_locked
    def op_step(self, op_id: str, step: str, data: dict[str, str] | None = None) -> Op:
        """Record that `step` completed, merging `data` (a SHA, a worktree path) into the op's data."""
        with self.transaction():
            op = self._op(op_id)
            if op.status is not OpStatus.OPEN:
                raise OpConflict(f"operation {op_id} is {op.status}")
            merged = {**op.data, **(data or {})}
            self.db.execute("UPDATE ops SET step = ?, data = ?, updated_at = ? WHERE op_id = ?",
                            (step, msgspec.json.encode(merged).decode(), now(), op_id))
            return self._op(op_id)

    @_locked
    def op_failed(self, op_id: str) -> int:
        """Count one failed attempt and return the total."""
        self.db.execute("UPDATE ops SET attempts = attempts + 1, updated_at = ? WHERE op_id = ?",
                        (now(), op_id))
        return self._op(op_id).attempts

    @_locked
    def op_finish(self, op_id: str, status: OpStatus) -> None:
        if status is OpStatus.OPEN:
            raise ValueError("finish needs a final status")
        self.db.execute("UPDATE ops SET status = ?, updated_at = ? WHERE op_id = ? AND status = 'open'",
                        (status.value, now(), op_id))

    @_locked
    def ops_open(self, ws: str | None = None) -> list[Op]:
        if ws is None:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' "  # noqa: S608
                                   "ORDER BY rowid")
        else:
            rows = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? "  # noqa: S608
                                   "ORDER BY rowid", (ws,))
        return [_op(r) for r in rows.fetchall()]

    @_locked
    def op_for(self, ws: str, bead: str) -> Op | None:
        row = self.db.execute(f"SELECT {_OP} FROM ops WHERE status = 'open' AND ws = ? AND bead = ?",  # noqa: S608
                              (ws, bead)).fetchone()
        return None if row is None else _op(row)

    # --- states, holds, events (plan 6 reads these) ---

    @_locked
    def set_state(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
                  detail: str = "", ref: str | None = None) -> None:
        """Move a bead to `state` (a legal transition only) and append the matching progress event."""
        with self.transaction():
            current = self.state(ws, bead)
            check(None if current is None else current.state, state)
            self._put(ws, bead, current, state, reason, detail, ref)

    @_locked
    def adopt(self, ws: str, bead: str, state: BeadState, reason: Reason | None = None,
              detail: str = "", ref: str | None = None) -> None:
        """The rebuild from beads and the runtime (§3.3, and each pickup's observation): beads are the
        truth, so the cached row is replaced without a transition check. It still emits the event."""
        with self.transaction():
            self._put(ws, bead, self.state(ws, bead), state, reason, detail, ref)

    def _put(self, ws: str, bead: str, current: BeadRow | None, state: BeadState, reason: Reason | None,
             detail: str, ref: str | None) -> None:
        if current is not None and (current.state, current.reason, current.detail) == (state, reason, detail):
            return
        self.db.execute(
            "INSERT INTO beads (ws, bead, state, reason, detail, since) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ws, bead) DO UPDATE SET state = excluded.state, reason = excluded.reason, "
            "detail = excluded.detail, since = excluded.since",
            (ws, bead, state.value, None if reason is None else reason.value, detail, now()))
        self.emit(ws, bead, f"state:{state.value}", state_detail(reason, detail), ref)

    @_locked
    def state(self, ws: str, bead: str) -> BeadRow | None:
        row = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads "
                              "WHERE ws = ? AND bead = ?", (ws, bead)).fetchone()
        return None if row is None else _bead(row)

    @_locked
    def states(self, ws: str) -> list[BeadRow]:
        rows = self.db.execute("SELECT ws, bead, state, reason, detail, since FROM beads WHERE ws = ? "
                               "ORDER BY bead", (ws,)).fetchall()
        return [_bead(r) for r in rows]

    @_locked
    def hold(self, ws: str, reason: Reason, detail: str = "") -> None:
        with self.transaction():
            if self.holds(ws).get(reason) == detail:
                return
            self.db.execute("INSERT INTO holds (ws, reason, detail, since) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT (ws, reason) DO UPDATE SET detail = excluded.detail",
                            (ws, reason.value, detail, now()))
            self.emit(ws, None, "hold", state_detail(reason, detail))

    @_locked
    def unhold(self, ws: str, reason: Reason) -> None:
        with self.transaction():
            cur = self.db.execute("DELETE FROM holds WHERE ws = ? AND reason = ?", (ws, reason.value))
            if cur.rowcount:
                self.emit(ws, None, "unhold", reason.value)

    @_locked
    def holds(self, ws: str) -> dict[Reason, str]:
        rows = self.db.execute("SELECT reason, detail FROM holds WHERE ws = ? ORDER BY reason",
                               (ws,)).fetchall()
        return {Reason(str(r[0])): str(r[1]) for r in rows}

    @_locked
    def set_ws_state(self, ws: str, state: WsState) -> None:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            if row is not None and str(row[0]) == state.value:
                return
            self.db.execute("INSERT INTO workstreams (ws, state, updated_at) VALUES (?, ?, ?) "
                            "ON CONFLICT (ws) DO UPDATE SET state = excluded.state, "
                            "updated_at = excluded.updated_at",
                            (ws, state.value, now()))
            self.emit(ws, None, f"ws:{state.value}")

    @_locked
    def emit(self, ws: str, bead: str | None, kind: str, detail: str = "", ref: str | None = None) -> int:
        cur = self.db.execute("INSERT INTO events (at, ws, bead, kind, detail, ref) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (now(), ws, bead, kind, detail, ref))
        return int(cast(int, cur.lastrowid))

    @_locked
    def inbox_pending_count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM inbox WHERE status = 'pending'").fetchone()
        return int(cast(int, row[0]))

    @_locked
    def events_since(self, seq: int, limit: int = 500) -> list[Event]:
        rows = self.db.execute("SELECT seq, at, ws, bead, kind, detail, ref FROM events WHERE seq > ? "
                               "ORDER BY seq LIMIT ?", (seq, limit)).fetchall()
        return [Event(int(cast(int, r[0])), str(r[1]), str(r[2]), _opt(r[3]), str(r[4]), str(r[5]),
                      _opt(r[6])) for r in rows]

    @_locked
    def snapshot(self, ws: str) -> Snapshot:
        with self.transaction():
            row = self.db.execute("SELECT state FROM workstreams WHERE ws = ?", (ws,)).fetchone()
            last = self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
            state = None if row is None else WsState(str(row[0]))
            return Snapshot(ws, state, self.holds(ws), self.states(ws), self.ops_open(ws),
                            int(cast(int, last[0])))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_journal.py -q`
Expected: `19 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/journal.py tests/test_wsd_journal.py
git commit -m "feat(wsd): SQLite journal with inbox, operations, holds and progress events"
```

### Task 4: The beads adapter, through btq

**Files:**
- Create: `$HZ/docs/spikes/S6-beads-json.md`, `$HZ/src/heterodyne/wsd/btq.py`, `$HZ/src/heterodyne/wsd/gitwip.py`, `$HZ/src/heterodyne/wsd/beads.py`, `$HZ/tests/fakes/fake_btq.py`, `$HZ/tests/wsd_env.py`
- Test: `$HZ/tests/test_wsd_beads.py`, `$HZ/tests/test_wsd_gitwip.py`

**Interfaces:**
- Consumes: `ids.ws_session`, `ids.bead_session`, `ids.SLUG` (Task 1).
- Produces:
  - `heterodyne.wsd.btq`: `AGENT = "wsd"`, `class BtqUnavailable(Exception)`, `class QueueLike(Protocol)` (`worker: str`, `state: Path`, `bd(*args)`, `show(id)`, `ready()`, `claim(id)`, `owned(id, statuses=("in_progress",))`, `worktree(id, repository)`, `exclusive()`), `QueueFactory = Callable[[str, str], QueueLike]` (workstream, btq session), `load(checkout: Path) -> ModuleType`, `factory(module, locations: Mapping[str, str]) -> QueueFactory`.
  - `heterodyne.wsd.gitwip`: `class GitFailed(Exception)`, `git(path, *args) -> str`, `branch(path) -> str`, `toplevel(path) -> Path`, `find_wip(worktree, mark) -> str | None`, `wip_commit(worktree, mark, summary) -> str` (idempotent per mark; returns the SHA).
  - `heterodyne.wsd.beads`: labels `PARKED = "v2:parked"`, `HELD = "v2:held"`, `NEEDS_HUMAN = "needs-human"`, `OPERATOR_INPUT_LABELS`, `NON_BLOCKING_DEPS`; `RECORD_KEY = "wsd_session"`; `PROVENANCE` (btq's worktree provenance comment); exceptions `BeadsUnavailable`, `UnexpectedShape(BeadsUnavailable)`, `ClaimRefused`, `ClaimUncertain`, `RoutingChanged`, `NotOurs`, `WorktreeConflict`, `RecordUnreadable`; `class SessionRecord(msgspec.Struct)`: `role, profile, session_key, repo, worktree` (**the §3.3 launched-session record**; unknown fields ignored), `encode_record(record) -> str`; `class ClaimView(StrEnum)`: `OURS, FREE, OTHER`; frozen `Dep(id, status, kind, labels)` with `.blocking`; frozen `Bead(id, title, status, assignee, labels, metadata, deps)` with `open_blockers()`, `waits_on_operator()`, `record() -> SessionRecord | None` (raises `RecordUnreadable`) and `provenance(worker) -> list[tuple[str, str]]`; `parse(raw, detail) -> Bead`; `class BeadsAdapter(factory: QueueFactory)` with `ws_queue(ws)`, `bead_queue(ws, bead)`, `ready(ws) -> list[Bead]`, `show(ws, bead) -> Bead`, `exists(ws, bead) -> bool`, `read_claim(ws, bead) -> ClaimView`, `ours(ws) -> list[Bead]` (by assignee, whatever the labels), `with_metadata(ws, key, settled: frozenset[str]) -> list[str]` (closed beads included), `validate(ws, bead) -> Bead` (btq's post-claim checks, as the bead's worker: `NotOurs` or `RoutingChanged`), `comments(ws, bead) -> list[str]`, `paused(ws) -> bool`, `set_paused(ws, paused)`, `claim(ws, bead) -> Bead`, `ensure_label(ws, bead, label, present=True)`, `ensure_blocker(ws, bead, blocker)`, `ensure_comment(ws, bead, mark, text)`, `ensure_record(ws, bead, record)`, `ensure_metadata(ws, bead, key, value)`, `worktree(ws, bead, repository: Path) -> Path`, `verify_worktree(ws, bead, repository, worktree) -> Path` (provenance or git common directory, else `WorktreeConflict`).
  - `tests/fakes/fake_btq.py`: `FakeBead`, `Fault(call, exc, after=False, times=1, bead=None)`, `World(state_root)` (`beads`, `down`, `faults`, `calls`, `claims`, `stolen`, `worktrees`, `approvals`; `add(id, ws="alpha", **kw)`, `fault(call, exc, after=False, times=1, bead=None)`, `close(id)`), `FakeQueue(world, ws, session)`, `factory(world) -> QueueFactory`.
  - `tests/wsd_env.py`: `WS = "alpha"`, `git_repo(path) -> Path`.

- [ ] **Step 1: Record the bd 1.1 JSON shapes (spike S6)**

The adapter must accept exactly the shapes bd produces and refuse anything else. Record them against a **throwaway** database, never the real queue:

```bash
export SPIKE=$(mktemp -d) && mkdir -p "$SPIKE/home" "$SPIKE/proj" && cd "$SPIKE/proj" && git init -q
export HOME="$SPIKE/home" BD_NON_INTERACTIVE=1
bd init --prefix s6 --non-interactive
a=$(bd create first --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
b=$(bd create second --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
bd show "$a" --json; bd list --json --limit 0
bd label add "$a" v2:parked --json; bd label add "$a" v2:parked --json
bd dep add "$a" "$b" --json; bd dep add "$a" "$b" --json
bd show "$a" --json; bd list --json --limit 0
bd comments "$b" --json; bd comments add "$a" hello --json; bd comments add "$a" hello --json; bd comments "$a" --json
bd update "$a" --set-metadata action_state=executing --json
bd show s6-missing --json; bd label add s6-missing x --json; echo "exit $?"
```

Write the findings to `$HZ/docs/spikes/S6-beads-json.md`. On bd 1.1.0 they are as below; if your bd differs, stop and update `beads.parse` and the fake to match before going on.

```markdown
# S6: bd 1.1 JSON shapes wsd relies on (plan 3, Task 4)

Run against bd 1.1.0 on 2026-10-04, in a throwaway embedded database (`bd init` in a temp directory with a temp `HOME`). No real queue was read or written. The fake queue (`tests/fakes/fake_btq.py`) answers in these shapes, and `heterodyne.wsd.beads.parse` refuses anything else (UnexpectedShape, which wsd treats as beads being unavailable).

## Beads

- `bd show <id> --json` and `bd list --json` both return a **list** of objects; `show` has exactly one.
- Always present: `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_count`, `dependent_count`, `comment_count`.
- **Omitted when empty:** `labels` (no labels), `metadata` (no metadata), `assignee` (unassigned) and `dependencies` (when `dependency_count` is 0). An absent key means empty only for these; wsd reads a missing `dependencies` with a non-zero `dependency_count` as a malformed answer, never as "no blockers".
- `metadata` is an object of strings (`bd update <id> --set-metadata k=v` returns the updated bead list).

## Dependencies

- In `show`, `dependencies` lists the beads depended on, each with `id`, `title`, `status`, `priority`, `issue_type`, `created_at`, `created_by`, `updated_at`, `dependency_type`, and `labels` when that bead has any.
- In `list`, `dependencies` is a list of **edges** instead: `issue_id`, `depends_on_id`, `type`, `created_at`, `created_by`, `metadata` (a JSON string). wsd reads blockers only from `show`.
- `bd dep add A B` (A depends on B, type `blocks`) returns `{"issue_id", "depends_on_id", "type", "status": "added", "schema_version"}`. Adding the same edge again also reports `added` and creates no second edge: idempotent.

## Labels

- `bd label add <id> <label>` returns `[{"issue_id", "label", "status": "added"}]`, also when the label is already there (idempotent). `label remove` of an absent label reports `removed`.
- **`bd label add` on a missing bead exits 0** with `[]` and an error line on stderr. So a label write is never trusted from its exit status: wsd reads every write back (`BeadsAdapter.ensure_*`).

## Comments

- `bd comments <id> --json` returns a list of `{"id", "issue_id", "author", "text", "created_at"}`; `[]` when there are none.
- `bd comments add <id> <text>` is **not idempotent**: the same text twice makes two comments. wsd puts a unique mark (`wsd-park: <op id>`) in each comment and looks for it before adding.

## Not found

- `bd show <missing>` exits 1 with `no issue found matching "<id>"` (btq raises it as RuntimeError). `dep add` and `comments` on a missing bead exit 1 with the same phrase inside a JSON `error`. wsd treats only this phrase as "absent"; any other failure is BeadsUnavailable.
```

- [ ] **Step 2: Create `$HZ/tests/fakes/fake_btq.py`**

The fake follows btq's claim rules and error messages, and answers in the S6 shapes. `fault(..., after=True)` performs the write and then fails, which is how an uncertain write looks to wsd. `stolen` makes another worker win the claim race.

```python
"""An in-memory beads queue with btq's `Queue` interface (the `QueueLike` slice wsd uses).

It answers in the bd 1.1 JSON shapes recorded in spike S6 (`docs/spikes/S6-beads-json.md`): empty
`labels` and `metadata` are omitted, `dependencies` is omitted when `dependency_count` is 0, and `list`
gives dependency edges where `show` gives the beads depended on. Claims, ownership, routing (`matches`),
the design gate (`design_allowed`, simplified: a `kind:task` needs a `design_approval` the world
accepts), worktree provenance notes and the pause flag follow btq's rules and error messages. Faults are
injected per call name, optionally for one bead only; `after=True` performs the write and then fails,
which is how an uncertain write looks to wsd.
"""

import contextlib
import hashlib
import subprocess
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOST = "testhost"


@dataclass
class FakeBead:
    id: str
    title: str = "a task"
    status: str = "open"
    assignee: str | None = None
    labels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    deps: list[tuple[str, str]] = field(default_factory=list)      # (other bead, dependency type)
    comments: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Fault:
    call: str
    exc: Exception
    after: bool = False
    times: int = 1
    bead: str | None = None      # only calls about this bead


class World:
    def __init__(self, state_root: Path) -> None:
        self.beads: dict[str, FakeBead] = {}
        self.state_root = state_root
        self.down = False
        self.faults: list[Fault] = []
        self.calls: list[tuple[str, str]] = []        # (worker, call)
        self.claims: list[str] = []
        self.stolen: set[str] = set()      # beads another worker claims first (a lost race)
        self.worktrees: list[str] = []
        self.approvals = {"approval-1"}     # design approvals btq's gate accepts
        self.lock = threading.RLock()

    def add(self, bead_id: str, ws: str = "alpha", **kw: Any) -> FakeBead:
        labels = kw.pop("labels", [])
        kind = kw.pop("kind", "task")
        metadata = {"design_approval": "approval-1", **kw.pop("metadata", {})}
        bead = FakeBead(bead_id, labels=["agent:wsd", f"ws:{ws}", f"kind:{kind}", *labels],
                        metadata=metadata, **kw)
        self.beads[bead_id] = bead
        return bead

    def fault(self, call: str, exc: Exception, after: bool = False, times: int = 1,
              bead: str | None = None) -> None:
        self.faults.append(Fault(call, exc, after, times, bead))

    def _take(self, call: str, after: bool, bead: str | None) -> Exception | None:
        for f in self.faults:
            if (f.call == call and f.after == after and f.times > 0
                    and (f.bead is None or f.bead == bead)):
                f.times -= 1
                return f.exc
        return None

    def check(self, call: str, worker: str, bead: str | None = None) -> None:
        self.calls.append((worker, call))
        if self.down:
            raise RuntimeError("dolt: connection refused")
        exc = self._take(call, after=False, bead=bead)
        if exc is not None:
            raise exc

    def check_after(self, call: str, bead: str | None = None) -> None:
        exc = self._take(call, after=True, bead=bead)
        if exc is not None:
            raise exc

    def blocked(self, bead: FakeBead) -> bool:
        return any(kind not in ("parent-child", "related", "discovered-from")
                   and self.beads[other].status != "closed" for other, kind in bead.deps)

    def ready_for(self, ws: str) -> list[FakeBead]:
        return [b for b in sorted(self.beads.values(), key=lambda b: b.id)
                if b.status == "open" and b.assignee is None and "agent:wsd" in b.labels
                and f"ws:{ws}" in b.labels and not self.blocked(b)]

    def close(self, bead_id: str) -> None:
        self.beads[bead_id].status = "closed"

    def json(self, bead: FakeBead, detail: bool) -> dict[str, Any]:
        out: dict[str, Any] = {"id": bead.id, "title": bead.title, "status": bead.status,
                               "priority": 2, "issue_type": "task",
                               "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
        if bead.assignee:
            out["assignee"] = bead.assignee
        if bead.labels:
            out["labels"] = list(bead.labels)
        if bead.metadata:
            out["metadata"] = dict(bead.metadata)
        if bead.notes:
            out["notes"] = bead.notes
        out["dependency_count"] = len(bead.deps)
        if not detail and bead.deps:        # `bd list` gives the edges, not the beads they point at
            out["dependencies"] = [{"issue_id": bead.id, "depends_on_id": other, "type": kind,
                                    "created_at": "2026-10-01T00:00:00Z", "metadata": "{}"}
                                   for other, kind in bead.deps]
        if detail and bead.deps:
            deps: list[dict[str, Any]] = []
            for other, kind in bead.deps:
                o = self.beads[other]
                dep: dict[str, Any] = {"id": o.id, "title": o.title, "status": o.status,
                                       "dependency_type": kind}
                if o.labels:
                    dep["labels"] = list(o.labels)
                deps.append(dep)
            out["dependencies"] = deps
        return out


class FakeQueue:
    def __init__(self, world: World, ws: str, session: str) -> None:
        self.world = world
        self.ws = ws
        self.worker = f"wsd:{HOST}:{session}"
        self.state = world.state_root / hashlib.sha256(self.worker.encode()).hexdigest()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._exclusive = threading.Lock()

    def _get(self, bead_id: str) -> FakeBead:
        bead = self.world.beads.get(bead_id)
        if bead is None:
            raise RuntimeError(f"Error: no issue found matching {bead_id!r}")
        return bead

    def show(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("show", self.worker, issue_id)
            return self.world.json(self._get(issue_id), detail=True)

    def ready(self) -> Any:
        with self.world.lock:
            self.world.check("ready", self.worker)
            if (self.state / "paused").exists():
                return []
            return [self.world.json(b, detail=False) for b in self.world.ready_for(self.ws)]

    def claim(self, issue_id: str) -> Any:
        with self.world.lock:
            self.world.check("claim", self.worker, issue_id)
            bead = self._get(issue_id)
            if issue_id in self.world.stolen:
                bead.status, bead.assignee = "in_progress", "codex:otherhost:x"
            mine = [b for b in self.world.beads.values() if b.assignee == self.worker]
            if any(b.status == "in_progress" for b in mine):
                raise ValueError("Finish or release this worker's existing claim first")
            if bead not in self.world.ready_for(self.ws):
                raise ValueError("Task is not eligible for this worker")
            bead.status, bead.assignee = "in_progress", self.worker
            self.world.claims.append(issue_id)
            self.world.check_after("claim", issue_id)
            return self.world.json(bead, detail=True)

    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any:
        with self.world.lock:
            self.world.check("owned", self.worker, issue_id)
            bead = self._get(issue_id)
            if bead.status not in statuses or bead.assignee != self.worker:
                raise ValueError("Task is not in progress under this worker")
            return self.world.json(bead, detail=True)

    def worktree(self, issue_id: str, repository: str) -> Any:
        with self.world.lock:
            self.world.check("worktree", self.worker, issue_id)
            self.owned(issue_id)
            repo = Path(repository).resolve(strict=True)
            dest = repo.parent / f"{repo.name}-btq-{issue_id}"
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                  capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"btq/{issue_id}",
                            str(dest), base], check=True, capture_output=True)
            self.world.worktrees.append(issue_id)
            self.world.check_after("worktree", issue_id)     # git made it, the provenance note failed
            bead = self._get(issue_id)
            line = f"worker={self.worker}; repository={repo}; base={base}; worktree={dest}"
            bead.notes = f"{bead.notes}\n{line}" if bead.notes else line
            return {"worktree": str(dest), "base": base}

    def matches(self, issue: Any) -> bool:
        """btq's routing rule, for this worker's agent (`wsd`), workstream and session."""
        labels: list[str] = issue.get("labels", [])
        routes = {p: [v[len(p):] for v in labels if v.startswith(p)]
                  for p in ("agent:", "ws:", "session:", "kind:")}
        session = self.worker.split(":", 2)[2]
        return (routes["agent:"] == ["wsd"] and routes["ws:"] == [self.ws]
                and routes["session:"] in ([], [session]) and len(routes["kind:"]) == 1
                and routes["kind:"][0] in ("brainstorm", "task", "review", "research")
                and "needs-human" not in labels)

    def design_allowed(self, issue: Any) -> bool:
        """btq's gate, simplified: a `kind:task` needs a design approval the world accepts."""
        if "kind:task" not in issue.get("labels", []):
            return True
        return issue.get("metadata", {}).get("design_approval") in self.world.approvals

    @contextlib.contextmanager
    def exclusive(self) -> Generator[None]:
        with self._exclusive:
            yield

    def bd(self, *args: str) -> Any:
        with self.world.lock:
            verb = args[0]
            name = "comments add" if verb == "comments" and len(args) > 2 else verb
            about = next((a for a in args[1:] if a in self.world.beads), None)
            self.world.check(name, self.worker, about)
            result = self._bd(list(args))
            self.world.check_after(name, about)
            return result

    def _bd(self, args: list[str]) -> Any:
        verb = args.pop(0)
        if verb == "list":
            labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
            every = "--all" in args
            statuses = args[args.index("--status") + 1].split(",") if "--status" in args else []
            beads = sorted(self.world.beads.values(), key=lambda b: b.id)
            return [self.world.json(b, detail=False) for b in beads
                    if (every or b.status in statuses) and all(lbl in b.labels for lbl in labels)]
        if verb == "label":
            action, bead_id, label = args
            bead = self._get(bead_id)
            if action == "add" and label not in bead.labels:
                bead.labels.append(label)
            if action == "remove" and label in bead.labels:
                bead.labels.remove(label)
            done = "added" if action == "add" else "removed"
            return [{"issue_id": bead_id, "label": label, "status": done}]
        if verb == "dep":
            _, bead_id, other = args
            bead = self._get(bead_id)
            self._get(other)
            if not any(d == other for d, _ in bead.deps):
                bead.deps.append((other, "blocks"))
            return {"status": "added"}
        if verb == "comments":
            if len(args) == 1:
                bead = self._get(args[0])
                return [{"id": i, "issue_id": bead.id, "author": self.worker, "text": t,
                         "created_at": "2026-10-01T00:00:00Z"} for i, t in enumerate(bead.comments)]
            _, bead_id, text = args
            self._get(bead_id).comments.append(text)
            return {"text": text}
        if verb == "update":
            bead_id, flag, pair = args
            assert flag == "--set-metadata"
            key, value = pair.split("=", 1)
            try:      # bd 1.1 stores a number as a number, anything else (JSON objects too) as a string
                self._get(bead_id).metadata[key] = int(value)
            except ValueError:
                self._get(bead_id).metadata[key] = value
            return [self.world.json(self._get(bead_id), detail=False)]
        raise AssertionError(f"fake bd does not support {verb}")


def factory(world: World):  # noqa: ANN201 - returns a QueueFactory
    queues: dict[tuple[str, str], FakeQueue] = {}

    def make(ws: str, session: str) -> FakeQueue:
        if world.down:
            raise OSError("credentials unreadable")
        queues.setdefault((ws, session), FakeQueue(world, ws, session))
        return queues[(ws, session)]

    return make
```

- [ ] **Step 3: Create `$HZ/tests/wsd_env.py`**

The shared test helpers start small; Task 6 replaces this file with the test rig.

```python
"""Shared helpers for the wsd tests. Task 6 adds the test rig."""

import subprocess
from pathlib import Path

WS = "alpha"


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path
```

- [ ] **Step 4: Create `$HZ/tests/test_wsd_beads.py`**

`test_btq_contract_*` loads the real `$BTQ_REPO/bin/btq` with a fake `bd` executable on `PATH` and `HOME` in `tmp_path`; it is skipped without `BTQ_REPO`.

```python
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fakes.fake_btq import World, factory
from wsd_env import WS, git_repo

from heterodyne.wsd import btq, ids
from heterodyne.wsd.beads import (
    PARKED,
    RECORD_KEY,
    BeadsAdapter,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    UnexpectedShape,
    WorktreeConflict,
    parse,
)


def raw(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": "btq-1", "title": "t", "status": "open", "dependency_count": 0}
    return {**base, **kw}


def test_parse_omitted_labels_metadata_and_deps() -> None:
    bead = parse(raw(), detail=True)
    assert (bead.labels, bead.metadata, bead.deps) == ((), {}, ())


def test_parse_missing_dependencies_with_a_count_fails_closed() -> None:
    with pytest.raises(UnexpectedShape):
        parse(raw(dependency_count=1), detail=True)


def test_parse_missing_count_and_list_fails_closed() -> None:
    bead = raw()
    del bead["dependency_count"]
    with pytest.raises(UnexpectedShape):
        parse(bead, detail=True)


@pytest.mark.parametrize("count", [0, 2, "1", True, -1])
def test_parse_rejects_a_dependency_count_that_disagrees(count: Any) -> None:
    """Finding 17: a count that doesn't match the list, or isn't a count, is contradictory evidence."""
    with pytest.raises(UnexpectedShape):
        parse(raw(dependency_count=count, dependencies=[{"id": "x", "status": "open",
                                                         "dependency_type": "blocks"}]), detail=True)


@pytest.mark.parametrize("bad", [
    raw(labels="x"), raw(labels=[1]), raw(metadata=[]), raw(id=None), raw(dependencies={}),
    raw(dependency_count=1, dependencies=[{"id": "x", "status": "open"}]), [raw()], None,
])
def test_parse_rejects_unexpected_shapes(bad: Any) -> None:
    with pytest.raises(UnexpectedShape):
        parse(bad, detail=True)


def test_unknown_dependency_type_blocks() -> None:
    bead = parse(raw(dependency_count=2, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "something-new"},
        {"id": "b", "status": "open", "dependency_type": "related"}]), detail=True)
    assert [d.id for d in bead.open_blockers()] == ["a"]


def test_operator_input_blocker_is_waiting_on_operator() -> None:
    bead = parse(raw(dependency_count=1, dependencies=[
        {"id": "a", "status": "open", "dependency_type": "blocks", "labels": ["kind:question"]}]),
        detail=True)
    assert bead.waits_on_operator()


def test_list_output_has_no_blocker_detail() -> None:
    with pytest.raises(ValueError, match="show"):
        parse(raw(), detail=False).open_blockers()


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path / "btq-state")


def test_claim_read_back_views(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    assert adapter.read_claim(WS, "btq-1") is ClaimView.FREE
    adapter.claim(WS, "btq-1")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    world.beads["btq-1"].assignee = "someone:else"
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OTHER


def test_claim_errors_are_classified(world: World) -> None:
    world.add("btq-1", status="closed")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(ClaimRefused):
        adapter.claim(WS, "btq-1")
    world.add("btq-2")
    world.fault("claim", RuntimeError("timeout"), after=True)
    with pytest.raises(ClaimUncertain):
        adapter.claim(WS, "btq-2")
    assert adapter.read_claim(WS, "btq-2") is ClaimView.OURS


def test_ours_lists_only_per_bead_workers(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2", status="in_progress", assignee="claude:host:x")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    assert [b.id for b in adapter.ours(WS)] == ["btq-1"]


def test_ours_is_found_by_assignee_whatever_the_labels(world: World) -> None:
    """Finding 12: a claimed bead that lost its routing labels, or went back to open or blocked under our
    worker, is still ours. A closed one is not."""
    for bead in ("btq-1", "btq-2", "btq-3", "btq-4"):
        world.add(bead)
    adapter = BeadsAdapter(factory(world))
    for bead in ("btq-1", "btq-2", "btq-3", "btq-4"):
        adapter.claim(WS, bead)
    world.beads["btq-1"].labels = ["kind:task"]
    world.beads["btq-2"].status = "blocked"
    world.beads["btq-3"].status = "open"
    world.close("btq-4")
    assert [b.id for b in adapter.ours(WS)] == ["btq-1", "btq-2", "btq-3"]


def test_unsettled_actions_include_closed_beads_and_unknown_values(world: World) -> None:
    """Finding 9: closing a bead settles nothing; a value wsd doesn't know is never read as settled."""
    world.add("btq-a", metadata={"action_state": "executing"})
    world.add("btq-b", status="closed", metadata={"action_state": "uncertain"})
    world.add("btq-c", status="closed", metadata={"action_state": "succeeded"})
    world.add("btq-d", metadata={"action_state": "half-done"})
    world.add("btq-e")
    adapter = BeadsAdapter(factory(world))
    found = adapter.with_metadata(WS, "action_state", frozenset({"pending", "succeeded", "failed"}))
    assert sorted(found) == ["btq-a", "btq-b", "btq-d"]


def test_validate_reruns_btqs_post_claim_checks(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2", kind="research", metadata={"design_approval": ""})
    world.add("btq-3")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(NotOurs):
        adapter.validate(WS, "btq-1")
    for bead in ("btq-1", "btq-2", "btq-3"):
        adapter.claim(WS, bead)
    assert adapter.validate(WS, "btq-1").id == "btq-1"
    assert adapter.validate(WS, "btq-2").id == "btq-2"         # no design gate on research
    del world.beads["btq-1"].metadata["design_approval"]
    with pytest.raises(RoutingChanged):
        adapter.validate(WS, "btq-1")
    world.beads["btq-3"].labels.append("session:someone-else")
    with pytest.raises(RoutingChanged):
        adapter.validate(WS, "btq-3")


def test_session_record_round_trips_and_never_guesses(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    assert adapter.show(WS, "btq-1").record() is None
    rec = SessionRecord("coder", "p-one", "key-1", "/r", "/r-btq-btq-1")
    adapter.ensure_record(WS, "btq-1", rec)
    assert adapter.show(WS, "btq-1").record() == rec
    world.beads["btq-1"].metadata[RECORD_KEY] = '{"role": "coder"}'
    with pytest.raises(RecordUnreadable):
        adapter.show(WS, "btq-1").record()


def test_worktree_needs_btqs_provenance(world: World, tmp_path: Path) -> None:
    """Finding 15: a worktree btq made for this bead and repository is verified; the same path without
    the provenance note (btq died between the two), or a worktree of another repository, is a conflict."""
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    path = adapter.worktree(WS, "btq-1", repo)
    assert adapter.verify_worktree(WS, "btq-1", repo, path) == path
    other = git_repo(tmp_path / "other" / "proj")
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", other, path)        # not a worktree of that repository
    world.beads["btq-1"].notes = ""
    with pytest.raises(WorktreeConflict):
        adapter.verify_worktree(WS, "btq-1", repo, path)
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-1", repo)                       # and never reused


def test_writes_are_idempotent_and_owned(world: World) -> None:
    world.add("btq-1")
    world.add("btq-2")
    adapter = BeadsAdapter(factory(world))
    with pytest.raises(NotOurs):
        adapter.ensure_label(WS, "btq-1", PARKED)
    adapter.claim(WS, "btq-1")
    for _ in range(2):
        adapter.ensure_label(WS, "btq-1", PARKED)
        adapter.ensure_blocker(WS, "btq-1", "btq-2")
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    bead = world.beads["btq-1"]
    assert bead.labels.count(PARKED) == 1
    assert bead.deps == [("btq-2", "blocks")]
    assert bead.comments == ["parked (m-1)"]


def test_uncertain_comment_is_not_repeated(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    world.fault("comments add", RuntimeError("timeout"), after=True)
    with pytest.raises(BeadsUnavailable):
        adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    adapter.ensure_comment(WS, "btq-1", "m-1", "parked (m-1)")
    assert world.beads["btq-1"].comments == ["parked (m-1)"]


def test_queue_down_is_unavailable_never_empty(world: World) -> None:
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue(WS)
    world.down = True
    with pytest.raises(BeadsUnavailable):
        adapter.ready(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.ours(WS)
    with pytest.raises(BeadsUnavailable):
        adapter.exists(WS, "btq-1")


def test_missing_bead_is_the_only_absent(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    assert adapter.exists(WS, "btq-9") is False


def test_unreadable_pause_flag_counts_as_paused(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    state = adapter.ws_queue(WS).state
    assert adapter.paused(WS) is False
    state.rmdir()
    state.write_text("not a directory")       # lstat of state/paused now fails with ENOTDIR
    assert adapter.paused(WS) is True


def test_pause_flag_reads_back(world: World) -> None:
    adapter = BeadsAdapter(factory(world))
    adapter.set_paused(WS, True)
    assert (adapter.ws_queue(WS).state / "paused").exists()
    adapter.set_paused(WS, False)
    assert adapter.paused(WS) is False


def test_worktree_is_idempotent_and_conflicts_fail_closed(world: World, tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "proj")
    world.add("btq-1")
    adapter = BeadsAdapter(factory(world))
    adapter.claim(WS, "btq-1")
    first = adapter.worktree(WS, "btq-1", repo)
    assert adapter.worktree(WS, "btq-1", repo) == first
    assert world.worktrees == ["btq-1"]
    world.add("btq-2")
    adapter.claim(WS, "btq-2")
    (tmp_path / "proj-btq-btq-2").mkdir()
    (tmp_path / "proj-btq-btq-2" / "keep").write_text("x")
    with pytest.raises(WorktreeConflict):
        adapter.worktree(WS, "btq-2", repo)
    assert (tmp_path / "proj-btq-btq-2" / "keep").exists()


FAKE_BD = """#!{python}
import json, sys
log = {log!r}
beads = json.loads({beads!r})
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if "show" in args:
    bead = beads.get(args[args.index("show") + 1])
    if bead is None:
        sys.exit("no issue found")
    print(json.dumps([bead]))
else:
    print("[]")
"""


@pytest.mark.skipif(not os.environ.get("BTQ_REPO"), reason="needs $BTQ_REPO (a beads-task-queue checkout)")
def test_contract_with_real_btq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real btq Queue, loaded as wsd does, against a fake `bd` executable: no Dolt, no network. Also
    the guard's re-run of btq's post-claim checks: `matches` and the design gate, as btq implements them."""
    home, bindir = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bindir.mkdir()
    config = tmp_path / "btq-config"
    config.mkdir()
    (config / "credentials.json").write_text(json.dumps({"wsd": "test-only"}))

    def bead(bead_id: str, kind: str, **extra: Any) -> dict[str, Any]:
        worker = f"wsd:fakehost:{ids.bead_session(WS, bead_id)}"
        return {"id": bead_id, "title": "t", "status": "in_progress", "assignee": worker,
                "labels": ["agent:wsd", f"ws:{WS}", f"kind:{kind}"], "dependency_count": 0, **extra}

    beads = {"btq-1": bead("btq-1", "task"),                                  # no design approval
             "btq-2": bead("btq-2", "research"),                              # no design gate
             "btq-3": bead("btq-3", "task", metadata={"design_approval": "btq-2"})}   # not an approval
    bd = bindir / "bd"
    bd.write_text(FAKE_BD.format(python=sys.executable, log=str(tmp_path / "bd.log"),
                                 beads=json.dumps(beads)))
    bd.chmod(bd.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("socket.gethostname", lambda: "fakehost")
    module = btq.load(Path(os.environ["BTQ_REPO"]))
    adapter = BeadsAdapter(btq.factory(module, {"config_dir": str(config), "repo": str(tmp_path)}))
    assert adapter.show(WS, "btq-1").labels == ("agent:wsd", f"ws:{WS}", "kind:task")
    assert adapter.read_claim(WS, "btq-1") is ClaimView.OURS
    assert adapter.paused(WS) is False
    assert str(adapter.ws_queue(WS).state).startswith(str(home))

    def actors() -> set[str]:
        logged = [json.loads(line) for line in (tmp_path / "bd.log").read_text().splitlines()]
        (tmp_path / "bd.log").unlink()
        assert all(argv[-1] == "--json" for argv in logged)
        return {argv[argv.index("--actor") + 1] for argv in logged}

    assert actors() == {f"wsd:fakehost:{ids.ws_session(WS)}"}       # reads use the workstream worker
    assert adapter.validate(WS, "btq-2").id == "btq-2"
    assert actors() == {beads["btq-2"]["assignee"]}                   # validation, the bead's own worker
    for gated in ("btq-1", "btq-3"):
        with pytest.raises(RoutingChanged):
            adapter.validate(WS, gated)


def test_btq_without_wsd_agent_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "btq").write_text("AGENTS = ('claude',)\nclass Queue: pass\n")
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)


def test_missing_btq_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(btq.BtqUnavailable):
        btq.load(tmp_path)
```

- [ ] **Step 5: Create `$HZ/tests/test_wsd_gitwip.py`**

```python
from pathlib import Path

import pytest
from wsd_env import git_repo

from heterodyne.wsd import gitwip


def test_wip_commit_is_idempotent(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    (repo / "a.txt").write_text("x")
    first = gitwip.wip_commit(repo, "op1", "parked btq-1")
    (repo / "b.txt").write_text("later")
    assert gitwip.wip_commit(repo, "op1", "parked btq-1") == first
    assert gitwip.find_wip(repo, "op1") == first
    assert gitwip.find_wip(repo, "op2") is None
    assert "b.txt" in gitwip.git(repo, "status", "--porcelain")


def test_wip_commit_with_nothing_to_commit_still_records_the_mark(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    sha = gitwip.wip_commit(repo, "op1", "parked")
    assert gitwip.find_wip(repo, "op1") == sha


def test_wip_commit_skips_hooks(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    gitwip.wip_commit(repo, "op1", "parked")


def test_git_failure_is_raised(tmp_path: Path) -> None:
    with pytest.raises(gitwip.GitFailed):
        gitwip.wip_commit(tmp_path, "op1", "not a repo")
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_beads.py tests/test_wsd_gitwip.py -q`
Expected: FAIL: `ModuleNotFoundError` for `heterodyne.wsd.btq`, `heterodyne.wsd.beads` or `heterodyne.wsd.gitwip`.

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/btq.py`**

```python
"""btq's Queue library, loaded in-process from the configured checkout (ADR 0001 §4.3, §16).

btq is a script without a `.py` suffix, so it is loaded by file. Only the `Queue` class is used, always
as agent `wsd`; `QueueLike` is the slice of it wsd relies on, which the test fake implements too.
"""

import importlib.machinery
import importlib.util
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

AGENT = "wsd"
MODULE_NAME = "_heterodyne_btq"


class BtqUnavailable(Exception):
    """The btq checkout is missing, unloadable, or predates the `wsd` agent (plan 1's btq change)."""


class QueueLike(Protocol):
    worker: str
    state: Path

    def bd(self, *args: str) -> Any: ...
    def show(self, issue_id: str) -> Any: ...
    def ready(self) -> Any: ...
    def claim(self, issue_id: str) -> Any: ...
    def owned(self, issue_id: str, statuses: tuple[str, ...] = ("in_progress",)) -> Any: ...
    def matches(self, issue: Any) -> bool: ...
    def design_allowed(self, issue: Any) -> bool: ...
    def worktree(self, issue_id: str, repository: str) -> Any: ...
    def exclusive(self) -> AbstractContextManager[None]: ...


# (workstream, btq session) -> Queue("wsd", workstream, session, **locations)
QueueFactory = Callable[[str, str], QueueLike]


def load(checkout: Path) -> ModuleType:
    path = checkout / "bin" / "btq"
    try:
        loader = importlib.machinery.SourceFileLoader(MODULE_NAME, str(path))
        spec = importlib.util.spec_from_loader(MODULE_NAME, loader)
        if spec is None:
            raise BtqUnavailable("btq could not be loaded")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as exc:
        raise BtqUnavailable(f"btq could not be loaded ({type(exc).__name__})") from None
    if AGENT not in getattr(module, "AGENTS", ()) or not hasattr(module, "Queue"):
        raise BtqUnavailable("this btq has no wsd agent; install plan 1's btq change")
    return module


def factory(module: ModuleType, locations: Mapping[str, str]) -> QueueFactory:
    queue_class = cast(Callable[..., QueueLike], module.Queue)
    overrides = dict(locations)

    def make(ws: str, session: str) -> QueueLike:
        return queue_class(AGENT, ws, session, **overrides)

    return make
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/gitwip.py`**

```python
"""The git operations of parking (ADR 0001 §4.3): find or make the WIP commit, and inspect a worktree.

The WIP commit message carries the park mark (`wsd-park: <op id>`), so a replayed park finds the commit
it already made instead of making a second one. The commit is local to the `btq/<id>` branch; nothing
is ever pushed (§5.3).
"""

import subprocess
from pathlib import Path

GIT_TIMEOUT = 60
PARK_MARK = "wsd-park: "


class GitFailed(Exception):
    pass


def git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                                timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitFailed(type(exc).__name__) from None
    if result.returncode:
        raise GitFailed(f"git {args[0]} exited {result.returncode}")
    return result.stdout.strip()


def branch(path: Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD")


def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel")).resolve()


def find_wip(worktree: Path, mark: str) -> str | None:
    """The SHA of the commit on HEAD's history whose message holds the mark, if any. Only the last 50
    commits are searched: a park's own commit is at or near the tip."""
    out = git(worktree, "log", "-n", "50", "--format=%H", "--fixed-strings", f"--grep={PARK_MARK}{mark}")
    shas = out.split()
    return shas[0] if shas else None


def wip_commit(worktree: Path, mark: str, summary: str) -> str:
    """Commit everything in the worktree as WIP and return HEAD. Idempotent: if a commit with the mark
    already exists, return it; if there is nothing to commit, an empty commit still records the mark."""
    found = find_wip(worktree, mark)
    if found is not None:
        return found
    git(worktree, "add", "--all")
    git(worktree, "-c", "user.name=wsd", "-c", "user.email=wsd@localhost", "commit", "--allow-empty",
        "--no-verify", "-m", f"WIP: {summary}\n\n{PARK_MARK}{mark}")
    return git(worktree, "rev-parse", "HEAD")


def common_dir(path: Path) -> Path:
    """The git common directory: shared by a repository and every worktree made from it."""
    out = git(path, "rev-parse", "--git-common-dir")
    return (path / out).resolve() if not Path(out).is_absolute() else Path(out).resolve()
```

- [ ] **Step 9: Create `$HZ/src/heterodyne/wsd/beads.py`**

```python
"""wsd's view of the beads queue, through btq's Queue library as agent `wsd` (ADR 0001 §4.3, §5.2).

- Ready work is listed with the workstream-session worker; each bead is claimed, owned and parked with
  its own per-bead worker (`ids.bead_session`).
- btq has no library call for labels, dependencies, comments or metadata, so those go through
  `Queue.bd()` of the per-bead worker, after `Queue.owned()` confirms the claim is still ours, under
  that worker's `exclusive()` lock.
- Every write is check, write, read back (`ensure_*`): a step whose effect is already on the bead is not
  repeated, and an uncertain write is only trusted once it reads back (§4.3).
- Ownership is found by assignee, not by routing labels: a bead held by one of this workstream's per-bead
  workers is ours even if its labels changed. Before every launch `validate` re-runs btq's own post-claim
  checks (`owned`, then `matches` and `design_allowed`, btq's shared routing and design-approval gate).
- The launched-session record (§3.3, §4.1) lives on the bead as metadata `wsd_session`: role, profile,
  session key, repository and worktree. It is written before every launch, so it survives a lost journal;
  plan 4 adds the adapter, model and session ID it learns at launch.
- Fail closed: anything that does not parse as the bd 1.1 JSON shapes recorded in spike S6 raises
  UnexpectedShape, and every infrastructure failure raises BeadsUnavailable. Neither is ever read as
  "absent", "not paused" or "unblocked".
"""

import contextlib
import os
import re
import subprocess
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import msgspec

from heterodyne.wsd import gitwip, ids
from heterodyne.wsd.btq import QueueFactory, QueueLike

PARKED = "v2:parked"
HELD = "v2:held"
NEEDS_HUMAN = "needs-human"
# A blocker carrying one of these labels is an operator ask (an approval, question, picker or permission
# prompt), so a bead parked on it is waiting on input rather than on other work (plans 5 to 7 create them).
OPERATOR_INPUT_LABELS = frozenset({"kind:approval", "kind:question", "kind:confirm"})
# Dependency types that never block. Any other type (`blocks` and anything bd adds later) blocks until
# the other bead is closed: an unknown type is never assumed harmless.
NON_BLOCKING_DEPS = frozenset({"parent-child", "related", "discovered-from"})
BTQ_REFUSALS = ("Finish or release this worker's existing claim first",
                "Task is not eligible for this worker")
BTQ_ROUTING_CHANGED = "Routing/design changed during claim"
BTQ_NOT_OWNED = "Task is not in progress under this worker"
NOT_FOUND = "no issue found matching"
RECORD_KEY = "wsd_session"
# The provenance line btq's `worktree` appends to the bead's notes.
PROVENANCE = re.compile(r"^worker=(?P<worker>[^;]+); repository=(?P<repo>.+); base=[0-9a-f]+; "
                        r"worktree=(?P<worktree>.+)$")
_FAILURES = (RuntimeError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError,
             IndexError, AttributeError)


class BeadsUnavailable(Exception):
    """The queue could not be read or written (Dolt down, bd failed or timed out, credentials missing)."""


class UnexpectedShape(BeadsUnavailable):
    """bd answered, but not in a shape wsd understands; treated exactly like an unreadable queue."""


class ClaimRefused(Exception):
    """btq refused the claim before writing anything."""


class ClaimUncertain(Exception):
    """The claim's outcome is unknown: read it back before doing anything else."""


class RoutingChanged(Exception):
    """btq claimed the bead but its routing or design gate changed meanwhile: never execute it."""


class NotOurs(Exception):
    """The bead is not in progress under this workstream's per-bead worker."""


class WorktreeConflict(Exception):
    """The btq worktree path exists but is not this bead's worktree; it is inspected, never deleted."""


class RecordUnreadable(Exception):
    """The bead's launched-session record is present but does not parse: never guessed at."""


class SessionRecord(msgspec.Struct, frozen=True, forbid_unknown_fields=False):
    """What was launched for a bead (§3.3 session registry). Session identity and worktree come from here,
    never from the current configuration. Plan 4 adds its own fields; unknown fields are kept by bd and
    ignored here."""
    role: str
    profile: str
    session_key: str
    repo: str
    worktree: str


def encode_record(record: SessionRecord) -> str:
    return msgspec.json.encode(record, order="sorted").decode()


class ClaimView(StrEnum):
    OURS = "ours"      # in_progress, assigned to our per-bead worker
    FREE = "free"      # open and unassigned: the claim did not happen
    OTHER = "other"    # anything else: someone else holds it, or it moved on


@dataclass(frozen=True)
class Dep:
    id: str
    status: str
    kind: str
    labels: tuple[str, ...]

    @property
    def blocking(self) -> bool:
        return self.kind not in NON_BLOCKING_DEPS and self.status != "closed"


@dataclass(frozen=True)
class Bead:
    id: str
    title: str
    status: str
    assignee: str | None
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    deps: tuple[Dep, ...] | None     # None for list output, which carries no dependency status
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=lambda: cast(dict[str, Any], {}), compare=False, repr=False)

    def open_blockers(self) -> tuple[Dep, ...]:
        if self.deps is None:
            raise ValueError("dependency detail needs show(), not list()")
        return tuple(d for d in self.deps if d.blocking)

    def waits_on_operator(self) -> bool:
        return any(OPERATOR_INPUT_LABELS & set(d.labels) for d in self.open_blockers())

    def record(self) -> SessionRecord | None:
        """The launched-session record; None if there is none. RecordUnreadable if it does not parse."""
        value = self.metadata.get(RECORD_KEY)
        if value is None:
            return None
        try:
            if isinstance(value, str):
                return msgspec.json.decode(value, type=SessionRecord)
            return msgspec.convert(value, SessionRecord)
        except (msgspec.DecodeError, msgspec.ValidationError):
            raise RecordUnreadable(self.id) from None

    def provenance(self, worker: str) -> list[tuple[str, str]]:
        """(repository, worktree) pairs btq recorded in the notes when `worker` made a worktree."""
        found: list[tuple[str, str]] = []
        for line in self.notes.splitlines():
            m = PROVENANCE.match(line.strip())
            if m and m["worker"] == worker:
                found.append((m["repo"], m["worktree"]))
        return found


def _str(raw: dict[str, Any], key: str, required: bool = True) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise UnexpectedShape(f"bead field {key} is not a string")
    return value


def _labels(raw: dict[str, Any]) -> tuple[str, ...]:
    labels = raw.get("labels", [])      # bd omits the key when there are none
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in cast(list[Any], labels)):
        raise UnexpectedShape("bead labels are not a list of strings")
    return tuple(cast(list[str], labels))


def parse(raw: object, detail: bool) -> Bead:
    """One bead from `bd show` (detail=True) or `bd list` (detail=False) JSON."""
    if not isinstance(raw, dict):
        raise UnexpectedShape("bead is not an object")
    item = cast(dict[str, Any], raw)
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        raise UnexpectedShape("bead metadata is not an object")
    deps: tuple[Dep, ...] | None = None
    if detail:
        count = item.get("dependency_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise UnexpectedShape("bead dependency_count is missing")
        raw_deps = item.get("dependencies", [])     # bd omits the list only when there are none
        if not isinstance(raw_deps, list):
            raise UnexpectedShape("bead dependencies are not a list")
        if len(cast(list[Any], raw_deps)) != count:   # an empty or cut-short list is not "no blockers"
            raise UnexpectedShape("bead dependencies do not match dependency_count")
        parsed: list[Dep] = []
        for dep in cast(list[Any], raw_deps):
            if not isinstance(dep, dict):
                raise UnexpectedShape("a dependency is not an object")
            d = cast(dict[str, Any], dep)
            parsed.append(Dep(cast(str, _str(d, "id")), cast(str, _str(d, "status")),
                              cast(str, _str(d, "dependency_type")), _labels(d)))
        deps = tuple(parsed)
    return Bead(cast(str, _str(item, "id")), cast(str, _str(item, "title")), cast(str, _str(item, "status")),
                _str(item, "assignee", required=False) or None, _labels(item),
                cast(dict[str, Any], metadata), deps, _str(item, "notes", required=False) or "", item)


def _one(result: object) -> Bead:
    if not isinstance(result, dict):
        raise UnexpectedShape("show did not return one bead")
    return parse(cast(object, result), detail=True)


class BeadsAdapter:
    def __init__(self, factory: QueueFactory) -> None:
        self._factory = factory
        self._queues: dict[tuple[str, str], QueueLike] = {}
        self._lock = threading.Lock()

    def _queue(self, ws: str, session: str) -> QueueLike:
        with self._lock:
            queue = self._queues.get((ws, session))
            if queue is None:
                try:
                    queue = self._factory(ws, session)
                except _FAILURES as exc:     # credentials unreadable, no `wsd` entry, state dir refused
                    raise BeadsUnavailable(f"btq queue could not be opened ({type(exc).__name__})") from None
                self._queues[(ws, session)] = queue
            return queue

    def ws_queue(self, ws: str) -> QueueLike:
        return self._queue(ws, ids.ws_session(ws))

    def bead_queue(self, ws: str, bead: str) -> QueueLike:
        return self._queue(ws, ids.bead_session(ws, bead))

    @staticmethod
    def _call[T](fn: Callable[[], T]) -> T:
        try:
            return fn()
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue call failed ({type(exc).__name__})") from None

    # --- reads ---

    def ready(self, ws: str) -> list[Bead]:
        """New work in btq's order. btq returns [] while the workstream worker is paused."""
        queue = self.ws_queue(ws)
        raw = self._call(queue.ready)
        if not isinstance(raw, list):
            raise UnexpectedShape("ready did not return a list")
        return [parse(item, detail=False) for item in cast(list[Any], raw)]

    def show(self, ws: str, bead: str) -> Bead:
        queue = self.ws_queue(ws)
        return _one(self._call(lambda: queue.show(bead)))

    def exists(self, ws: str, bead: str) -> bool:
        """False only when bd says the bead does not exist; any other failure raises."""
        queue = self.ws_queue(ws)
        try:
            queue.show(bead)
        except RuntimeError as exc:
            if NOT_FOUND in str(exc):
                return False
            raise BeadsUnavailable("show failed") from None
        except _FAILURES as exc:
            raise BeadsUnavailable(f"show failed ({type(exc).__name__})") from None
        return True

    def read_claim(self, ws: str, bead: str) -> ClaimView:
        found = self.show(ws, bead)
        if found.status == "in_progress" and found.assignee == self.bead_queue(ws, bead).worker:
            return ClaimView.OURS
        if found.status == "open" and found.assignee is None:
            return ClaimView.FREE
        return ClaimView.OTHER

    def ours(self, ws: str) -> list[Bead]:
        """Every bead held by one of this workstream's per-bead workers, with dependency detail. Found by
        assignee across every unclosed claimed status, never by routing labels: a bead whose labels changed
        under its claim is still ours, and recovery must see it. A bead whose ID is not a slug can't have
        been claimed by wsd and is skipped."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--status", "in_progress,blocked,open", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        found: list[Bead] = []
        for item in cast(list[Any], raw):
            listed = parse(item, detail=False)
            if not ids.SLUG.fullmatch(listed.id):
                continue
            if listed.assignee == self.bead_queue(ws, listed.id).worker:
                found.append(self.show(ws, listed.id))
        return found

    def with_metadata(self, ws: str, key: str, settled: frozenset[str]) -> list[str]:
        """IDs of beads labelled `ws:<ws>`, closed ones included, whose metadata `key` is set to anything
        but one of the `settled` values. Closing a bead says nothing about its action, so status is never
        a reason to skip one, and a value wsd doesn't know is never read as settled."""
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("list", "--all", "--label", f"ws:{ws}", "--limit", "0"))
        if not isinstance(raw, list):
            raise UnexpectedShape("list did not return a list")
        beads = [parse(item, detail=False) for item in cast(list[Any], raw)]
        return [b.id for b in beads if key in b.metadata and b.metadata[key] not in settled]

    def validate(self, ws: str, bead: str) -> Bead:
        """btq's post-claim checks, run again right before a launch: the bead is in progress under our
        per-bead worker (NotOurs otherwise), and btq's own `matches` and `design_allowed` still pass
        (RoutingChanged otherwise). Returns the bead as read under the worker's exclusive lock."""
        with self._owned(ws, bead) as owned:
            try:
                issue = owned.owned(bead)
                ok = owned.matches(issue) and owned.design_allowed(issue)
            except ValueError as exc:
                if str(exc).startswith(BTQ_NOT_OWNED):
                    raise NotOurs(bead) from None
                raise
        if not ok:
            raise RoutingChanged(bead)
        return _one(issue)

    def comments(self, ws: str, bead: str) -> list[str]:
        queue = self.ws_queue(ws)
        raw = self._call(lambda: queue.bd("comments", bead))
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise UnexpectedShape("comments did not return a list")
        texts: list[str] = []
        for item in cast(list[Any], raw):
            if not isinstance(item, dict) or not isinstance(cast(dict[str, Any], item).get("text"), str):
                raise UnexpectedShape("a comment has no text")
            texts.append(cast(str, cast(dict[str, Any], item)["text"]))
        return texts

    # --- the shared pause flag (§4.3) ---

    def paused(self, ws: str) -> bool:
        """True unless the flag is confirmed absent: an unreadable state directory counts as paused."""
        flag = self.ws_queue(ws).state / "paused"
        try:
            os.lstat(flag)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return True

    def set_paused(self, ws: str, paused: bool) -> None:
        flag = self.ws_queue(ws).state / "paused"
        try:
            if paused:
                flag.touch()
            else:
                flag.unlink(missing_ok=True)
        except OSError as exc:
            raise BeadsUnavailable(f"pause flag not written ({type(exc).__name__})") from None
        if self.paused(ws) != paused:
            raise BeadsUnavailable("pause flag did not read back")

    # --- claim (§4.3) ---

    def claim(self, ws: str, bead: str) -> Bead:
        """Claim with the per-bead worker. ClaimRefused means nothing was written; ClaimUncertain means the
        caller must `read_claim` before anything else; RoutingChanged means the claim is ours but the
        bead must not be executed."""
        queue = self.bead_queue(ws, bead)
        try:
            return _one(queue.claim(bead))
        except ValueError as exc:
            if str(exc).startswith(BTQ_REFUSALS):
                raise ClaimRefused(str(exc)) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except RuntimeError as exc:
            if str(exc).startswith(BTQ_ROUTING_CHANGED):
                raise RoutingChanged(bead) from None
            raise ClaimUncertain(type(exc).__name__) from None
        except _FAILURES as exc:
            raise ClaimUncertain(type(exc).__name__) from None

    # --- owned writes ---

    @contextlib.contextmanager
    def _owned(self, ws: str, bead: str) -> Generator[QueueLike]:
        queue = self.bead_queue(ws, bead)
        try:
            with queue.exclusive():
                try:
                    queue.owned(bead)
                except ValueError as exc:
                    if str(exc).startswith(BTQ_NOT_OWNED):
                        raise NotOurs(bead) from None
                    raise
                yield queue
        except (NotOurs, BeadsUnavailable):
            raise
        except _FAILURES as exc:
            raise BeadsUnavailable(f"queue write failed ({type(exc).__name__})") from None

    def _write(self, ws: str, bead: str, *args: str) -> None:
        with self._owned(ws, bead) as queue:
            queue.bd(*args)

    def ensure_label(self, ws: str, bead: str, label: str, present: bool = True) -> None:
        if (label in self.show(ws, bead).labels) == present:
            return
        self._write(ws, bead, "label", "add" if present else "remove", bead, label)
        if (label in self.show(ws, bead).labels) != present:
            raise BeadsUnavailable("label change did not read back")

    def ensure_blocker(self, ws: str, bead: str, blocker: str) -> None:
        def has() -> bool:
            deps = self.show(ws, bead).deps or ()
            return any(d.id == blocker and d.kind not in NON_BLOCKING_DEPS for d in deps)
        if has():
            return
        self._write(ws, bead, "dep", "add", bead, blocker)
        if not has():
            raise BeadsUnavailable("blocking edge did not read back")

    def ensure_comment(self, ws: str, bead: str, mark: str, text: str) -> None:
        """Comments are not idempotent in bd, so `mark` (unique per write, and part of `text`) is looked for
        first and the comment is added only if no comment holds it."""
        if mark not in text:
            raise ValueError("the comment text must contain its mark")
        if any(mark in c for c in self.comments(ws, bead)):
            return
        self._write(ws, bead, "comments", "add", bead, text)
        if not any(mark in c for c in self.comments(ws, bead)):
            raise BeadsUnavailable("comment did not read back")

    def ensure_record(self, ws: str, bead: str, record: SessionRecord) -> None:
        """Write the launched-session record before the launch it describes, and read it back."""
        self.ensure_metadata(ws, bead, RECORD_KEY, encode_record(record))
        if self.show(ws, bead).record() != record:
            raise BeadsUnavailable("session record did not read back")

    def ensure_metadata(self, ws: str, bead: str, key: str, value: str) -> None:
        if self.show(ws, bead).metadata.get(key) == value:
            return
        self._write(ws, bead, "update", bead, "--set-metadata", f"{key}={value}")
        if self.show(ws, bead).metadata.get(key) != value:
            raise BeadsUnavailable("metadata did not read back")

    # --- worktrees (§4.3: btq's convention) ---

    def worktree(self, ws: str, bead: str, repository: Path) -> Path:
        """The bead's btq worktree, `<repo>-btq-<id>` on branch `btq/<id>`. Idempotent: an existing path
        is reused only if it is that worktree on that branch; anything else is a WorktreeConflict."""
        ids.slug(bead, "bead")
        try:
            repo = repository.resolve(strict=True)
        except OSError:
            raise WorktreeConflict("repository missing") from None
        destination = repo.parent / f"{repo.name}-btq-{bead}"
        if os.path.lexists(destination):
            return self.verify_worktree(ws, bead, repo, destination)
        queue = self.bead_queue(ws, bead)
        try:
            result = queue.worktree(bead, str(repo))
        except ValueError as exc:
            if str(exc).startswith(BTQ_NOT_OWNED):
                raise NotOurs(bead) from None
            raise BeadsUnavailable(type(exc).__name__) from None
        except _FAILURES as exc:
            raise BeadsUnavailable(f"worktree failed ({type(exc).__name__})") from None
        if not isinstance(result, dict) or cast(dict[str, Any], result).get("worktree") != str(destination):
            raise UnexpectedShape("worktree result")
        return self.verify_worktree(ws, bead, repo, destination)

    def verify_worktree(self, ws: str, bead: str, repository: Path, worktree: Path) -> Path:
        """`worktree` is this bead's worktree of `repository`: a real directory at its own top level, on
        branch `btq/<id>`, sharing the repository's git common directory, and recorded on the bead by btq
        for our per-bead worker. A path that fails any check (including a worktree whose provenance note
        was never written) is a WorktreeConflict: inspected by a human, never deleted or reused."""
        worker = self.bead_queue(ws, bead).worker
        try:
            repo = repository.resolve(strict=True)
        except OSError:
            raise WorktreeConflict("repository missing") from None
        try:
            ok = (worktree.is_dir() and not worktree.is_symlink()
                  and gitwip.toplevel(worktree) == worktree.resolve()
                  and gitwip.branch(worktree) == f"btq/{bead}"
                  and gitwip.common_dir(worktree) == gitwip.common_dir(repo))
        except (OSError, gitwip.GitFailed):
            ok = False
        if not ok or (str(repo), str(worktree)) not in self.show(ws, bead).provenance(worker):
            raise WorktreeConflict("the path is not this bead's recorded worktree")
        return worktree
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_beads.py tests/test_wsd_gitwip.py -q`
Expected: `40 passed, 1 skipped`. Then `BTQ_REPO=$BTQ_REPO uv run pytest tests/test_wsd_beads.py -q` runs the contract test too: `37 passed`.

- [ ] **Step 11: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 12: Commit**

```bash
cd $HZ
git add docs/spikes/S6-beads-json.md src/heterodyne/wsd/btq.py src/heterodyne/wsd/gitwip.py src/heterodyne/wsd/beads.py tests/fakes/fake_btq.py tests/wsd_env.py tests/test_wsd_beads.py tests/test_wsd_gitwip.py
git commit -m "feat(wsd): beads adapter through btq's Queue as agent wsd, with read-back writes"
```

### Task 5: Claim gate, pause, and the runtime and reconciler seams

**Files:**
- Create: `$HZ/src/heterodyne/wsd/gate.py`, `$HZ/src/heterodyne/wsd/runtime.py`, `$HZ/tests/fakes/fake_runtime.py`
- Test: `$HZ/tests/test_wsd_gate.py`, `$HZ/tests/test_wsd_runtime.py`

**Interfaces:**
- Consumes: `private_dir` (Task 1); `ids.slug` (Task 1); `Checkpoint`, `nothing` (Task 1); `BeadsAdapter`, `Bead`, `BeadsUnavailable` (Task 4).
- Produces:
  - `heterodyne.wsd.gate`: `POINTS = ("gate.checked",)`, `PAUSE_POINTS = ("gate.pause.waiting",)`, `class Paused(Exception)`, `class AlreadyRunning(Exception)`, `class ClaimGate(lock_dir: Path, beads: BeadsAdapter, cp: Checkpoint = nothing)` with `locked(ws)` (context manager; a per-workstream `flock`), `pause(ws)` (checkpoint `gate.pause.waiting`, then the lock, then the flag), `resume(ws)`, `claim(ws, bead) -> Bead` (re-checks the flag inside the lock, then checkpoint `gate.checked`, then claims); `instance_lock(path: Path) -> int`.
  - `heterodyne.wsd.runtime` (**the plan 4 and plan 5 seams**): `ACTION_STATE = "action_state"`, `SETTLED_ACTIONS = {"pending", "succeeded", "failed"}`; `class Liveness(StrEnum)`: `LIVE, UNKNOWN`; frozen `Session(key, ws, bead, role, liveness)`; frozen `LaunchSpec(ws, bead, role, profile, session_key, label, worktree, resume, ref=None)`; `RuntimeUnavailable` (nothing attempted), `LaunchFailed` (confirmed: nothing started), `LaunchUncertain`; `class AgentRuntime(Protocol)`: `available() -> bool`, `sessions(ws) -> list[Session]` (every session launched until the runtime confirms it ended; never a partial list), `launch(spec) -> None` (idempotent on the session key), `stop(session_key) -> None` (returns once ended); `NoRuntime`; `class ActionReconciler(Protocol)`: `unresolved(ws) -> list[str]`; `HoldingReconciler(beads)`.
  - `tests/fakes/fake_runtime.py`: `FakeRuntime` (`up`, `listed`, `launches`, `stops`, `launch_failures`, `launch_uncertain`, `failing_beads`, `stop_failures`, `list_failures`; `end(key)`, `set(key, liveness)`, `adopt(spec, liveness=LIVE)`, `live()`, `coders(ws)`).

- [ ] **Step 1: Create `$HZ/tests/test_wsd_gate.py`**

`test_pause_waits_for_the_claim_lock` holds the claim lock as an in-flight claim would and shows the pause is not acknowledged until the lock is released.

```python
import os
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.gate import AlreadyRunning, ClaimGate, Paused, instance_lock

WS = "alpha"


def gate(tmp_path: Path, cp: Recorder | None = None) -> tuple[World, BeadsAdapter, ClaimGate]:
    world = World(tmp_path / "btq-state")
    beads = BeadsAdapter(factory(world))
    return world, beads, ClaimGate(tmp_path / "claims", beads, cp or Recorder())


def test_claim_checks_the_flag_then_claims(tmp_path: Path) -> None:
    cp = Recorder()
    world, _, g = gate(tmp_path, cp)
    world.add("btq-1")
    assert g.claim(WS, "btq-1").status == "in_progress"
    assert cp.seen == ["gate.checked"]


def test_claim_rechecks_flag_inside_lock(tmp_path: Path) -> None:
    world, beads, g = gate(tmp_path)
    world.add("btq-1")
    beads.set_paused(WS, True)          # a direct `btq pause`: no lock taken
    with pytest.raises(Paused):
        g.claim(WS, "btq-1")
    assert world.claims == []


def test_pause_and_resume_set_the_shared_flag(tmp_path: Path) -> None:
    _, beads, g = gate(tmp_path)
    g.pause(WS)
    assert (beads.ws_queue(WS).state / "paused").exists()
    g.resume(WS)
    assert not beads.paused(WS)


def test_pause_waits_for_the_claim_lock(tmp_path: Path) -> None:
    _, beads, g = gate(tmp_path)
    acked = threading.Event()
    with g.locked(WS):                  # a claim in flight
        pauser = threading.Thread(target=lambda: (g.pause(WS), acked.set()))
        pauser.start()
        assert not acked.wait(0.3)
        assert not beads.paused(WS)
    pauser.join(5)
    assert acked.is_set() and beads.paused(WS)


def test_claim_lock_file_is_private(tmp_path: Path) -> None:
    _, _, g = gate(tmp_path)
    with g.locked(WS):
        pass
    assert (tmp_path / "claims").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "claims" / f"{WS}.claim").stat().st_mode & 0o777 == 0o600


def test_instance_lock_is_exclusive(tmp_path: Path) -> None:
    fd = instance_lock(tmp_path / "state" / "wsd.lock")
    with pytest.raises(AlreadyRunning):
        instance_lock(tmp_path / "state" / "wsd.lock")
    os.close(fd)
    os.close(instance_lock(tmp_path / "state" / "wsd.lock"))
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_runtime.py`**

```python
from pathlib import Path

import pytest
from fakes.fake_btq import World, factory

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.runtime import HoldingReconciler, LaunchSpec, NoRuntime, RuntimeUnavailable


def test_no_runtime_is_unavailable_and_lists_nothing_as_ended() -> None:
    runtime = NoRuntime()
    assert not runtime.available()
    with pytest.raises(RuntimeUnavailable):
        runtime.sessions("alpha")       # never "no sessions": that would read as "none running"
    with pytest.raises(RuntimeUnavailable):
        runtime.launch(LaunchSpec("alpha", "btq-a", "coder", "p", "k", "l", Path("/nonexistent"), False))
    with pytest.raises(RuntimeUnavailable):
        runtime.stop("any")


def test_holding_reconciler_reports_every_unsettled_action(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    world.add("btq-a", metadata={"action_state": "executing"})
    world.add("btq-b", metadata={"action_state": "uncertain"})
    world.add("btq-c", metadata={"action_state": "succeeded"})
    world.add("btq-d", ws="beta", metadata={"action_state": "uncertain"})
    world.add("btq-e", status="closed", metadata={"action_state": "uncertain"})   # closed still counts
    world.add("btq-f", metadata={"action_state": "reticulating"})      # unknown: never settled
    world.add("btq-g", metadata={"action_state": "pending"})
    world.add("btq-h", status="closed", metadata={"action_state": "failed"})
    world.add("btq-i")
    assert HoldingReconciler(BeadsAdapter(factory(world))).unresolved("alpha") == ["btq-a", "btq-b", "btq-e",
                                                                                   "btq-f"]


def test_holding_reconciler_fails_closed(tmp_path: Path) -> None:
    world = World(tmp_path / "btq-state")
    adapter = BeadsAdapter(factory(world))
    adapter.ws_queue("alpha")
    world.down = True
    with pytest.raises(BeadsUnavailable):
        HoldingReconciler(adapter).unresolved("alpha")
```

- [ ] **Step 3: Create `$HZ/tests/fakes/fake_runtime.py`**

```python
"""A recording AgentRuntime (plan 4 provides the real one). Sessions survive a simulated wsd restart,
like real agent processes do. A session stays listed until it is stopped or `end`s on its own."""

from heterodyne.wsd.runtime import (
    LaunchFailed,
    LaunchSpec,
    LaunchUncertain,
    Liveness,
    RuntimeUnavailable,
    Session,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.up = True
        self.listed: dict[str, Session] = {}
        self.launches: list[LaunchSpec] = []
        self.stops: list[str] = []
        self.launch_failures = 0      # the next N launches fail, confirmed: nothing starts
        self.launch_uncertain = 0     # the next N launches are uncertain: listed as UNKNOWN
        self.failing_beads: set[str] = set()     # every launch for these beads fails, confirmed
        self.stop_failures = 0
        self.list_failures = 0

    def available(self) -> bool:
        return self.up

    def sessions(self, ws: str) -> list[Session]:
        if not self.up or self.list_failures:
            self.list_failures = max(0, self.list_failures - 1)
            raise RuntimeUnavailable("can't list sessions")
        return [s for s in self.listed.values() if s.ws == ws]

    def launch(self, spec: LaunchSpec) -> None:
        if not self.up:
            raise RuntimeUnavailable("down")
        current = self.listed.get(spec.session_key)
        if current is not None and current.liveness is Liveness.LIVE:
            return
        if self.launch_failures or spec.bead in self.failing_beads:
            self.launch_failures = max(0, self.launch_failures - 1)
            raise LaunchFailed("launch failed")
        self.launches.append(spec)
        liveness = Liveness.UNKNOWN if self.launch_uncertain else Liveness.LIVE
        self.listed[spec.session_key] = Session(spec.session_key, spec.ws, spec.bead, spec.role, liveness)
        if self.launch_uncertain:
            self.launch_uncertain -= 1
            raise LaunchUncertain("no answer from the backend")

    def stop(self, session_key: str) -> None:
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeUnavailable("stop not confirmed")
        self.stops.append(session_key)
        self.listed.pop(session_key, None)

    # --- test controls ---

    def end(self, session_key: str) -> None:
        """The session ended on its own (the agent exited or crashed)."""
        del self.listed[session_key]

    def set(self, session_key: str, liveness: Liveness) -> None:
        old = self.listed[session_key]
        self.listed[session_key] = Session(old.key, old.ws, old.bead, old.role, liveness)

    def adopt(self, spec: LaunchSpec, liveness: Liveness = Liveness.LIVE) -> None:
        """A session the runtime already runs (for example one launched before a lost journal)."""
        self.listed[spec.session_key] = Session(spec.session_key, spec.ws, spec.bead, spec.role, liveness)

    def live(self) -> list[str]:
        return [k for k, s in self.listed.items() if s.liveness is Liveness.LIVE]

    def coders(self, ws: str = "alpha") -> list[str]:
        """Beads with a listed coder session."""
        return sorted(s.bead for s in self.listed.values() if s.ws == ws and s.role == "coder")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_gate.py tests/test_wsd_runtime.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.gate'` (and `heterodyne.wsd.runtime`).

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/gate.py`**

```python
"""The shared pause gate and the per-workstream claim lock (ADR 0001 §4.3).

Pause is the btq `paused` flag of the workstream-session worker. Every claim runs inside the
workstream's claim lock and re-checks that flag immediately before `claim()`; pausing takes the same
lock before it sets the flag and acknowledges. So once a pause is acknowledged, no claim can start. The
lock is an flock on a file in wsd's state directory, so `wsctl` (another process) and wsd share it.
A direct `btq pause` sets the same flag without the lock: honoured from the next claim check (best effort).
"""

import contextlib
import fcntl
import os
from collections.abc import Generator
from pathlib import Path

from heterodyne.fsutil import private_dir
from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint, nothing

POINTS = ("gate.checked",)
PAUSE_POINTS = ("gate.pause.waiting",)


class Paused(Exception):
    """The workstream is paused: no claim was attempted."""


class AlreadyRunning(Exception):
    """Another wsd holds the instance lock for this state directory."""


class ClaimGate:
    def __init__(self, lock_dir: Path, beads: BeadsAdapter, cp: Checkpoint = nothing) -> None:
        self.lock_dir = lock_dir
        self.beads = beads
        self.cp = cp

    @contextlib.contextmanager
    def locked(self, ws: str) -> Generator[None]:
        """The workstream's claim lock. A fresh descriptor per use, so threads exclude each other too."""
        private_dir(self.lock_dir)
        fd = os.open(self.lock_dir / f"{ids.slug(ws, 'workstream')}.claim",
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)        # closing the descriptor releases the lock

    def pause(self, ws: str) -> None:
        """Returns (acknowledges) only once the flag reads back, with no claim in flight."""
        self.cp("gate.pause.waiting")
        with self.locked(ws):
            self.beads.set_paused(ws, True)

    def resume(self, ws: str) -> None:
        self.cp("gate.pause.waiting")
        with self.locked(ws):
            self.beads.set_paused(ws, False)

    def claim(self, ws: str, bead: str) -> Bead:
        with self.locked(ws):
            if self.beads.paused(ws):
                raise Paused(ws)
            self.cp("gate.checked")
            return self.beads.claim(ws, bead)


def instance_lock(path: Path) -> int:
    """Take the single-instance lock for a wsd state directory; the descriptor is held for the process's
    life. Two wsd processes on one journal would each believe they own every in-flight operation."""
    private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AlreadyRunning(str(path.name)) from None
    return fd
```

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/runtime.py`**

```python
"""Seams to later plans: the agent runtime (plan 4) and action reconciliation (plan 5).

Plan 3 never launches an agent itself. It calls `AgentRuntime`, which plan 4 implements (adapters and
the sandbox backend chosen in ADR revision 14). Nothing here names an adapter or a sandbox. Until a real
runtime is wired in, `NoRuntime` reports itself unavailable, and wsd holds every workstream rather than
claim work it can't run.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from heterodyne.wsd.beads import BeadsAdapter

ACTION_STATE = "action_state"
# The action states that say nothing is in flight (ADR §3.3: pending, executing, then succeeded, failed
# or uncertain). A pending action has not started. Every other value, including one wsd doesn't know,
# is unsettled.
SETTLED_ACTIONS = frozenset({"pending", "succeeded", "failed"})


class Liveness(StrEnum):
    LIVE = "live"
    UNKNOWN = "unknown"     # listed, but the runtime can't tell whether it runs: treated as live


@dataclass(frozen=True)
class Session:
    """A session the runtime may still be running. Ended sessions are not listed."""
    key: str
    ws: str
    bead: str
    role: str
    liveness: Liveness


@dataclass(frozen=True)
class LaunchSpec:
    ws: str
    bead: str
    role: str
    profile: str
    session_key: str        # ids.role_session(bead, role, profile), as recorded on the bead
    label: str              # "<bead> · <role> · <title>" (§4.1)
    worktree: Path
    resume: bool            # resume the same session rather than start one
    ref: str | None = None  # the message or event that caused the launch, for progress reactions


class RuntimeUnavailable(Exception):
    """No runtime can act right now (none configured, or its backend is down). Nothing was attempted."""


class LaunchFailed(Exception):
    """The runtime confirms this launch started nothing. The reason is the exception text (fixed
    wording, no secrets)."""


class LaunchUncertain(Exception):
    """The launch was attempted and the runtime can't say whether a session is running. Any exception
    from `launch` other than LaunchFailed and RuntimeUnavailable is treated the same way."""


class AgentRuntime(Protocol):
    def available(self) -> bool:
        """Whether launches can be attempted at all. Checked before every claim."""
        ...

    def sessions(self, ws: str) -> list[Session]:
        """Every session of the workstream that may be running: each key `launch` was called with, until
        the runtime has confirmed that session ended. This includes sessions started before a wsd
        restart and sessions whose bead wsd no longer knows. Raises RuntimeUnavailable if it can't list
        them; a partial list is never returned."""
        ...

    def launch(self, spec: LaunchSpec) -> None:
        """Start (or, with `spec.resume`, resume) the session. Idempotent on `spec.session_key`: a live
        session is left alone. Returns once the session is live. Raises LaunchFailed when nothing
        started, RuntimeUnavailable when nothing was attempted, and LaunchUncertain otherwise."""
        ...

    def stop(self, session_key: str) -> None:
        """Interrupt the session and wait until it has ended. Idempotent: an ended session is fine.
        Raises RuntimeUnavailable if it can't confirm the session has ended."""
        ...


class NoRuntime:
    """The runtime until plan 4: it launches nothing, and since it can't list sessions it never lets wsd
    conclude that none are running."""

    def available(self) -> bool:
        return False

    def sessions(self, ws: str) -> list[Session]:
        raise RuntimeUnavailable("no agent runtime is configured")

    def launch(self, spec: LaunchSpec) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")

    def stop(self, session_key: str) -> None:
        raise RuntimeUnavailable("no agent runtime is configured")


class ActionReconciler(Protocol):
    def unresolved(self, ws: str) -> list[str]:
        """IDs of beads, closed ones included, whose action is in any state but pending, succeeded or
        failed and not yet reconciled against its target (§5.4). Raises BeadsUnavailable if that can't
        be established."""
        ...


class HoldingReconciler:
    """Plan 3's reconciler: it can't check targets (plan 5 can), so it only finds unsettled actions and
    reports every one as unresolved. wsd holds the workstream until they are settled; it never assumes
    there are none."""

    def __init__(self, beads: BeadsAdapter) -> None:
        self.beads = beads

    def unresolved(self, ws: str) -> list[str]:
        return self.beads.with_metadata(ws, ACTION_STATE, SETTLED_ACTIONS)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_gate.py tests/test_wsd_runtime.py -q`
Expected: `9 passed`.

- [ ] **Step 8: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 9: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/gate.py src/heterodyne/wsd/runtime.py tests/fakes/fake_runtime.py tests/test_wsd_gate.py tests/test_wsd_runtime.py
git commit -m "feat(wsd): claim gate with the shared pause flag; runtime and reconciler seams"
```

### Task 6: The operation lock, park, resume, release and escalation, and the launch guard

**Files:**
- Create: `$HZ/src/heterodyne/wsd/workstream.py`, `$HZ/src/heterodyne/wsd/park.py`
- Modify: `$HZ/tests/wsd_env.py` (replace it with the test rig)
- Test: `$HZ/tests/test_wsd_park.py`

**Interfaces:**
- Consumes: `ids` (Task 1); `Journal`, `Op`, `OpKind`, `OpStatus` (Task 3); `BeadsAdapter`, `Bead`, `SessionRecord`, labels and exceptions (Task 4); `gitwip.wip_commit`, `GitFailed` (Task 4); `ClaimGate` (Task 5); `AgentRuntime`, `ActionReconciler`, `Session`, `Liveness`, `LaunchSpec`, `LaunchFailed`, `RuntimeUnavailable` (Task 5).
- Produces:
  - `heterodyne.wsd.workstream`: `REPO_KEY = "repo"`, `DEFAULT_REPO = "default"`, `class ConfigInvalid(Exception)`, frozen `Limits(launch_failures_before_human=2, park_attempts_before_human=3)`, frozen `WorkstreamSettings(name, repos: dict[str, Path], coder_role, coder_profile, profiles: frozenset[str], limits=Limits())`, frozen `Placement(repo, worktree, profile, session_key, label)`, `place(ws, bead) -> Placement` (first launch only), `label(bead, role) -> str`, `record(ws, spot) -> SessionRecord`, frozen `Deps(journal, beads, gate, runtime, reconciler, cp=nothing)`.
  - `heterodyne.wsd.park`: `PARK_POINTS = ("lock.waiting", "park.intent", "park.stopped!", "park.stopped", "park.committed!", "park.committed", "park.blocked!", "park.blocked", "park.labelled!", "park.labelled", "park.commented!", "park.done")`, `RESUME_POINTS = ("resume.intent", "resume.unlabelled!", "resume.unlabelled", "resume.launched!", "resume.done")`, `RELEASE_POINTS = ("lock.waiting", "release.intent", "release.unlabelled!", "release.unlabelled", "release.done")`, `ESCALATE_POINTS = ("escalate.intent", "escalate.labelled!", "escalate.done")`; conditional barriers outside the tuples: `release.stopped!`, `release.recorded!`, `<kind>.recorded!`, `<kind>.shelved!`. `PARK_MARK = "wsd-park: "`, `REASONS`, `RELEASABLE = {HELD, STUCK}`, `GUARD_SETTLES = {LAUNCH_UNCERTAIN}`.
  - `class Launch(StrEnum)`: `STARTED, LIVE, WAIT, UNCERTAIN, FAILED, ENDED`; `class NotReleasable(Exception)`; `parked_state(bead) -> BeadState`, `blocker_detail(bead) -> str`, `resumable(bead) -> bool`, `park_comment(op) -> str`.
  - `class Parker(ws, deps)` with `entry()` (**the workstream's operation lock**: checkpoint `lock.waiting`, then an `RLock`; park, release, pickup and recovery all take it), `park(bead, blockers: tuple[str, ...], why="", hold=False, ref=None) -> BeadState` (**the plan 5/6 seam**; `OpConflict` if another operation is open on the bead), `release(bead, ref=None) -> BeadState` (**the plan 6 seam**; `NotReleasable`), `resume(bead: Bead, ref=None) -> Launch`, `launch(op, new: SessionRecord | None = None) -> Launch` (**the launch guard**, D17), `escalate(bead, reason, detail="")`, `escalate_from(op, reason, detail="")`, `replay(op)`, `replay_park(op) -> BeadState`, `replay_resume(op) -> Launch`, `replay_release(op) -> BeadState`, `replay_escalate(op)`.
  - `tests/wsd_env.py`: `PROFILES`, `Rig` (`world`, `runtime`, `repo`, `ws`, `cp`, `journal`, `beads`, `gate`, `deps`, `parker`; `restart(cp=None)`, `start(bead)` (claim, worktree, launched-session record, launch, state RUNNING), `key(bead)` (the recorded session key), `replay_open()`, `worktree(bead)`, `state(bead)`), `make_rig(tmp_path, cp=None, limits=None) -> Rig`.

- **Park:** intent → stop every session of the bead (unconfirmed: the workstream holds `runtime_unavailable`, the bead stays PARKING, no budget spent) → WIP commit (SHA recorded) → blocking edges → labels (`v2:held` first when held, then `v2:parked`) → bead comment. A missing or unreadable session record, or a worktree that fails verification, escalates.
- **Resume:** intent → remove `v2:parked` (a replay that finds it gone treats that as its own unlabel) → the launch guard, which reads the record already on the bead.
- **Release:** intent → remove `v2:held` and `needs-human` → a parked bead goes back to PARKED or WAITING_INPUT; an unparked one gets a resume operation at `unlabelled`, so it launches only through the guard. Sessions under another key are stopped before a replacement record is written.
- **Escalate:** the STUCK row and the escalation open in one transaction, then `needs-human`, replayed until it reads back.
- **The guard** (D17): the only call to `AgentRuntime.launch`. `RuntimeUnavailable` is a hold (`WAIT`), `LaunchFailed` spends the operation's budget (`FAILED`, then escalation), anything else is `UNCERTAIN`: the workstream holds `launch_uncertain` and the role stays taken until the session list settles it. However an operation ends, it clears its own `launch_uncertain` hold.

Each external effect is followed by its `!` barrier and each journal write by its step checkpoint; the crash tests restart at every point and replay.

- [ ] **Step 1: Create `$HZ/tests/wsd_env.py`**

Replace the Task 4 file. `restart()` models wsd dying and starting again: a new `Journal` on the same file and new wsd objects, while the queue, the repository and the agent sessions carry on. `start()` does what a pickup does for one bead without the pickup journal (Task 7 adds pickup).

```python
"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place, record

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@dataclass
class Rig:
    root: Path
    world: World
    runtime: FakeRuntime
    repo: Path
    ws: WorkstreamSettings
    cp: Checkpoint = field(default_factory=Recorder)
    reconciler: ActionReconciler | None = None
    journal: Journal = field(init=False)
    beads: BeadsAdapter = field(init=False)
    gate: ClaimGate = field(init=False)
    deps: Deps = field(init=False)
    parker: Parker = field(init=False)

    def __post_init__(self) -> None:
        self.restart(self.cp)

    def restart(self, cp: Checkpoint | None = None) -> None:
        if hasattr(self, "journal"):
            self.journal.close()
        self.cp = cp if cp is not None else Recorder()
        self.journal = Journal(self.root / "state" / "wsd.db")
        self.beads = BeadsAdapter(factory(self.world))
        self.gate = ClaimGate(self.root / "state" / "claims", self.beads, self.cp)
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp)
        self.parker = Parker(self.ws, self.deps)

    def start(self, bead: str) -> None:
        """What a pickup does for one bead, without the pickup op: claim, worktree, record, launch."""
        self.gate.claim(WS, bead)
        spot = place(self.ws, self.beads.show(WS, bead))
        self.beads.worktree(WS, bead, spot.repo)
        self.beads.ensure_record(WS, bead, record(self.ws, spot))
        self.runtime.launch(LaunchSpec(WS, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                       spot.label, spot.worktree, resume=False))
        for step in (BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING):
            self.journal.set_state(WS, bead, step)

    def key(self, bead: str) -> str:
        """The coder session key of a started bead, from its launched-session record."""
        found = self.beads.show(WS, bead).record()
        assert found is not None
        return found.session_key

    def replay_open(self) -> None:
        for op in self.journal.ops_open(WS):
            self.parker.replay(op)

    def worktree(self, bead: str) -> Path:
        return self.repo.parent / f"{self.repo.name}-btq-{bead}"

    def state(self, bead: str) -> str | None:
        row = self.journal.state(WS, bead)
        return None if row is None else row.state.value


def make_rig(tmp_path: Path, cp: Checkpoint | None = None, limits: Limits | None = None) -> Rig:
    world = World(tmp_path / "btq-state")
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits())
    return Rig(tmp_path, world, FakeRuntime(), repo, ws, cp if cp is not None else Recorder())
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_park.py`**

```python
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, git_repo, make_rig

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY, BeadsUnavailable
from heterodyne.wsd.journal import OpKind
from heterodyne.wsd.park import (
    ESCALATE_POINTS,
    PARK_MARK,
    PARK_POINTS,
    RELEASE_POINTS,
    RESUME_POINTS,
    Launch,
    NotReleasable,
    resumable,
)
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.states import Reason
from heterodyne.wsd.workstream import Limits, WorkstreamSettings


def running(tmp_path: Path, cp: Recorder | None = None, limits: Limits | None = None) -> Rig:
    rig = make_rig(tmp_path, cp=cp, limits=limits)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")       # the bead btq-1 will wait on
    rig.start("btq-1")
    (rig.worktree("btq-1") / "work.txt").write_text("half done")
    return rig


def parked(tmp_path: Path, limits: Limits | None = None) -> Rig:
    """btq-1 parked on btq-2, and btq-2 since closed: resumable."""
    rig = running(tmp_path, limits=limits)
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    return rig


def wip_commits(rig: Rig) -> list[str]:
    out = gitwip.git(rig.worktree("btq-1"), "log", "--format=%H", f"--grep={PARK_MARK}")
    return out.split()


def resume(rig: Rig, ref: str | None = None) -> Launch:
    with rig.parker.entry():
        return rig.parker.resume(rig.beads.show(WS, "btq-1"), ref=ref)


def reason(rig: Rig, bead: str = "btq-1") -> Reason | None:
    row = rig.journal.state(WS, bead)
    assert row is not None
    return row.reason


# --- park ---

def test_park_sequence(tmp_path: Path) -> None:
    rig = running(tmp_path)
    key = rig.key("btq-1")
    rig.restart(Recorder())
    assert rig.parker.park("btq-1", ("btq-2",), why="needs the schema", ref="msg-7").value == "parked"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in PARK_POINTS] == list(PARK_POINTS)
    bead = rig.world.beads["btq-1"]
    assert bead.status == "in_progress"                            # never unclaimed
    assert PARKED in bead.labels and ("btq-2", "blocks") in bead.deps
    assert len(wip_commits(rig)) == 1
    assert gitwip.git(rig.worktree("btq-1"), "status", "--porcelain") == ""
    [comment] = bead.comments
    assert "btq-2" in comment and "needs the schema" in comment
    assert rig.runtime.stops == [key]
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "parked" and row.reason is Reason.BLOCKED_ON_BEAD
    assert row.detail == "btq-2"
    progress = [(e.kind, e.ref) for e in rig.journal.events_since(0) if e.bead == "btq-1"]
    assert ("state:parking", "msg-7") in progress and ("state:parked", "msg-7") in progress
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("point", PARK_POINTS)
def test_crash_at_every_park_point_completes_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",), why="w")
    rig.restart()
    rig.replay_open()
    if point == "lock.waiting":                                     # died at the door: nothing happened
        assert rig.state("btq-1") == "running" and rig.journal.ops_open() == []
        return
    bead = rig.world.beads["btq-1"]
    assert PARKED in bead.labels and bead.deps == [("btq-2", "blocks")]
    assert len(bead.comments) == 1
    assert len(wip_commits(rig)) == 1
    assert rig.state("btq-1") == "parked"
    assert rig.journal.ops_open() == []
    assert rig.runtime.live() == [] and len(rig.runtime.launches) == 1     # never relaunched while parked


def test_park_stops_every_session_of_the_bead(tmp_path: Path) -> None:
    """A second, unrecorded session of the bead (one listed as UNKNOWN, say) is stopped too: a park never
    commits under a session that may still write."""
    rig = running(tmp_path)
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "other-key", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.park("btq-1", ("btq-2",))
    assert sorted(rig.runtime.stops) == sorted([first.session_key, "other-key"])
    assert rig.state("btq-1") == "parked"


def test_unconfirmed_stop_holds_and_never_spends_budget(tmp_path: Path) -> None:
    """Finding 10/11: a stop the runtime can't confirm is a hold, not a failure. No WIP commit, no label,
    no budget, no `needs-human`, however often it repeats; it completes once the stop is confirmed."""
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.runtime.stop_failures = 5
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    for _ in range(4):
        rig.replay_open()
    assert reason(rig) is Reason.STOP_UNCONFIRMED
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    bead = rig.world.beads["btq-1"]
    assert PARKED not in bead.labels and NEEDS_HUMAN not in bead.labels and wip_commits(rig) == []
    [op] = rig.journal.ops_open()
    assert op.attempts == 0 and op.step == "intent"
    rig.replay_open()
    assert rig.state("btq-1") == "parked"


def test_git_failure_retries_then_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=2))
    gitdir = Path(gitwip.git(rig.worktree("btq-1"), "rev-parse", "--absolute-git-dir").strip())
    (gitdir / "index.lock").write_text("")                          # every `git add` fails
    assert rig.parker.park("btq-1", ("btq-2",)).value == "parking"
    assert reason(rig) is Reason.PARK_FAILED
    assert PARKED not in rig.world.beads["btq-1"].labels           # no label before the WIP commit
    rig.replay_open()                                               # second failure
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.PARK_FAILED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.journal.ops_open() == []


def test_park_without_a_session_record_escalates(tmp_path: Path) -> None:
    """Finding 11: the worktree comes from the record, never from the current placement. No record: no
    commit anywhere, a human looks."""
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.LAUNCH_UNRECORDED
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels and wip_commits(rig) == []


def test_park_with_an_unreadable_record_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.beads["btq-1"].metadata[RECORD_KEY] = "{not json"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.LAUNCH_UNRECORDED and wip_commits(rig) == []


def test_park_into_a_foreign_worktree_escalates(tmp_path: Path) -> None:
    """Finding 15: a record naming a worktree of another repository (same path layout, same branch name)
    fails the common-dir check, and nothing is committed there."""
    rig = running(tmp_path)
    other = git_repo(tmp_path / "elsewhere" / "proj")
    foreign = other.parent / "proj-btq-btq-1"
    gitwip.git(other, "worktree", "add", "-q", "-b", "btq/btq-1", str(foreign))
    bead = rig.world.beads["btq-1"]
    bead.metadata[RECORD_KEY] = bead.metadata[RECORD_KEY].replace(str(rig.worktree("btq-1")), str(foreign))
    base = gitwip.git(other, "rev-parse", "HEAD").strip()
    bead.notes += f"\nworker={bead.assignee}; repository={rig.repo}; base={base}; worktree={foreign}"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.WORKTREE_FAILED
    assert gitwip.git(foreign, "log", "--format=%H", f"--grep={PARK_MARK}").split() == []


def test_beads_outage_during_park_never_escalates(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.fault("dep", RuntimeError("dolt down"), times=2)
    for _ in range(2):
        with pytest.raises(BeadsUnavailable):
            rig.replay_open() if rig.journal.ops_open() else rig.parker.park("btq-1", ("btq-2",))
        assert rig.state("btq-1") == "parking"
        assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.replay_open()
    assert rig.state("btq-1") == "parked"


def test_park_of_a_bead_not_ours_is_abandoned(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.beads["btq-1"].assignee = "someone-else"
    assert rig.parker.park("btq-1", ("btq-2",)).value == "stuck"
    assert reason(rig) is Reason.CLAIM_LOST
    assert PARKED not in rig.world.beads["btq-1"].labels


def test_operator_hold_is_not_resumable(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    bead = rig.beads.show(WS, "btq-1")
    assert HELD in bead.labels and not resumable(bead)
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "held" and row.reason is Reason.HELD_BY_OPERATOR


def test_question_blocker_is_waiting_input(tmp_path: Path) -> None:
    rig = running(tmp_path)
    rig.world.add("btq-q", labels=["kind:question"])
    rig.world.beads["btq-q"].labels.remove("agent:wsd")
    rig.parker.park("btq-1", ("btq-q",), why="which schema?")
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.state.value == "waiting_input" and row.reason is Reason.WAITING_ON_OPERATOR


def test_park_needs_a_blocker_or_a_hold(tmp_path: Path) -> None:
    rig = running(tmp_path)
    with pytest.raises(ValueError, match="blocker"):
        rig.parker.park("btq-1", ())
    assert rig.journal.ops_open() == []


# --- resume and the launch guard ---

def test_resume_sequence(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    assert resumable(rig.beads.show(WS, "btq-1"))
    rig.restart(Recorder())
    assert resume(rig, ref="msg-9") is Launch.STARTED
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in RESUME_POINTS] == list(RESUME_POINTS)
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.ref) == ("btq-1", True, "msg-9")
    assert spec.worktree == rig.worktree("btq-1")
    assert spec.session_key == rig.runtime.launches[0].session_key
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "running"


@pytest.mark.parametrize("point", RESUME_POINTS)
def test_crash_at_every_resume_point_launches_once(tmp_path: Path, point: str) -> None:
    """Finding 7 among them: at `resume.unlabelled!` the label is gone but the step is not journaled. The
    replay reads that as its own unlabel and goes on to launch; it is never a cancellation."""
    rig = parked(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.restart()
    rig.replay_open()
    assert len([s for s in rig.runtime.launches if s.resume]) == 1
    assert rig.runtime.coders() == ["btq-1"]
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_resume_uses_the_recorded_identity_after_a_config_change(tmp_path: Path) -> None:
    """Finding 4: the operator changed the default profile and repository since the launch. The resume is
    the recorded session in the recorded worktree, never a new placement."""
    rig = parked(tmp_path)
    first = rig.runtime.launches[0]
    other = git_repo(tmp_path / "repos" / "other")
    rig.ws = WorkstreamSettings(WS, {"default": other}, "coder", "p-two", rig.ws.profiles, rig.ws.limits)
    rig.restart()
    assert resume(rig) is Launch.STARTED
    spec = rig.runtime.launches[-1]
    assert (spec.profile, spec.session_key, spec.worktree) == (first.profile, first.session_key,
                                                               first.worktree)


def test_resume_abandoned_when_blocked_again(tmp_path: Path) -> None:
    """Between the decision to resume and the replay, the bead gained an open blocker: the resume ends
    with the bead still parked, and nothing is launched."""
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.world.beads["btq-1"].deps.append(("btq-3", "blocks"))
    assert resume(rig) is Launch.ENDED
    assert [s.resume for s in rig.runtime.launches] == [False]
    assert PARKED in rig.world.beads["btq-1"].labels and rig.state("btq-1") == "parked"


def test_hold_added_after_the_unlabel_shelves_the_bead(tmp_path: Path) -> None:
    """The operator held the bead between the unlabel and the replay: the guard puts `v2:parked` back and
    records it as HELD. Nothing is launched."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.world.beads["btq-1"].labels.append(HELD)
    rig.restart()
    rig.replay_open()
    assert PARKED in rig.world.beads["btq-1"].labels and rig.state("btq-1") == "held"
    assert [s.resume for s in rig.runtime.launches] == [False] and rig.journal.ops_open() == []


@pytest.mark.parametrize("change", ["routing", "design"])
def test_guard_revalidates_routing_and_design_approval(tmp_path: Path, change: str) -> None:
    """Findings 5 and 6: btq's post-claim checks run again right before the launch. A bead that moved to
    another workstream, or lost its design approval, is escalated and not launched."""
    rig = parked(tmp_path)
    bead = rig.world.beads["btq-1"]
    if change == "routing":
        bead.labels[bead.labels.index("ws:alpha")] = "ws:beta"
    else:
        del bead.metadata["design_approval"]
    assert resume(rig) is Launch.ENDED
    assert reason(rig) is Reason.ROUTING_CHANGED and NEEDS_HUMAN in bead.labels
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_refuses_a_lost_claim(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.world.beads["btq-1"].assignee = "someone-else"
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and reason(rig) is Reason.CLAIM_LOST
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_guard_waits_while_another_bead_holds_the_coder_role(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.world.add("btq-3")
    rig.start("btq-3")
    assert resume(rig) is Launch.WAIT
    assert rig.runtime.coders() == ["btq-3"]
    [op] = rig.journal.ops_open()
    assert op.kind is OpKind.RESUME and op.attempts == 0


def test_no_runtime_waits_without_budget_or_escalation(tmp_path: Path) -> None:
    """Finding 10: RuntimeUnavailable is a hold. It never counts as a launch failure."""
    rig = parked(tmp_path, limits=Limits(launch_failures_before_human=1))
    rig.runtime.up = False
    for _ in range(3):
        assert (resume(rig) if not rig.journal.ops_open() else rig.parker.launch(rig.journal.ops_open()[0])
                ) is Launch.WAIT
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    [op] = rig.journal.ops_open()
    assert op.attempts == 0


def test_confirmed_launch_failure_retries_then_escalates(tmp_path: Path) -> None:
    rig = parked(tmp_path, limits=Limits(launch_failures_before_human=2))
    rig.runtime.launch_failures = 2
    assert resume(rig) is Launch.FAILED
    assert reason(rig) is Reason.LAUNCH_FAILED and rig.runtime.coders() == []
    rig.replay_open()
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_uncertain_launch_keeps_the_role_until_the_list_settles_it(tmp_path: Path) -> None:
    """Finding 16: an uncertain launch is never retried blind. The workstream holds LAUNCH_UNCERTAIN; the
    replay waits while the session is listed UNKNOWN and finishes once it is listed live."""
    rig = parked(tmp_path)
    rig.runtime.launch_uncertain = 1
    assert resume(rig) is Launch.UNCERTAIN
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    rig.replay_open()
    assert len(rig.runtime.launches) == 2 and rig.journal.ops_open() != []
    rig.runtime.set(rig.key("btq-1"), Liveness.LIVE)
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}
    assert len(rig.runtime.launches) == 2 and rig.journal.ops_open() == []


def test_uncertain_launch_that_never_started_is_launched_again(tmp_path: Path) -> None:
    rig = parked(tmp_path)
    rig.runtime.launch_uncertain = 1
    assert resume(rig) is Launch.UNCERTAIN
    rig.runtime.end(rig.key("btq-1"))                              # the runtime confirms it is not running
    rig.replay_open()
    assert rig.runtime.coders() == ["btq-1"] and rig.state("btq-1") == "running"
    assert rig.journal.holds(WS) == {}


# --- release and escalate ---

def held(tmp_path: Path) -> Rig:
    rig = running(tmp_path)
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    return rig


def test_release_of_a_held_parked_bead_makes_it_resumable(tmp_path: Path) -> None:
    rig = held(tmp_path)
    rig.restart(Recorder())
    assert rig.parker.release("btq-1", ref="msg-r").value == "parked"
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in RELEASE_POINTS] == list(RELEASE_POINTS)
    bead = rig.beads.show(WS, "btq-1")
    assert HELD not in bead.labels and resumable(bead)
    assert rig.journal.ops_open() == []


@pytest.mark.parametrize("point", RELEASE_POINTS[1:])
def test_crash_at_every_release_point_completes_once(tmp_path: Path, point: str) -> None:
    rig = held(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.release("btq-1")
    rig.restart()
    rig.replay_open()
    assert rig.state("btq-1") == "parked" and rig.journal.ops_open() == []
    assert HELD not in rig.world.beads["btq-1"].labels


def test_release_of_a_stuck_running_bead_resumes_through_the_guard(tmp_path: Path) -> None:
    rig = running(tmp_path, limits=Limits(launch_failures_before_human=1))
    rig.runtime.end(rig.key("btq-1"))
    rig.parker.escalate("btq-1", Reason.LAUNCH_FAILED)
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.parker.release("btq-1").value == "resuming"
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    rig.replay_open()
    assert rig.state("btq-1") == "running" and rig.runtime.coders() == ["btq-1"]
    assert rig.runtime.launches[-1].session_key == rig.runtime.launches[0].session_key


def test_release_stops_unrecorded_sessions_before_writing_a_record(tmp_path: Path) -> None:
    rig = running(tmp_path)
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    first = rig.runtime.launches[0]
    rig.runtime.adopt(type(first)(WS, "btq-1", "coder", "p-two", "stray", first.label, first.worktree,
                                  resume=False), Liveness.UNKNOWN)
    rig.parker.escalate("btq-1", Reason.LAUNCH_UNRECORDED)
    assert rig.parker.release("btq-1").value == "resuming"
    assert "stray" in rig.runtime.stops and "stray" not in rig.runtime.listed
    assert rig.beads.show(WS, "btq-1").record() is not None


def test_only_held_or_stuck_beads_are_releasable(tmp_path: Path) -> None:
    rig = running(tmp_path)
    with pytest.raises(NotReleasable):
        rig.parker.release("btq-1")
    with pytest.raises(NotReleasable):
        rig.parker.release("btq-404")


@pytest.mark.parametrize("point", ESCALATE_POINTS)
def test_crash_at_every_escalate_point_labels_once(tmp_path: Path, point: str) -> None:
    rig = running(tmp_path)
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        rig.parker.escalate("btq-1", Reason.UNEXPECTED_STATE)
    rig.restart()
    rig.replay_open()
    assert rig.world.beads["btq-1"].labels.count(NEEDS_HUMAN) == 1
    assert rig.state("btq-1") == "stuck" and rig.journal.ops_open() == []


def test_guard_never_launches_beside_an_unknown_session_of_its_bead(tmp_path: Path) -> None:
    """Finding 6: the replay finds the bead's own session listed but not confirmed live. It holds; it
    neither launches a second one nor assumes the first is running."""
    rig = parked(tmp_path)
    rig.restart(CrashAt("resume.unlabelled"))
    with pytest.raises(SimulatedCrash):
        resume(rig)
    rig.runtime.adopt(rig.runtime.launches[0], Liveness.UNKNOWN)
    rig.restart()
    rig.replay_open()
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    assert len(rig.runtime.launches) == 1 and rig.journal.ops_open() != []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_park.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.park'` (or `heterodyne.wsd.workstream`).

- [ ] **Step 4: Create `$HZ/src/heterodyne/wsd/workstream.py`**

```python
"""One workstream's settings, and where a bead runs: repository, worktree, profile and session key.

`place` is used only where wsd starts something new: a pickup's worktree and record, and a release that
must record a bead whose launch was never recorded. Everything that continues existing work (resume,
park, recovery) reads the launched-session record on the bead instead, so a configuration change never
redirects a running or parked bead to another session or worktree.
"""

from dataclasses import dataclass, field
from pathlib import Path

from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter, SessionRecord
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime

REPO_KEY = "repo"            # bead metadata naming one of the workstream's [repos]
DEFAULT_REPO = "default"
LABEL_TITLE_CHARS = 40


class ConfigInvalid(Exception):
    """The bead can't be placed: no such repository, or an unknown or ambiguous profile."""


@dataclass(frozen=True)
class Limits:
    launch_failures_before_human: int = 2      # §9 reconcile, §10 agent crash
    park_attempts_before_human: int = 3


@dataclass(frozen=True)
class WorkstreamSettings:
    name: str
    repos: dict[str, Path]
    coder_role: str
    coder_profile: str
    profiles: frozenset[str]
    limits: Limits = field(default_factory=Limits)


@dataclass(frozen=True)
class Placement:
    repo: Path
    worktree: Path
    profile: str
    session_key: str
    label: str


def place(ws: WorkstreamSettings, bead: Bead) -> Placement:
    name = bead.metadata.get(REPO_KEY, DEFAULT_REPO)
    if not isinstance(name, str) or name not in ws.repos:
        raise ConfigInvalid("the bead names no repository of this workstream")
    try:
        profile = ids.profile_for(bead.labels, ws.coder_role, ws.coder_profile)
        session_key = ids.role_session(bead.id, ws.coder_role, profile)
    except ids.BadName as exc:
        raise ConfigInvalid(str(exc)) from None
    if profile not in ws.profiles:
        raise ConfigInvalid("the bead names an unknown profile")
    repo = ws.repos[name].resolve()
    return Placement(repo, repo.parent / f"{repo.name}-btq-{bead.id}", profile, session_key,
                     label(bead, ws.coder_role))


def label(bead: Bead, role: str) -> str:
    """The session label (§4.1): "<bead> · <role> · <title>"."""
    title = " ".join(bead.title.split())[:LABEL_TITLE_CHARS]
    return f"{bead.id} · {role} · {title}"


def record(ws: WorkstreamSettings, spot: Placement) -> SessionRecord:
    return SessionRecord(ws.coder_role, spot.profile, spot.session_key, str(spot.repo), str(spot.worktree))


@dataclass(frozen=True)
class Deps:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    runtime: AgentRuntime
    reconciler: ActionReconciler
    cp: Checkpoint = nothing
```

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/park.py`**

```python
"""Every journaled bead operation but the claim (ADR 0001 §3.3, §4.3), and the one launch path.

- Park: intent, stop every session of the bead, WIP commit (SHA recorded), blocking edges, labels, bead
  comment. Blocking edges go on before `v2:parked`, and `v2:held` before `v2:parked`, so the bead is
  never `v2:parked` without what keeps it from being resumed.
- Resume: intent, remove `v2:parked`, then the launch guard. A replay that finds `v2:parked` already
  gone is its own completed unlabel, not a cancellation.
- Release (the operator's, plan 6): remove `v2:held` and `needs-human`, then either back to parked or
  on to a resume. It is the only way out of HELD or STUCK.
- Escalate: STUCK in the journal, then `needs-human` on the bead, replayed until it reads back.
- `launch`, the guard: the only call to `AgentRuntime.launch`, used by pickup, resume and their replays.

Every external effect is followed by a `<op>.<step>!` checkpoint, and every journal write by
`<op>.<step>`, so a test can crash on either side of each write. `entry()` is the workstream's one
lock: park, release, pickup and recovery all take it, so no two operations interleave.

wsd never unclaims: a parked or stuck bead stays `in_progress` under its per-bead worker.
BeadsUnavailable is never caught here: it propagates, the operation stays open, and the caller holds
the workstream until beads answer again (§10). RuntimeUnavailable is a hold too: it uses no failure
budget and never escalates.
"""

import contextlib
import threading
from collections.abc import Generator
from enum import StrEnum
from pathlib import Path

from heterodyne.wsd import gitwip
from heterodyne.wsd.beads import (
    HELD,
    NEEDS_HUMAN,
    PARKED,
    Bead,
    NotOurs,
    RecordUnreadable,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec, Liveness, RuntimeUnavailable, Session
from heterodyne.wsd.states import BeadState, Reason
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, label, place, record

PARK_POINTS = ("lock.waiting", "park.intent", "park.stopped!", "park.stopped", "park.committed!",
               "park.committed", "park.blocked!", "park.blocked", "park.labelled!", "park.labelled",
               "park.commented!", "park.done")
RESUME_POINTS = ("resume.intent", "resume.unlabelled!", "resume.unlabelled", "resume.launched!",
                 "resume.done")
RELEASE_POINTS = ("lock.waiting", "release.intent", "release.unlabelled!", "release.unlabelled",
                  "release.done")
ESCALATE_POINTS = ("escalate.intent", "escalate.labelled!", "escalate.done")
PARK_MARK = "wsd-park: "
REASONS = {BeadState.HELD: Reason.HELD_BY_OPERATOR, BeadState.WAITING_INPUT: Reason.WAITING_ON_OPERATOR,
           BeadState.PARKED: Reason.BLOCKED_ON_BEAD}
RELEASABLE = frozenset({BeadState.HELD, BeadState.STUCK})
# Holds the guard settles itself from the session list instead of waiting on.
GUARD_SETTLES = frozenset({Reason.LAUNCH_UNCERTAIN})


class Launch(StrEnum):
    """What the launch guard did."""
    STARTED = "started"        # launched now
    LIVE = "live"              # the recorded session was already running
    WAIT = "wait"              # not attempted: a hold applies or the role is taken; the operation stays open
    UNCERTAIN = "uncertain"    # attempted, outcome unknown: the role stays taken and the workstream holds
    FAILED = "failed"          # the runtime confirmed nothing started; the operation is retried later
    ENDED = "ended"            # the operation finished without a launch (shelved, stuck or claim lost)


class NotReleasable(Exception):
    """Release applies only to a HELD or STUCK bead with no operation open on it."""


def park_comment(op: Op) -> str:
    blockers = op.data.get("blockers") or "none"
    why = op.data.get("why") or "no reason given"
    return (f"Parked by wsd ({PARK_MARK}{op.op_id}). WIP commit {op.data.get('sha', '?')}. "
            f"Waiting on: {blockers}. Why: {why}.")


def parked_state(bead: Bead) -> BeadState:
    """What a `v2:parked` bead is waiting for, from its labels and open blockers."""
    if HELD in bead.labels:
        return BeadState.HELD
    if bead.waits_on_operator():
        return BeadState.WAITING_INPUT
    return BeadState.PARKED


def blocker_detail(bead: Bead) -> str:
    """The open blockers, for the state's detail: plan 6 counts "N beads on M approvals" from it."""
    return ",".join(sorted(d.id for d in bead.open_blockers()))


def resumable(bead: Bead) -> bool:
    """§4.3: claimed by wsd (the caller checked), `v2:parked`, every blocking edge closed. A bead held by
    the operator or escalated to a human is not resumable."""
    return (PARKED in bead.labels and HELD not in bead.labels and NEEDS_HUMAN not in bead.labels
            and not bead.open_blockers())


class Parker:
    def __init__(self, ws: WorkstreamSettings, deps: Deps) -> None:
        self.ws = ws
        self.d = deps
        self.lock = threading.RLock()

    @contextlib.contextmanager
    def entry(self) -> Generator[None]:
        """The workstream's operation lock. Every public entry point (park, release, pickup, recovery)
        takes it; the checkpoint before it lets a test hold one caller at the door."""
        self.d.cp("lock.waiting")
        with self.lock:
            yield

    # --- shared ---

    def _finish(self, op: Op, status: OpStatus, state: BeadState | None, reason: Reason | None = None,
                detail: str = "") -> None:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self._settle_uncertain(op)
            if state is not None:
                self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail,
                                         op.data.get("ref") or None)

    def _settle_uncertain(self, op: Op) -> None:
        """An operation that ends, however it ends, settles its own LAUNCH_UNCERTAIN hold: any session it
        may have started is listed by the runtime, and the sweep and the role check take it from there."""
        if self.d.journal.holds(self.ws.name).get(Reason.LAUNCH_UNCERTAIN) == op.bead:
            self.d.journal.unhold(self.ws.name, Reason.LAUNCH_UNCERTAIN)

    def _sessions(self, bead: str | None = None) -> list[Session]:
        found = self.d.runtime.sessions(self.ws.name)
        return [s for s in found if bead is None or s.bead == bead]

    def _runtime_hold(self, op: Op, reason: Reason = Reason.RUNTIME_UNAVAILABLE) -> BeadState:
        """Hold the workstream; the operation stays open at its step and uses no failure budget."""
        j = self.d.journal
        j.hold(self.ws.name, Reason.RUNTIME_UNAVAILABLE)
        row = j.state(self.ws.name, op.bead)
        if row is None:
            return BeadState.STUCK
        j.set_state(self.ws.name, op.bead, row.state, reason)
        return row.state

    # --- escalate ---

    def escalate_from(self, op: Op, reason: Reason, detail: str = "") -> None:
        """End `op` as STUCK and open the escalation in the same transaction, so a crash can't leave a
        stuck bead without its pending `needs-human`."""
        j = self.d.journal
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.STUCK)
            self._settle_uncertain(op)
            esc = j.op_open(OpKind.ESCALATE, self.ws.name, op.bead, {"reason": reason.value})
            j.set_state(self.ws.name, op.bead, BeadState.STUCK, reason, detail, op.data.get("ref") or None)
        self.d.cp("escalate.intent")
        self.replay_escalate(esc)

    def escalate(self, bead: str, reason: Reason, detail: str = "") -> None:
        """Recovery's escalation: the journal row is rebuilt as STUCK whatever it said (beads are the
        truth, and they contradict it), then `needs-human` goes on the bead."""
        j = self.d.journal
        with j.transaction():
            esc = j.op_open(OpKind.ESCALATE, self.ws.name, bead, {"reason": reason.value})
            j.adopt(self.ws.name, bead, BeadState.STUCK, reason, detail)
        self.d.cp("escalate.intent")
        self.replay_escalate(esc)

    def replay_escalate(self, op: Op) -> None:
        try:
            self.d.beads.ensure_label(self.ws.name, op.bead, NEEDS_HUMAN)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, None)      # the row already says STUCK
            return
        self.d.cp("escalate.labelled!")
        self._finish(op, OpStatus.DONE, None)
        self.d.cp("escalate.done")

    # --- park ---

    def park(self, bead: str, blockers: tuple[str, ...], why: str = "", hold: bool = False,
             ref: str | None = None) -> BeadState:
        """Park a bead wsd runs. `blockers` are the beads it waits on; `hold` parks it for the operator
        (/stop) until they release it. Returns the bead's state afterwards (PARKING if a step must be
        retried). Raises OpConflict if another operation is open on the bead: the caller retries after
        the next pickup, it never runs beside it."""
        if not blockers and not hold:
            raise ValueError("a park needs a blocker or an operator hold")
        with self.entry():
            j = self.d.journal
            with j.transaction():
                op = j.op_open(OpKind.PARK, self.ws.name, bead,
                               {"blockers": ",".join(blockers), "why": why, "hold": "1" if hold else "",
                                "ref": ref or ""})
                j.set_state(self.ws.name, bead, BeadState.PARKING, ref=ref)
            self.d.cp("park.intent")
            return self.replay_park(op)

    def replay_park(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        try:
            if op.step == "intent":
                for session in self._sessions(bead):
                    self.d.runtime.stop(session.key)
                self.d.cp("park.stopped!")
                op = j.op_step(op.op_id, "stopped")
                self.d.cp("park.stopped")
            if op.step == "stopped":
                rec = self.d.beads.show(ws, bead).record()
                if rec is None:
                    self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "no session record names the worktree")
                    return BeadState.STUCK
                worktree = self.d.beads.verify_worktree(ws, bead, Path(rec.repo), Path(rec.worktree))
                sha = gitwip.wip_commit(worktree, op.op_id, f"parked {bead}")
                self.d.cp("park.committed!")
                op = j.op_step(op.op_id, "committed", {"sha": sha})
                self.d.cp("park.committed")
            if op.step == "committed":
                for blocker in filter(None, op.data.get("blockers", "").split(",")):
                    self.d.beads.ensure_blocker(ws, bead, blocker)
                self.d.cp("park.blocked!")
                op = j.op_step(op.op_id, "blocked")
                self.d.cp("park.blocked")
            if op.step == "blocked":
                if op.data.get("hold"):
                    self.d.beads.ensure_label(ws, bead, HELD)
                self.d.beads.ensure_label(ws, bead, PARKED)
                self.d.cp("park.labelled!")
                op = j.op_step(op.op_id, "labelled")
                self.d.cp("park.labelled")
            self.d.beads.ensure_comment(ws, bead, f"{PARK_MARK}{op.op_id}", park_comment(op))
            self.d.cp("park.commented!")
            shown = self.d.beads.show(ws, bead)
            final = parked_state(shown)
            self._finish(op, OpStatus.DONE, final, REASONS[final], blocker_detail(shown))
            self.d.cp("park.done")
            return final
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return BeadState.STUCK
        except RecordUnreadable:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "the session record does not parse")
            return BeadState.STUCK
        except WorktreeConflict as exc:
            self.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
            return BeadState.STUCK
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        except gitwip.GitFailed as exc:
            if j.op_failed(op.op_id) >= self.ws.limits.park_attempts_before_human:
                self.escalate_from(op, Reason.PARK_FAILED, str(exc))
                return BeadState.STUCK
            j.set_state(ws, bead, BeadState.PARKING, Reason.PARK_FAILED, str(exc))
            return BeadState.PARKING

    # --- resume ---

    def resume(self, bead: Bead, ref: str | None = None) -> Launch:
        """Resume a resumable parked bead as its recorded session in its recorded worktree. The caller
        (pickup) holds `entry()`."""
        j = self.d.journal
        with j.transaction():
            op = j.op_open(OpKind.RESUME, self.ws.name, bead.id, {"ref": ref or ""})
            j.set_state(self.ws.name, bead.id, BeadState.RESUMING, ref=ref)
        self.d.cp("resume.intent")
        return self.replay_resume(op)

    def replay_resume(self, op: Op) -> Launch:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        if op.step == "intent":
            shown = self.d.beads.show(ws, bead)
            # `v2:parked` already gone is this operation's own unlabel, done before a crash: go on. The
            # guard below re-checks everything that would make launching wrong.
            if PARKED in shown.labels:
                if NEEDS_HUMAN in shown.labels:
                    self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.NEEDS_HUMAN)
                    return Launch.ENDED
                if not resumable(shown):         # it stopped being resumable meanwhile
                    final = parked_state(shown)
                    self._finish(op, OpStatus.ABANDONED, final, REASONS[final], blocker_detail(shown))
                    return Launch.ENDED
                try:
                    self.d.beads.ensure_label(ws, bead, PARKED, present=False)
                except NotOurs:
                    self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
                    return Launch.ENDED
            self.d.cp("resume.unlabelled!")
            op = j.op_step(op.op_id, "unlabelled")
            self.d.cp("resume.unlabelled")
        return self.launch(op)

    # --- release ---

    def release(self, bead: str, ref: str | None = None) -> BeadState:
        """The operator's release of a HELD or STUCK bead (plan 6). Removes `v2:held` and `needs-human`;
        a parked bead goes back to waiting on its blockers, any other goes on to a resume through the
        launch guard at the next pickup. An external removal of either label never does this: the
        journal keeps the bead HELD or STUCK until release runs."""
        with self.entry():
            j, ws = self.d.journal, self.ws.name
            with j.transaction():
                row = j.state(ws, bead)
                if row is None or row.state not in RELEASABLE or j.op_for(ws, bead) is not None:
                    raise NotReleasable(bead)
                op = j.op_open(OpKind.RELEASE, ws, bead, {"ref": ref or ""})
            self.d.cp("release.intent")
            return self.replay_release(op)

    def replay_release(self, op: Op) -> BeadState:
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        try:
            if op.step == "intent":
                self.d.beads.ensure_label(ws, bead, HELD, present=False)
                self.d.beads.ensure_label(ws, bead, NEEDS_HUMAN, present=False)
                self.d.cp("release.unlabelled!")
                op = j.op_step(op.op_id, "unlabelled")
                self.d.cp("release.unlabelled")
            shown = self.d.beads.show(ws, bead)
            if PARKED in shown.labels:
                final = parked_state(shown)
                self._finish(op, OpStatus.DONE, final, REASONS[final], blocker_detail(shown))
                self.d.cp("release.done")
                return final
            self._record_for_release(shown)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return BeadState.STUCK
        except ConfigInvalid as exc:
            self.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
            return BeadState.STUCK
        except RuntimeUnavailable:
            return self._runtime_hold(op, Reason.STOP_UNCONFIRMED)
        with j.transaction():
            j.op_finish(op.op_id, OpStatus.DONE)
            nxt = j.op_open(OpKind.RESUME, ws, bead, {"ref": op.data.get("ref", "")})
            j.op_step(nxt.op_id, "unlabelled")
            j.set_state(ws, bead, BeadState.RESUMING, ref=op.data.get("ref") or None)
        self.d.cp("release.done")
        return BeadState.RESUMING

    def _record_for_release(self, shown: Bead) -> None:
        """An unparked bead resumes through the guard, which needs a launched-session record. One that
        is missing or unreadable is replaced from the current placement, but only after every session
        of the bead under another key is confirmed stopped: nothing unrecorded keeps running."""
        try:
            rec = shown.record()
        except RecordUnreadable:
            rec = None
        own = self._sessions(shown.id)
        if rec is not None and all(s.key == rec.session_key for s in own):
            return
        new = rec if rec is not None else record(self.ws, place(self.ws, shown))
        for session in own:
            if session.key != new.session_key:
                self.d.runtime.stop(session.key)
        self.d.cp("release.stopped!")
        if rec is None:
            self.d.beads.ensure_record(self.ws.name, shown.id, new)
            self.d.cp("release.recorded!")

    # --- the launch guard ---

    def launch(self, op: Op, new: SessionRecord | None = None) -> Launch:
        """The one way wsd starts or resumes a session. In order: the runtime is up and no workstream
        hold applies; no other bead holds the coder role; the bead has no `needs-human`; btq's own
        post-claim checks pass (ownership, routing, design approval); the bead is runnable; the launched-
        session record is on the bead and its worktree verified; then the launch. `new` is the record a
        pickup writes; a resume reads the record already on the bead and never recomputes it."""
        ws, bead, j = self.ws.name, op.bead, self.d.journal
        kind = op.kind.value
        if not self.d.runtime.available():
            j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
            return Launch.WAIT
        if set(j.holds(ws)) - GUARD_SETTLES:
            return Launch.WAIT
        try:
            coder = [s for s in self._sessions() if s.role == self.ws.coder_role]
        except RuntimeUnavailable:
            j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
            return Launch.WAIT
        if any(s.bead != bead for s in coder):
            return Launch.WAIT               # one coder session at a time (§4.3)
        own = [s for s in coder if s.bead == bead]
        if NEEDS_HUMAN in self.d.beads.show(ws, bead).labels:
            self._finish(op, OpStatus.STUCK, BeadState.STUCK, Reason.NEEDS_HUMAN)
            return Launch.ENDED
        try:
            shown = self.d.beads.validate(ws, bead)
        except NotOurs:
            self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
            return Launch.ENDED
        except RoutingChanged:
            self.escalate_from(op, Reason.ROUTING_CHANGED)
            return Launch.ENDED
        if HELD in shown.labels or PARKED in shown.labels or shown.open_blockers():
            if own:
                self.escalate_from(op, Reason.UNEXPECTED_STATE, "not runnable, but its session is listed")
                return Launch.ENDED
            return self._shelve(op)
        try:
            if new is not None:
                self.d.beads.ensure_record(ws, bead, new)
                self.d.cp(f"{kind}.recorded!")
            rec = shown.record() if new is None else new
            if rec is None:
                self.escalate_from(op, Reason.LAUNCH_UNRECORDED)
                return Launch.ENDED
            worktree = self.d.beads.verify_worktree(ws, bead, Path(rec.repo), Path(rec.worktree))
        except RecordUnreadable:
            self.escalate_from(op, Reason.LAUNCH_UNRECORDED, "the session record does not parse")
            return Launch.ENDED
        except WorktreeConflict as exc:
            self.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
            return Launch.ENDED
        if any(s.liveness is not Liveness.LIVE or s.key != rec.session_key for s in own):
            return self._uncertain(op, "a session of this bead is listed but not confirmed live")
        if not own:
            spec = LaunchSpec(ws, bead, rec.role, rec.profile, rec.session_key, label(shown, rec.role),
                              worktree, resume=op.kind is OpKind.RESUME, ref=op.data.get("ref") or None)
            try:
                self.d.runtime.launch(spec)
            except RuntimeUnavailable:
                j.hold(ws, Reason.RUNTIME_UNAVAILABLE)
                return Launch.WAIT
            except LaunchFailed as exc:
                if j.op_failed(op.op_id) >= self.ws.limits.launch_failures_before_human:
                    self.escalate_from(op, Reason.LAUNCH_FAILED, str(exc))
                    return Launch.ENDED
                row = j.state(ws, bead)
                j.set_state(ws, bead, row.state if row else BeadState.STUCK, Reason.LAUNCH_FAILED, str(exc))
                return Launch.FAILED
            except Exception as exc:  # noqa: BLE001 - any other outcome is uncertain, never a failure
                return self._uncertain(op, type(exc).__name__)
            self.d.cp(f"{kind}.launched!")
        self._finish(op, OpStatus.DONE, BeadState.RUNNING)
        self.d.cp(f"{kind}.done")
        return Launch.LIVE if own else Launch.STARTED

    def _uncertain(self, op: Op, detail: str) -> Launch:
        """The role stays taken and the workstream holds; the next pickup's replay settles it from the
        session list (live: done; no longer listed: launch again; still unknown: keep holding)."""
        j = self.d.journal
        j.hold(self.ws.name, Reason.LAUNCH_UNCERTAIN, op.bead)
        row = j.state(self.ws.name, op.bead)
        if row is not None:
            j.set_state(self.ws.name, op.bead, row.state, Reason.LAUNCH_UNCERTAIN, detail)
        return Launch.UNCERTAIN

    def _shelve(self, op: Op) -> Launch:
        """The bead stopped being runnable before its launch (a blocker, a hold or `v2:parked` appeared)
        and has no session: it goes back to waiting, parked, with nothing to stop or commit."""
        self.d.beads.ensure_label(self.ws.name, op.bead, PARKED)
        self.d.cp(f"{op.kind.value}.shelved!")
        shown = self.d.beads.show(self.ws.name, op.bead)
        final = parked_state(shown)
        self._finish(op, OpStatus.ABANDONED, final, REASONS[final], blocker_detail(shown))
        return Launch.ENDED

    # --- dispatch ---

    def replay(self, op: Op) -> None:
        """Continue any open operation but a pickup (the scheduler owns those)."""
        if op.kind is OpKind.PARK:
            self.replay_park(op)
        elif op.kind is OpKind.RESUME:
            self.replay_resume(op)
        elif op.kind is OpKind.RELEASE:
            self.replay_release(op)
        elif op.kind is OpKind.ESCALATE:
            self.replay_escalate(op)
        else:
            raise ValueError("pickup operations are replayed by the scheduler")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_park.py -q`
Expected: `53 passed`.

- [ ] **Step 7: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 8: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/workstream.py src/heterodyne/wsd/park.py tests/wsd_env.py tests/test_wsd_park.py
git commit -m "feat(wsd): journaled park, resume, release and escalation behind one lock and one launch guard"
```

### Task 7: Pickup ("never idle while an unblocked bead exists")

**Files:**
- Create: `$HZ/src/heterodyne/wsd/sweep.py`, `$HZ/src/heterodyne/wsd/scheduler.py`
- Modify: `$HZ/tests/wsd_env.py` (add the scheduler to the rig)
- Test: `$HZ/tests/test_wsd_pickup.py`, `$HZ/tests/test_wsd_pause.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6; in particular `Parker` (`entry`, `launch`, `resume`, `replay`, `escalate`, `escalate_from`), `Launch`, `resumable`, `parked_state`, `place`, `record`, `Deps`, `ClaimGate`/`Paused`, `BeadsAdapter.ready/ours/read_claim/validate`, `Journal.op_*`, `ws_state`.
- Produces:
  - `heterodyne.wsd.sweep`: `KEEP = {STUCK, HELD}`, frozen `Swept(resumes=0, held=0, stopped=0)`, `sweep(parker) -> Swept` (run under `parker.entry()`; `BeadsUnavailable` and `RuntimeUnavailable` propagate).
- Produces (`heterodyne.wsd.scheduler`):
  - `POINTS = ("lock.waiting", "pickup.intent", "gate.checked", "pickup.claimed!", "pickup.claimed", "pickup.worktree!", "pickup.worktree", "pickup.recorded!", "pickup.launched!", "pickup.done")`, `RESUMABLE_ROWS = {PARKED, WAITING_INPUT}`, `record_from(role, op) -> SessionRecord`.
  - `class TriggerKind(StrEnum)`: `TURN_ENDED, BEAD_CLOSED, BEAD_PARKED, APPROVAL_RESOLVED, BACKSTOP, OPERATOR, STARTUP`; frozen `Trigger(kind, ref=None)` (**the plan 6 seam**: `ref` is the message behind the trigger, carried into progress events and `LaunchSpec.ref`).
  - `class Outcome(StrEnum)`: `STARTED, RESUMED, BUSY, NOTHING, HELD`.
  - `class Scheduler(ws, deps, parker=None)` with `.parker`, `pickup(trigger) -> Outcome` (under `parker.entry()`), `publish()`, `start_new(bead, ref) -> Launch`, `resolve_claim(op) -> Op | None`, `replay(op)`, `replay_pickup(op) -> Launch`.
  - `tests/wsd_env.py`: `Rig.sched`, `Rig.pickup(kind=TriggerKind.BACKSTOP, ref=None) -> Outcome`.

Pickup, under the operation lock: hold if no runtime; read unsettled actions (a hold the guard respects); the sweep; replay every open operation (each launch goes through the guard); report HELD while any hold is left; BUSY while the runtime lists any coder session of the workstream; then resumable parked beads first and, unless paused, ready beads in order, until one starts. A confirmed launch failure moves on to the next candidate; an uncertain one holds.

The sweep (`sweep.py`, recovery step 5 too), for every bead with no open operation:
- a session whose bead is not ours (closed, another worker's, gone) is stopped; an unconfirmed stop is STUCK `stop_unconfirmed` and retried on the next sweep;
- a bead of ours with `needs-human` is STUCK; one with no journal row is escalated `journal_lost`; STUCK and HELD rows stay as they are;
- a routing change escalates `routing_changed`; `v2:held` without `v2:parked`, a parked bead with a listed session, or a state beads and the journal disagree on escalates `unexpected_state`;
- a RUNNING bead with no readable record escalates `launch_unrecorded`; one whose sessions are all gone gets a resume operation at `unlabelled` (RESUMING `session_dead`), which the guard carries out.

The hypothesis property `test_never_idle_while_an_unblocked_bead_exists` checks the "never idle" rule over random queues, races and per-bead faults (including uncertain launches), with an oracle that reads only the fake queue and the fake runtime: never more than one coder session; STARTED, RESUMED and BUSY mean exactly one; HELD means a recorded hold; NOTHING means no ready bead and no coder session.

- [ ] **Step 1: Add the scheduler to the rig in `$HZ/tests/wsd_env.py`**

The whole file after the change:

```python
"""A wsd test rig: a fake queue, a fake runtime, a real temp git repository and a real journal.

`restart()` models wsd dying and starting again: a new Journal on the same file and new wsd objects,
while the queue, the repository and the agent sessions (other processes) carry on.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from fakes.checkpoints import Recorder
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime

from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.park import Parker
from heterodyne.wsd.runtime import ActionReconciler, HoldingReconciler, LaunchSpec
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.states import BeadState
from heterodyne.wsd.workstream import Deps, Limits, WorkstreamSettings, place, record

WS = "alpha"
PROFILES = frozenset({"p-one", "p-two"})


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.org",
                                                   "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@dataclass
class Rig:
    root: Path
    world: World
    runtime: FakeRuntime
    repo: Path
    ws: WorkstreamSettings
    cp: Checkpoint = field(default_factory=Recorder)
    reconciler: ActionReconciler | None = None
    journal: Journal = field(init=False)
    beads: BeadsAdapter = field(init=False)
    gate: ClaimGate = field(init=False)
    deps: Deps = field(init=False)
    parker: Parker = field(init=False)
    sched: Scheduler = field(init=False)

    def __post_init__(self) -> None:
        self.restart(self.cp)

    def restart(self, cp: Checkpoint | None = None) -> None:
        if hasattr(self, "journal"):
            self.journal.close()
        self.cp = cp if cp is not None else Recorder()
        self.journal = Journal(self.root / "state" / "wsd.db")
        self.beads = BeadsAdapter(factory(self.world))
        self.gate = ClaimGate(self.root / "state" / "claims", self.beads, self.cp)
        self.deps = Deps(self.journal, self.beads, self.gate, self.runtime,
                         self.reconciler or HoldingReconciler(self.beads), self.cp)
        self.parker = Parker(self.ws, self.deps)
        self.sched = Scheduler(self.ws, self.deps, self.parker)

    def start(self, bead: str) -> None:
        """What a pickup does for one bead, without the pickup op: claim, worktree, record, launch."""
        self.gate.claim(WS, bead)
        spot = place(self.ws, self.beads.show(WS, bead))
        self.beads.worktree(WS, bead, spot.repo)
        self.beads.ensure_record(WS, bead, record(self.ws, spot))
        self.runtime.launch(LaunchSpec(WS, bead, self.ws.coder_role, spot.profile, spot.session_key,
                                       spot.label, spot.worktree, resume=False))
        for step in (BeadState.CLAIMING, BeadState.STARTING, BeadState.RUNNING):
            self.journal.set_state(WS, bead, step)

    def key(self, bead: str) -> str:
        """The coder session key of a started bead, from its launched-session record."""
        found = self.beads.show(WS, bead).record()
        assert found is not None
        return found.session_key

    def replay_open(self) -> None:
        for op in self.journal.ops_open(WS):
            self.parker.replay(op)

    def pickup(self, kind: TriggerKind = TriggerKind.BACKSTOP, ref: str | None = None) -> Outcome:
        return self.sched.pickup(Trigger(kind, ref))

    def worktree(self, bead: str) -> Path:
        return self.repo.parent / f"{self.repo.name}-btq-{bead}"

    def state(self, bead: str) -> str | None:
        row = self.journal.state(WS, bead)
        return None if row is None else row.state.value


def make_rig(tmp_path: Path, cp: Checkpoint | None = None, limits: Limits | None = None) -> Rig:
    world = World(tmp_path / "btq-state")
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES, limits or Limits())
    return Rig(tmp_path, world, FakeRuntime(), repo, ws, cp if cp is not None else Recorder())
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_pickup.py`**

```python
import threading
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Many, PauseAt, Recorder, SimulatedCrash
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd import ids
from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, BeadsUnavailable
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import POINTS, Outcome
from heterodyne.wsd.states import Reason, WsState
from heterodyne.wsd.workstream import Limits, WorkstreamSettings


def test_clean_pickup_passes_every_point_once(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", title="Do   the\nthing")
    assert rig.pickup(ref="msg-1") is Outcome.STARTED
    assert isinstance(rig.cp, Recorder)
    assert [p for p in rig.cp.seen if p in POINTS] == list(POINTS)
    [spec] = rig.runtime.launches
    assert spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    assert spec.label == "btq-1 · coder · Do the thing"
    assert spec.worktree == rig.worktree("btq-1") and spec.ref == "msg-1" and not spec.resume
    assert rig.state("btq-1") == "running"
    assert rig.journal.snapshot(WS).state is WsState.RUNNING


@pytest.mark.parametrize("point", POINTS)
def test_crash_at_every_pickup_point_replays_to_one_start(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path, cp=CrashAt(point))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.pickup()
    assert rig.world.claims == ["btq-1"]
    assert rig.world.worktrees == ["btq-1"]
    assert len(rig.runtime.launches) == 1
    assert rig.state("btq-1") == "running"
    assert rig.journal.ops_open() == []


def test_uncertain_claim_that_landed_is_used(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_claim_that_did_not_land_holds_and_retries(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("claim", RuntimeError("timeout"))
    assert rig.pickup() is Outcome.HELD                 # never "idle" with btq-1 still ready
    assert rig.journal.snapshot(WS).state is WsState.HELD
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"] and rig.journal.holds(WS) == {}


def test_unreadable_claim_holds_the_workstream(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("timeout"), after=True)
    rig.world.fault("show", RuntimeError("dolt down"))
    assert rig.pickup() is Outcome.HELD
    assert Reason.CLAIM_UNCERTAIN in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]           # nothing else is claimed while it is unknown
    assert rig.pickup() is Outcome.BUSY             # read back as ours on the next trigger, and started
    assert rig.state("btq-1") == "running"
    assert Reason.CLAIM_UNCERTAIN not in rig.journal.holds(WS)
    assert rig.world.claims == ["btq-1"]


def test_lost_race_tries_the_next_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.stolen.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "dropped" and rig.state("btq-2") == "running"


def test_routing_change_is_never_executed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.fault("claim", RuntimeError("Routing/design changed during claim; ask Bel"), after=True)
    assert rig.pickup() is Outcome.STARTED
    assert rig.state("btq-1") == "stuck"
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.world.beads["btq-1"].status == "in_progress"       # never unclaimed
    assert [s.bead for s in rig.runtime.launches] == ["btq-2"]


def test_unknown_repository_is_stuck_not_guessed(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1", metadata={"repo": "elsewhere"})
    assert rig.pickup() is Outcome.NOTHING
    assert rig.journal.state(WS, "btq-1") is not None
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CONFIG_INVALID
    assert rig.runtime.launches == []


def test_confirmed_launch_failure_tries_the_next_candidate(tmp_path: Path) -> None:
    """Finding 16: a launch the runtime confirms never started frees the role, so the next ready bead is
    tried in the same pickup. The failed one keeps its operation and is retried by later pickups until its
    budget runs out."""
    rig = make_rig(tmp_path, limits=Limits(launch_failures_before_human=2))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.failing_beads.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    assert rig.runtime.coders() == ["btq-2"]
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("starting", Reason.LAUNCH_FAILED)
    assert rig.pickup() is Outcome.BUSY             # btq-1's replay waits: btq-2 holds the role
    rig.world.close("btq-2")
    rig.pickup()                                    # second failure: btq-1 escalates
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in rig.world.beads["btq-1"].labels


def test_uncertain_launch_keeps_the_role_and_holds(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.LAUNCH_UNCERTAIN: "btq-1"}
    assert rig.world.claims == ["btq-1"] and rig.runtime.coders() == ["btq-1"]
    assert rig.pickup() is Outcome.HELD             # still listed as unknown: nothing else is tried
    rig.runtime.set(rig.key("btq-1"), Liveness.LIVE)
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


def test_no_runtime_never_spends_launch_budget(tmp_path: Path) -> None:
    """Finding 10: a runtime that went away after the claim is a hold. The pickup waits at its worktree
    step, with no failure counted and no `needs-human`, and launches once the runtime is back."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"), limits=Limits(launch_failures_before_human=1))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.restart()
    rig.runtime.up = False
    for _ in range(3):
        assert rig.pickup() is Outcome.HELD
    [op] = rig.journal.ops_open()
    assert op.attempts == 0 and NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    rig.runtime.up = True
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.journal.holds(WS) == {}


@pytest.mark.parametrize("change", ["routing", "design"])
def test_replayed_pickup_revalidates_before_launch(tmp_path: Path, change: str) -> None:
    """Findings 5 and 6: between the claim and a replayed launch the bead moved to another workstream or
    lost its design approval. The guard's re-run of btq's checks refuses, and a human decides."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    bead = rig.world.beads["btq-1"]
    if change == "routing":
        bead.labels.remove("ws:alpha")
    else:
        bead.metadata["design_approval"] = "approval-revoked"
    rig.restart()
    rig.pickup()
    assert rig.state("btq-1") == "stuck" and NEEDS_HUMAN in bead.labels
    assert rig.runtime.launches == []


def test_replayed_pickup_launches_what_it_chose(tmp_path: Path) -> None:
    """Finding 4: the default profile changed between the worktree step and the replay. The launch is the
    one the pickup journaled, recorded on the bead before it starts."""
    rig = make_rig(tmp_path, cp=CrashAt("pickup.worktree"))
    rig.world.add("btq-1")
    with pytest.raises(SimulatedCrash):
        rig.pickup()
    rig.ws = WorkstreamSettings(WS, rig.ws.repos, "coder", "p-two", rig.ws.profiles, rig.ws.limits)
    rig.restart()
    rig.pickup()
    [spec] = rig.runtime.launches
    assert spec.profile == "p-one" and spec.session_key == ids.role_session("btq-1", "coder", "p-one")
    rec = rig.beads.show(WS, "btq-1").record()
    assert rec is not None and rec.session_key == spec.session_key


def test_crash_between_worktree_and_provenance_escalates(tmp_path: Path) -> None:
    """btq made the worktree but died before writing its provenance note. The path is never trusted or
    reused: the bead is escalated and nothing is launched in it."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.fault("worktree", RuntimeError("killed"), after=True)
    assert rig.pickup() is Outcome.HELD             # the worktree call's outcome is unknown
    assert rig.pickup() is Outcome.NOTHING          # replayed: the path exists without provenance
    assert rig.state("btq-1") == "stuck"
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.WORKTREE_FAILED
    assert rig.runtime.launches == []


def test_unsettled_action_blocks_a_replayed_launch(tmp_path: Path) -> None:
    """Finding 8: a hold stops every launch, replays included. A running bead whose session died is not
    relaunched while an action is unsettled."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.runtime.end(rig.key("btq-1"))
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    assert rig.pickup() is Outcome.HELD
    assert len(rig.runtime.launches) == 1 and rig.runtime.coders() == []
    rig.world.beads["btq-ap"].metadata["action_state"] = "succeeded"
    assert rig.pickup() is Outcome.BUSY
    assert [s.resume for s in rig.runtime.launches] == [False, True]


def test_closed_bead_with_an_unsettled_action_holds(tmp_path: Path) -> None:
    """Finding 9: closing a bead says nothing about its action."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-ap", labels=["kind:approval"], status="closed", metadata={"action_state": "uncertain"})
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}


def test_no_runtime_holds_without_claiming(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.runtime.up = False
    assert rig.pickup() is Outcome.HELD
    assert rig.world.claims == []
    assert rig.journal.snapshot(WS).holds == {Reason.RUNTIME_UNAVAILABLE: ""}


def test_beads_down_holds_and_never_reads_as_idle(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.snapshot(WS).state is WsState.HELD
    rig.world.down = False
    assert rig.pickup() is Outcome.STARTED
    assert rig.journal.holds(WS) == {}


def test_unsettled_action_holds_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "uncertain"})
    assert rig.pickup() is Outcome.HELD
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap"}
    assert rig.world.claims == []


def test_one_coder_session_at_a_time(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    for n in range(3):
        rig.world.add(f"btq-{n}")
    assert rig.pickup() is Outcome.STARTED
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.coders() == ["btq-0"]
    rig.world.close("btq-0")                        # the agent closed it; its session is still listed
    assert rig.pickup() is Outcome.STARTED          # the sweep stopped it first
    assert rig.state("btq-0") == "closed" and rig.runtime.coders() == ["btq-1"]


def test_lost_claim_stops_the_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].assignee = "someone:host:recovery"
    rig.pickup()
    assert rig.state("btq-1") == "stuck"
    assert rig.runtime.coders() == []


def test_dead_session_is_relaunched_as_the_same_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    key = rig.key("btq-1")
    rig.runtime.end(key)
    assert rig.pickup() is Outcome.BUSY             # resumed before any new work
    assert [(s.session_key, s.resume) for s in rig.runtime.launches] == [(key, False), (key, True)]
    assert rig.world.claims == ["btq-1"]


def test_unknown_liveness_never_starts_a_second_session(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.pickup()
    rig.runtime.set(rig.key("btq-1"), Liveness.UNKNOWN)
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"] and len(rig.runtime.launches) == 1


# --- never idle while an unblocked bead exists ---

KINDS = ("ok", "lost_race", "bad_repo", "launch_fails", "launch_uncertain", "uncertain_landed",
         "uncertain_missed", "blocked", "closed_blocker")


def settle(rig: Rig) -> None:
    """What the world does between triggers: an uncertain launch turns out live, and every bead with a
    live session is finished and closed by its agent."""
    for key, session in list(rig.runtime.listed.items()):
        if session.liveness is Liveness.UNKNOWN:
            rig.runtime.set(key, Liveness.LIVE)
    for key in rig.runtime.live():
        rig.world.close(rig.runtime.listed[key].bead)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(st.sampled_from(KINDS), min_size=0, max_size=6))
def test_never_idle_while_an_unblocked_bead_exists(tmp_path_factory: pytest.TempPathFactory,
                                                    kinds: list[str]) -> None:
    """The oracle, per pickup: STARTED, RESUMED and BUSY each mean exactly one coder session is listed;
    HELD means a hold names why; NOTHING means no ready bead is left and no coder runs. Faults are scoped
    to their bead, so one bead's trouble never hides another's."""
    rig = make_rig(tmp_path_factory.mktemp("never-idle"))
    rig.world.add("btq-zz-blocker")
    rig.world.beads["btq-zz-blocker"].labels.remove("agent:wsd")      # someone else's bead
    rig.world.add("btq-zz-done", status="closed")
    for n, kind in enumerate(kinds):
        bead = f"btq-{n}"
        rig.world.add(bead)
        if kind == "bad_repo":
            rig.world.beads[bead].metadata["repo"] = "nope"
        elif kind == "blocked":
            rig.world.beads[bead].deps.append(("btq-zz-blocker", "blocks"))
        elif kind == "closed_blocker":
            rig.world.beads[bead].deps.append(("btq-zz-done", "blocks"))
        elif kind == "lost_race":
            rig.world.stolen.add(bead)
        elif kind == "launch_fails":
            rig.runtime.failing_beads.add(bead)
        elif kind == "launch_uncertain":
            rig.runtime.launch_uncertain += 1
        elif kind == "uncertain_landed":
            rig.world.fault("claim", RuntimeError("timeout"), after=True, bead=bead)
        elif kind == "uncertain_missed":
            rig.world.fault("claim", RuntimeError("timeout"), bead=bead)
    for _ in range(6 * len(kinds) + 6):
        outcome = rig.pickup()
        coders = rig.runtime.coders()
        assert len(coders) <= 1
        if outcome in (Outcome.STARTED, Outcome.RESUMED, Outcome.BUSY):
            assert len(coders) == 1
        elif outcome is Outcome.HELD:
            assert rig.journal.holds(WS)
        else:
            assert outcome is Outcome.NOTHING
            assert rig.world.ready_for(WS) == [] and coders == []
            if not rig.journal.ops_open():
                break
        settle(rig)
    else:
        pytest.fail("pickup never settled")
    assert "btq-zz-blocker" not in rig.world.claims
    for n, kind in enumerate(kinds):
        expected = {"ok": "closed", "closed_blocker": "closed", "launch_uncertain": "closed",
                    "uncertain_landed": "closed", "uncertain_missed": "closed", "lost_race": "dropped",
                    "bad_repo": "stuck", "launch_fails": "stuck", "blocked": None}[kind]
        assert rig.state(f"btq-{n}") == expected, (n, kind)


# --- parked beads in pickup (§5.2: resumable before ready; a parked bead never pre-empts) ---


def parked_rig(tmp_path: Path) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", ("btq-2",))
    return rig


def test_parked_bead_never_preempts_the_running_one(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.STARTED
    rig.world.close("btq-2")                                        # btq-1 is resumable now
    assert rig.pickup() is Outcome.BUSY
    assert PARKED in rig.world.beads["btq-1"].labels
    rig.world.close("btq-3")
    assert rig.pickup() is Outcome.RESUMED
    spec = rig.runtime.launches[-1]
    assert (spec.bead, spec.resume, spec.worktree) == ("btq-1", True, rig.worktree("btq-1"))


def test_resumable_beats_ready(tmp_path: Path) -> None:
    rig = parked_rig(tmp_path)
    rig.world.add("btq-0")                                          # sorts first in ready order
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED
    assert "btq-0" not in rig.world.claims


def test_held_bead_is_skipped_by_pickup(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    assert rig.pickup() is Outcome.NOTHING
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_parked_bead_with_a_listed_session_is_escalated(tmp_path: Path) -> None:
    """Beads say parked, the runtime lists a session of it: never resumed or guessed at."""
    rig = parked_rig(tmp_path)
    rig.runtime.adopt(rig.runtime.launches[0])
    rig.world.add("btq-3")
    assert rig.pickup() is Outcome.BUSY             # the listed session keeps the role
    assert rig.state("btq-1") == "stuck" and rig.world.claims == ["btq-1"]


def test_pickup_replays_an_interrupted_park(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.fault("dep", RuntimeError("dolt down"))
    with pytest.raises(BeadsUnavailable):
        rig.parker.park("btq-1", ("btq-2",))
    assert rig.pickup() is Outcome.NOTHING          # the park finished first; btq-1 waits on btq-2
    assert rig.state("btq-1") == "parked"


def test_unconfirmed_park_stop_keeps_the_coder_role(tmp_path: Path) -> None:
    """A park that can't confirm its session stopped holds the workstream (no budget, no `needs-human`):
    the session keeps the coder role and no new work is claimed until the stop is confirmed."""
    rig = make_rig(tmp_path, limits=Limits(park_attempts_before_human=1))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.runtime.stop_failures = 3
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.add("btq-3")
    for _ in range(2):
        assert rig.pickup() is Outcome.HELD
        assert rig.state("btq-1") == "parking" and rig.runtime.coders() == ["btq-1"]
    assert rig.world.claims == ["btq-1"] and NEEDS_HUMAN not in rig.world.beads["btq-1"].labels
    assert rig.pickup() is Outcome.STARTED          # the third stop is confirmed; the park completes
    assert rig.state("btq-1") == "parked" and rig.world.claims == ["btq-1", "btq-3"]


def test_config_change_never_touches_a_running_bead(tmp_path: Path) -> None:
    """The bead's repo metadata now names a repository wsd doesn't know. The running session was placed
    from its record, so nothing about it changes."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.beads["btq-1"].metadata["repo"] = "gone"
    rig.world.add("btq-2")
    assert rig.pickup() is Outcome.BUSY
    assert rig.state("btq-1") == "running" and rig.world.claims == ["btq-1"]


def test_closed_bead_with_unconfirmed_stop_keeps_the_role(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.world.close("btq-1")
    rig.world.add("btq-2")
    rig.runtime.stop_failures = 1
    assert rig.pickup() is Outcome.BUSY
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and (row.state.value, row.reason) == ("stuck", Reason.STOP_UNCONFIRMED)
    assert rig.pickup() is Outcome.STARTED          # the retried stop is confirmed
    assert rig.state("btq-1") == "closed"
    assert rig.world.claims == ["btq-1", "btq-2"]


class DoorWatch(Recorder):
    """Signals when the thread named `who` reaches the operation lock's door."""

    def __init__(self, who: str) -> None:
        super().__init__()
        self.who = who
        self.reached = threading.Event()

    def __call__(self, name: str) -> None:
        super().__call__(name)
        if name == "lock.waiting" and threading.current_thread().name == self.who:
            self.reached.set()


def test_pickup_waits_at_the_door_while_a_park_runs(tmp_path: Path) -> None:
    """Finding 13: a park has stopped the session (the coder role looks free) but not yet labelled the
    bead. A pickup arriving now waits on the operation lock; it never claims in that window."""
    pause, door = PauseAt("park.stopped!"), DoorWatch("pickup")
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.world.add("btq-3")
    rig.restart(Many(pause, door))
    parker = threading.Thread(target=lambda: rig.parker.park("btq-1", ("btq-2",)), name="park")
    parker.start()
    assert pause.reached.wait(5)
    outcome: list[Outcome] = []
    picker = threading.Thread(target=lambda: outcome.append(rig.pickup()), name="pickup")
    picker.start()
    assert door.reached.wait(5)
    assert outcome == [] and rig.world.claims == ["btq-1"] and rig.runtime.coders() == []
    pause.go.set()
    parker.join(5)
    picker.join(5)
    assert outcome == [Outcome.STARTED]
    assert rig.state("btq-1") == "parked" and rig.world.claims == ["btq-1", "btq-3"]


def test_external_label_removal_is_not_a_release(tmp_path: Path) -> None:
    """Someone removed `v2:held` by hand. The journal still says HELD; nothing resumes until release."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.pickup()
    rig.parker.park("btq-1", (), why="operator /stop", hold=True)
    rig.world.beads["btq-1"].labels.remove(HELD)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.state("btq-1") == "held"
    assert [s.resume for s in rig.runtime.launches] == [False]


def test_uncertain_hold_ends_with_its_operation(tmp_path: Path) -> None:
    """The uncertain launch's bead was closed meanwhile: the replay finds the claim gone and ends the
    operation, and the hold goes with it. The listed session is the sweep's to stop."""
    rig = parked_rig(tmp_path)
    rig.world.close("btq-2")
    rig.runtime.launch_uncertain = 1
    assert rig.pickup() is Outcome.HELD           # the resume's launch is uncertain
    rig.world.close("btq-1")
    for op in rig.journal.ops_open():
        rig.parker.replay(op)
    assert rig.journal.ops_open() == [] and rig.journal.holds(WS) == {}
    row = rig.journal.state(WS, "btq-1")
    assert row is not None and row.reason is Reason.CLAIM_LOST
    assert rig.pickup() is Outcome.NOTHING
    assert rig.runtime.coders() == [] and rig.state("btq-1") == "closed"
```

- [ ] **Step 3: Create `$HZ/tests/test_wsd_pause.py`**

These force the §4.3 interleavings with `PauseAt`: a direct `btq pause` after listing, and a `wsctl`-style pause racing an in-flight claim. The racing pause is shown to have reached its lock attempt (`Seen('gate.pause.waiting')`) before the test checks it is not yet acknowledged.

```python
"""Pause at the pickup level (ADR 0001 §4.3): it stops new claims only, and an acknowledged pause is
never followed by a claim. Interleavings are forced with checkpoints."""

import threading
from pathlib import Path

from fakes.checkpoints import Many, PauseAt, Seen
from wsd_env import WS, make_rig

from heterodyne.wsd.scheduler import Outcome


def test_pause_stops_claims(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == []
    rig.gate.resume(WS)
    assert rig.pickup() is Outcome.STARTED
    assert rig.world.claims == ["btq-1"]


def test_direct_btq_pause_after_listing_is_honoured(tmp_path: Path) -> None:
    cp = PauseAt("pickup.intent")           # ready() has listed btq-1; the claim has not started
    rig = make_rig(tmp_path, cp=cp)
    rig.world.add("btq-1")
    result: list[Outcome] = []
    worker = threading.Thread(target=lambda: result.append(rig.pickup()))
    worker.start()
    assert cp.reached.wait(5)
    rig.beads.set_paused(WS, True)
    cp.go.set()
    worker.join(5)
    assert result == [Outcome.NOTHING]
    assert rig.world.claims == []
    assert rig.state("btq-1") == "dropped"


def test_pause_waits_for_in_flight_claim(tmp_path: Path) -> None:
    """The claim has passed the flag check when the operator pauses. The pause is not acknowledged until
    that claim is done, and after the acknowledgement no claim starts."""
    cp = PauseAt("gate.checked")
    door = Seen("gate.pause.waiting")
    rig = make_rig(tmp_path, cp=Many(cp, door))
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    outcome: list[Outcome] = []
    picker = threading.Thread(target=lambda: outcome.append(rig.pickup()))
    picker.start()
    assert cp.reached.wait(5)
    acked = threading.Event()
    pauser = threading.Thread(target=lambda: (rig.gate.pause(WS), acked.set()))
    pauser.start()
    assert door.reached.wait(5)             # the pauser is at the claim lock's door, which the claim holds
    assert not acked.is_set()
    cp.go.set()
    picker.join(5)
    pauser.join(5)
    assert acked.is_set()
    assert outcome == [Outcome.STARTED]
    assert rig.world.claims == ["btq-1"]
    rig.world.close("btq-1")
    assert rig.pickup() is Outcome.NOTHING
    assert rig.world.claims == ["btq-1"]


def test_pause_does_not_stop_the_running_bead(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    assert rig.pickup() is Outcome.STARTED
    rig.gate.pause(WS)
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.stops == []


def test_pause_does_not_stop_a_resume(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.gate.pause(WS)
    rig.world.close("btq-2")
    assert rig.pickup() is Outcome.RESUMED          # pausing stops new claims only
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_pickup.py tests/test_wsd_pause.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.scheduler'`.

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/sweep.py`**

The sweep reconciles beads, sessions and the journal. Pickup runs it on every call and recovery runs it as its step 5.

```python
"""Reconcile beads, sessions and the journal (ADR 0001 §4.3, §10): recovery's step 5, and the first
thing every pickup does. The sweep never launches; it stops what must not run, records what beads say,
and hands a dead recorded session to the launch guard as a resume operation.

Only beads with no open operation are swept: an open operation's replay owns its bead.

- A listed session whose bead is not ours (closed, claimed by another worker, gone) is stopped. If the
  stop can't be confirmed the bead is STUCK with STOP_UNCONFIRMED and the next sweep tries again; the
  session keeps the coder role until it is gone.
- A bead of ours is checked against its row: `needs-human` is STUCK; no row (a lost journal) is held as
  JOURNAL_LOST; STUCK and HELD rows stay so until the operator's release; a status, label or row that
  doesn't fit is escalated as UNEXPECTED_STATE; btq's post-claim checks must still pass.
- A RUNNING bead with no session and a readable record gets a resume operation at `unlabelled` (it was
  never parked). With no readable record, or with a session under another key, it is escalated.
"""

from collections import defaultdict
from dataclasses import dataclass

from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, Bead, NotOurs, RecordUnreadable, RoutingChanged
from heterodyne.wsd.journal import OpKind
from heterodyne.wsd.park import REASONS, Parker, blocker_detail, parked_state
from heterodyne.wsd.runtime import RuntimeUnavailable, Session
from heterodyne.wsd.states import TERMINAL, WAITING, BeadState, Reason

KEEP = frozenset({BeadState.STUCK, BeadState.HELD})     # only the operator's release moves these on


@dataclass(frozen=True)
class Swept:
    resumes: int = 0         # dead sessions handed to the guard as resume operations
    held: int = 0            # beads escalated because beads and the journal disagree
    stopped: int = 0         # sessions stopped because their bead is not ours


def sweep(parker: Parker) -> Swept:
    """Run under `parker.entry()`. BeadsUnavailable and RuntimeUnavailable propagate: the caller holds."""
    return _Sweep(parker).run()


class _Sweep:
    def __init__(self, parker: Parker) -> None:
        self.p = parker
        self.d = parker.d
        self.j = parker.d.journal
        self.name = parker.ws.name
        self.held = 0
        self.stopped = 0
        self.resumes = 0

    def run(self) -> Swept:
        d, j, name = self.d, self.j, self.name
        ours = {b.id: b for b in d.beads.ours(name)}
        by_bead: dict[str, list[Session]] = defaultdict(list)
        for session in d.runtime.sessions(name):
            by_bead[session.bead].append(session)
        for bead, sessions in sorted(by_bead.items()):
            if bead not in ours and j.op_for(name, bead) is None:
                self._not_ours(bead, sessions)
        for row in j.states(name):
            if (row.state not in TERMINAL and row.state not in KEEP and row.bead not in ours
                    and row.bead not in by_bead and j.op_for(name, row.bead) is None):
                self._not_ours(row.bead, [])
        for bead in sorted(ours.values(), key=lambda b: b.id):
            if j.op_for(name, bead.id) is None:
                self._ours(bead, by_bead.get(bead.id, []))
        return Swept(self.resumes, self.held, self.stopped)

    def _not_ours(self, bead: str, sessions: list[Session]) -> None:
        """Nothing runs without our claim: stop the bead's sessions, then record what beads say."""
        confirmed = True
        for session in sessions:
            try:
                self.d.runtime.stop(session.key)
                self.stopped += 1
            except RuntimeUnavailable:
                confirmed = False
        if not confirmed:
            self.j.adopt(self.name, bead, BeadState.STUCK, Reason.STOP_UNCONFIRMED)
        elif not self.d.beads.exists(self.name, bead):
            self.j.adopt(self.name, bead, BeadState.DROPPED, Reason.CLAIM_LOST, "the bead no longer exists")
        elif self.d.beads.show(self.name, bead).status == "closed":
            self.j.adopt(self.name, bead, BeadState.CLOSED)
        elif self.j.state(self.name, bead) is not None:
            self.j.adopt(self.name, bead, BeadState.STUCK, Reason.CLAIM_LOST)

    def _hold(self, bead: Bead, reason: Reason, detail: str = "") -> None:
        self.held += 1
        self.p.escalate(bead.id, reason, detail)

    def _ours(self, bead: Bead, sessions: list[Session]) -> None:
        j, name = self.j, self.name
        row = j.state(name, bead.id)
        if NEEDS_HUMAN in bead.labels:
            if row is None or row.state is not BeadState.STUCK:
                j.adopt(name, bead.id, BeadState.STUCK, Reason.NEEDS_HUMAN)
            return
        if row is None:
            self._hold(bead, Reason.JOURNAL_LOST, "claimed by this workstream, with no journal record")
            return
        if row.state in KEEP:
            return
        if bead.status != "in_progress":
            self._hold(bead, Reason.UNEXPECTED_STATE, f"claimed with status {bead.status}")
            return
        try:
            self.d.beads.validate(name, bead.id)
        except RoutingChanged:
            self._hold(bead, Reason.ROUTING_CHANGED)
            return
        except NotOurs:
            j.adopt(name, bead.id, BeadState.STUCK, Reason.CLAIM_LOST)
            return
        if HELD in bead.labels and PARKED not in bead.labels:
            self._hold(bead, Reason.UNEXPECTED_STATE, "v2:held without v2:parked")
        elif PARKED in bead.labels:
            if sessions:
                self._hold(bead, Reason.UNEXPECTED_STATE, "parked, but a session is listed")
            elif row.state in WAITING:
                state = parked_state(bead)
                if (row.state, row.detail) != (state, blocker_detail(bead)):
                    j.adopt(name, bead.id, state, REASONS[state], blocker_detail(bead))
            else:
                self._hold(bead, Reason.UNEXPECTED_STATE, f"parked, but the journal says {row.state.value}")
        elif row.state is not BeadState.RUNNING:
            self._hold(bead, Reason.UNEXPECTED_STATE, f"running, but the journal says {row.state.value}")
        else:
            self._running(bead, sessions)

    def _running(self, bead: Bead, sessions: list[Session]) -> None:
        """A recorded running bead: its session runs on, or it gets a resume operation for the guard."""
        try:
            rec = bead.record()
        except RecordUnreadable:
            rec = None
        if rec is None:
            self._hold(bead, Reason.LAUNCH_UNRECORDED)
        elif any(s.key != rec.session_key for s in sessions):
            self._hold(bead, Reason.UNEXPECTED_STATE, "a session not in its record is listed")
        elif not sessions:
            with self.j.transaction():
                op = self.j.op_open(OpKind.RESUME, self.name, bead.id, {"ref": ""})
                self.j.op_step(op.op_id, "unlabelled")      # never parked: nothing to unlabel
                self.j.set_state(self.name, bead.id, BeadState.RESUMING, Reason.SESSION_DEAD)
            self.resumes += 1
```

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/scheduler.py`**

```python
"""Deterministic pickup (ADR 0001 §5.2) for one workstream, and the pickup journal (§4.3).

On every trigger (a turn ends, a bead closes or parks, an approval resolves, the 60s backstop), under
the workstream's operation lock (`Parker.entry`):
1. the runtime must be available, or the workstream holds (no failure budget, no `needs-human`);
2. unsettled actions are found (closed beads included); any hold the launch guard respects;
3. the sweep (`sweep.py`) reconciles beads, sessions and the journal: sessions of beads no longer ours
   are stopped, and a running bead whose session is gone gets a resume operation;
4. every open operation is replayed: park, release and escalation finish; pickup and resume reach the
   launch guard, which refuses while any hold applies;
5. any hold left: pickup reports HELD;
6. the coder role is taken if the runtime lists any coder session of the workstream, live or unknown;
7. otherwise resumable parked beads first, then new ready work, trying each candidate in turn until one
   starts. A refused claim, a lost claim or a confirmed launch failure moves on to the next candidate
   (the "never idle while an unblocked bead exists" rule); an uncertain launch keeps the role and holds.

New work is journaled: intent, claim (inside the claim gate), worktree, then the launch guard, which
writes the launched-session record before launching. A claim with an uncertain outcome is read back
before anything else; while it can't be read back, the workstream is held.
"""

from dataclasses import dataclass
from enum import StrEnum

from heterodyne.wsd.beads import (
    Bead,
    BeadsUnavailable,
    ClaimRefused,
    ClaimUncertain,
    ClaimView,
    NotOurs,
    RoutingChanged,
    SessionRecord,
    WorktreeConflict,
)
from heterodyne.wsd.gate import Paused
from heterodyne.wsd.journal import Op, OpKind, OpStatus
from heterodyne.wsd.park import Launch, Parker, resumable
from heterodyne.wsd.runtime import RuntimeUnavailable
from heterodyne.wsd.states import BeadState, Reason, ws_state
from heterodyne.wsd.sweep import sweep
from heterodyne.wsd.workstream import ConfigInvalid, Deps, WorkstreamSettings, place, record

POINTS = ("lock.waiting", "pickup.intent", "gate.checked", "pickup.claimed!", "pickup.claimed",
          "pickup.worktree!", "pickup.worktree", "pickup.recorded!", "pickup.launched!", "pickup.done")
RESUMABLE_ROWS = frozenset({BeadState.PARKED, BeadState.WAITING_INPUT})


class TriggerKind(StrEnum):
    TURN_ENDED = "turn_ended"
    BEAD_CLOSED = "bead_closed"
    BEAD_PARKED = "bead_parked"
    APPROVAL_RESOLVED = "approval_resolved"
    BACKSTOP = "backstop"
    OPERATOR = "operator"
    STARTUP = "startup"


@dataclass(frozen=True)
class Trigger:
    kind: TriggerKind
    ref: str | None = None      # the message or event behind the trigger, for progress reactions


class Outcome(StrEnum):
    STARTED = "started"          # new work claimed and launched
    RESUMED = "resumed"          # a parked bead resumed
    BUSY = "busy"                # the coder role already has a session
    NOTHING = "nothing"          # nothing is ready and nothing is resumable
    HELD = "held"                # pickup is held (see the workstream's holds)


class Scheduler:
    def __init__(self, ws: WorkstreamSettings, deps: Deps, parker: Parker | None = None) -> None:
        self.ws = ws
        self.d = deps
        self.parker = parker or Parker(ws, deps)

    # --- entry point ---

    def pickup(self, trigger: Trigger) -> Outcome:
        with self.parker.entry():
            try:
                outcome = self._pickup(trigger)
            except BeadsUnavailable as exc:
                self.d.journal.hold(self.ws.name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
                outcome = Outcome.HELD
            self.publish()
            return outcome

    def publish(self) -> None:
        j, name = self.d.journal, self.ws.name
        try:
            paused = self.d.beads.paused(name)
        except BeadsUnavailable:
            paused = True        # can't read the flag: show the workstream as stopped, never as running
        j.set_ws_state(name, ws_state(paused, j.holds(name), (b.state for b in j.states(name))))

    def _pickup(self, trigger: Trigger) -> Outcome:
        j, name = self.d.journal, self.ws.name
        if not self.d.runtime.available():
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        j.unhold(name, Reason.RUNTIME_UNAVAILABLE)
        unresolved = self.d.reconciler.unresolved(name)
        j.unhold(name, Reason.BEADS_UNREACHABLE)      # beads answered
        if unresolved:
            j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
        else:
            j.unhold(name, Reason.ACTIONS_UNRECONCILED)
        try:
            sweep(self.parker)
            for op in j.ops_open(name):
                self.replay(op)
            if j.holds(name):
                return Outcome.HELD
            coder = [s for s in self.d.runtime.sessions(name) if s.role == self.ws.coder_role]
        except RuntimeUnavailable:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE)
            return Outcome.HELD
        if coder:
            return Outcome.BUSY
        for bead in self._resumable():
            result = self.parker.resume(bead, trigger.ref)
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.RESUMED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
        if self.d.beads.paused(name):
            return Outcome.NOTHING       # pausing stops new claims only (§4.3)
        for bead in self.d.beads.ready(name):
            if j.op_for(name, bead.id) is not None:
                continue
            try:
                result = self.start_new(bead, trigger.ref)
            except Paused:
                return Outcome.NOTHING
            if result in (Launch.STARTED, Launch.LIVE):
                return Outcome.STARTED
            if result in (Launch.WAIT, Launch.UNCERTAIN):
                return self._stalled()
        return Outcome.NOTHING

    def _stalled(self) -> Outcome:
        return Outcome.HELD if self.d.journal.holds(self.ws.name) else Outcome.BUSY

    def _resumable(self) -> list[Bead]:
        """Parked beads of ours that are resumable now and that the journal has as waiting on blockers
        or input. A bead the journal has as HELD or STUCK is never resumed here, whatever its labels say:
        only the operator's release moves it on."""
        j, name = self.d.journal, self.ws.name
        rows = {r.bead: r.state for r in j.states(name)}
        return sorted((b for b in self.d.beads.ours(name)
                       if rows.get(b.id) in RESUMABLE_ROWS and resumable(b) and j.op_for(name, b.id) is None),
                      key=lambda b: b.id)

    # --- new work ---

    def start_new(self, bead: Bead, ref: str | None) -> Launch:
        j, name = self.d.journal, self.ws.name
        with j.transaction():
            op = j.op_open(OpKind.PICKUP, name, bead.id, {"ref": ref or ""})
            j.set_state(name, bead.id, BeadState.CLAIMING, ref=ref)
        self.d.cp("pickup.intent")
        try:
            self.d.gate.claim(name, bead.id)
        except Paused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            raise
        except ClaimRefused:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            return Launch.ENDED
        except RoutingChanged:
            # btq claimed it but routing or the design gate changed: the claim stays (wsd never
            # unclaims), the bead is never executed, and a human decides.
            self.parker.escalate_from(op, Reason.ROUTING_CHANGED)
            return Launch.ENDED
        except ClaimUncertain:
            result = self.replay_pickup(op)
            row = j.state(name, bead.id)
            if row is not None and (row.state, row.reason) == (BeadState.DROPPED, Reason.CLAIM_ABANDONED):
                # The claim failed without landing: the queue is failing, not the bead. Hold pickup and let
                # the next trigger try again, rather than calling the workstream idle with work ready.
                raise BeadsUnavailable("claim did not land") from None
            return result
        self.d.cp("pickup.claimed!")
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return self._start(op)

    def _finish(self, op: Op, status: OpStatus, state: BeadState, reason: Reason | None = None,
                detail: str = "") -> None:
        with self.d.journal.transaction():
            self.d.journal.op_finish(op.op_id, status)
            self.d.journal.set_state(self.ws.name, op.bead, state, reason, detail, op.data.get("ref") or None)

    def resolve_claim(self, op: Op) -> Op | None:
        """A pickup journal at `intent`: read the claim back. Returns the operation at `claimed` if the
        claim is ours, or None if it was finished (not ours) or can't be read (held)."""
        j, name = self.d.journal, self.ws.name
        try:
            view = self.d.beads.read_claim(name, op.bead)
        except BeadsUnavailable as exc:
            j.hold(name, Reason.CLAIM_UNCERTAIN, op.bead)
            j.set_state(name, op.bead, BeadState.CLAIMING, Reason.CLAIM_UNCERTAIN, type(exc).__name__)
            return None
        j.unhold(name, Reason.CLAIM_UNCERTAIN)
        if view is ClaimView.FREE:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_ABANDONED)
            return None
        if view is ClaimView.OTHER:
            self._finish(op, OpStatus.ABANDONED, BeadState.DROPPED, Reason.CLAIM_LOST)
            return None
        op = j.op_step(op.op_id, "claimed")
        self.d.cp("pickup.claimed")
        return op

    def replay(self, op: Op) -> None:
        """Continue an open journal from the step it reached."""
        if op.kind is OpKind.PICKUP:
            self.replay_pickup(op)
        else:
            self.parker.replay(op)

    def replay_pickup(self, op: Op) -> Launch:
        if op.step == "intent":
            resolved = self.resolve_claim(op)
            if resolved is None:
                held = Reason.CLAIM_UNCERTAIN in self.d.journal.holds(self.ws.name)
                return Launch.WAIT if held else Launch.ENDED
            op = resolved
        return self._start(op)

    def _start(self, op: Op) -> Launch:
        j, name = self.d.journal, self.ws.name
        if op.step == "claimed":
            try:
                spot = place(self.ws, self.d.beads.show(name, op.bead))
                j.set_state(name, op.bead, BeadState.STARTING)
                path = self.d.beads.worktree(name, op.bead, spot.repo)
            except NotOurs:
                self._finish(op, OpStatus.ABANDONED, BeadState.STUCK, Reason.CLAIM_LOST)
                return Launch.ENDED
            except ConfigInvalid as exc:
                self.parker.escalate_from(op, Reason.CONFIG_INVALID, str(exc))
                return Launch.ENDED
            except WorktreeConflict as exc:
                self.parker.escalate_from(op, Reason.WORKTREE_FAILED, str(exc))
                return Launch.ENDED
            self.d.cp("pickup.worktree!")
            rec = record(self.ws, spot)
            op = j.op_step(op.op_id, "worktree", {"worktree": str(path), "repo": rec.repo,
                                                  "profile": rec.profile, "session_key": rec.session_key})
            self.d.cp("pickup.worktree")
        rec = record_from(self.ws.coder_role, op)
        return self.parker.launch(op, rec)


def record_from(role: str, op: Op) -> SessionRecord:
    """The record a pickup decided on at its worktree step, from its journal: a replay launches what the
    pickup chose, even if the configuration changed since."""
    data = op.data
    return SessionRecord(role, data["profile"], data["session_key"], data["repo"], data["worktree"])
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_pickup.py tests/test_wsd_pause.py tests/test_wsd_park.py -q`
Expected: `103 passed` (50 new, plus Task 6's 53 still passing with the extended rig).

- [ ] **Step 8: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 9: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/sweep.py src/heterodyne/wsd/scheduler.py tests/wsd_env.py tests/test_wsd_pickup.py tests/test_wsd_pause.py
git commit -m "feat(wsd): journaled pickup that never idles while unblocked work exists"
```

### Task 8: Startup recovery

**Files:**
- Create: `$HZ/src/heterodyne/wsd/recovery.py`
- Test: `$HZ/tests/test_wsd_recovery.py`

**Interfaces:**
- Consumes: `Scheduler` (`.d`, `.ws`, `.parker`, `.resolve_claim`, `.publish`) (Task 7); `sweep`, `Swept` (Task 7); `Parker.entry`, `Parker.replay` (Task 6); `BeadsAdapter.ours` (Task 4); `AgentRuntime.sessions`, `RuntimeUnavailable` (Task 5); `Journal.ops_open/hold/unhold` (Task 3).
- Produces (`heterodyne.wsd.recovery`): `RECOVERY_POINTS = ("lock.waiting", "recovery.read", "recovery.actions", "recovery.journals", "recovery.sessions")`; frozen `Recovered(ws, ok, replayed=0, resumes=0, held=0, stopped=0)`; `recover(sched: Scheduler) -> Recovered`.

The order is the ADR's (§3.3): journal integrity (done by `Journal()`); read beads (by assignee) and the runtime's sessions; reconcile actions (a hold); replay every open park, release and escalation, and read back any claim left at `intent`; the sweep (reconcile sessions). The caller accepts events only afterwards (Task 9). **Recovery never launches**: a pickup past its claim, a resume, and the resume operations the sweep opens for dead sessions are all left to the next pickup's launch guard, which sees the whole session list first. `BeadsUnavailable` or `RuntimeUnavailable` anywhere makes the recovery fail (`ok=False`) with the matching hold; it is retried before the next pickup. The tests crash at every recovery point and recover again, and cover a lost journal (deleted between runs): nothing is relaunched, parked or unparked until the operator's release.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_recovery.py`**

```python
from pathlib import Path

import pytest
from fakes.checkpoints import CrashAt, Recorder, SimulatedCrash
from wsd_env import WS, Rig, make_rig

from heterodyne.wsd.beads import HELD, NEEDS_HUMAN, PARKED, RECORD_KEY
from heterodyne.wsd.journal import OpKind
from heterodyne.wsd.recovery import RECOVERY_POINTS, recover
from heterodyne.wsd.runtime import Liveness
from heterodyne.wsd.scheduler import Outcome
from heterodyne.wsd.states import Reason


def lose_journal(rig: Rig) -> None:
    rig.journal.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{rig.root / 'state' / 'wsd.db'}{suffix}").unlink(missing_ok=True)
    rig.restart()
    assert rig.journal.fresh


def started(tmp_path: Path, *extra: str) -> Rig:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    for bead in extra:
        rig.world.add(bead)
    assert rig.pickup() is Outcome.STARTED
    return rig


def row_of(rig: Rig, bead: str = "btq-1") -> tuple[str | None, Reason | None]:
    row = rig.journal.state(WS, bead)
    return (None, None) if row is None else (row.state.value, row.reason)


def test_recovery_runs_in_adr_order(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    assert recover(rig.sched).ok
    assert isinstance(rig.cp, Recorder)
    assert rig.cp.seen == list(RECOVERY_POINTS)


@pytest.mark.parametrize("point", RECOVERY_POINTS)
def test_crash_during_recovery_is_safe_to_repeat(tmp_path: Path, point: str) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(CrashAt("park.blocked"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    rig.restart(CrashAt(point))
    with pytest.raises(SimulatedCrash):
        recover(rig.sched)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.state("btq-1") == "parked" and rig.journal.ops_open() == []
    assert len(rig.runtime.launches) == 1


def test_recovery_never_launches(tmp_path: Path) -> None:
    """Findings 2 and 3: a running bead whose session ended gets a resume operation; the launch is the
    guard's, at the next pickup, after every listed session has been seen."""
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.resumes == 1
    assert len(rig.runtime.launches) == 1
    [op] = rig.journal.ops_open()
    assert (op.kind, op.step) == (OpKind.RESUME, "unlabelled")
    assert row_of(rig) == ("resuming", Reason.SESSION_DEAD)
    assert rig.pickup() is Outcome.BUSY
    assert [s.resume for s in rig.runtime.launches] == [False, True]


def test_unknown_session_is_reserved_before_any_launch(tmp_path: Path) -> None:
    """Finding 2: the runtime lists the session, but can't say it is live. It keeps the coder role: no
    relaunch of its bead and no new claim."""
    rig = started(tmp_path, "btq-2")
    rig.runtime.set(rig.key("btq-1"), Liveness.UNKNOWN)
    rig.restart()
    assert recover(rig.sched).ok
    assert rig.pickup() is Outcome.BUSY
    assert len(rig.runtime.launches) == 1 and rig.world.claims == ["btq-1"]


def test_two_dead_sessions_never_run_together(tmp_path: Path) -> None:
    """Two running beads of ours with no session (possible only after manual intervention): both get
    resume operations, and the guard lets exactly one through."""
    rig = started(tmp_path, "btq-2")
    rig.start("btq-2")                              # the intervention: a second coder, by hand
    for bead in ("btq-1", "btq-2"):
        rig.runtime.end(rig.key(bead))
    rig.restart()
    assert recover(rig.sched).resumes == 2
    assert rig.pickup() is Outcome.BUSY
    assert rig.runtime.coders() == ["btq-1"]
    assert [op.bead for op in rig.journal.ops_open()] == ["btq-2"]       # waiting on the role


def test_lost_journal_holds_a_running_bead(tmp_path: Path) -> None:
    """Finding 11: no journal row for a bead we hold. Its session keeps the role; nothing is relaunched,
    nothing new is claimed, and a human looks."""
    rig = started(tmp_path, "btq-2")
    lose_journal(rig)
    result = recover(rig.sched)
    assert result.ok and result.held == 1
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert NEEDS_HUMAN in rig.world.beads["btq-1"].labels
    assert rig.pickup() is Outcome.BUSY
    assert rig.world.claims == ["btq-1"]


def test_lost_journal_never_relaunches_a_dead_session(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_lost_journal_never_finishes_a_cut_short_park(tmp_path: Path) -> None:
    """r1 completed the park from beads alone; r2 holds instead. The edge is on, `v2:parked` is not, and
    nothing says whether the WIP commit happened."""
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.restart(CrashAt("park.blocked"))
    with pytest.raises(SimulatedCrash):
        rig.parker.park("btq-1", ("btq-2",))
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert PARKED not in rig.world.beads["btq-1"].labels
    assert len(rig.runtime.launches) == 1


def test_lost_journal_parked_bead_resumes_only_after_release(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.add("btq-2")
    rig.world.beads["btq-2"].labels.remove("agent:wsd")
    rig.pickup()
    rig.parker.park("btq-1", ("btq-2",))
    rig.world.close("btq-2")
    lose_journal(rig)
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.JOURNAL_LOST)
    assert rig.pickup() is Outcome.NOTHING
    assert rig.parker.release("btq-1").value == "parked"
    assert rig.pickup() is Outcome.RESUMED


def test_ownership_is_found_after_a_label_change(tmp_path: Path) -> None:
    """Finding 12: someone moved the claimed bead off this workstream. Recovery finds it by assignee
    and escalates it; its session keeps the role."""
    rig = started(tmp_path, "btq-2")
    rig.world.beads["btq-1"].labels.remove("ws:alpha")
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.ROUTING_CHANGED)
    assert rig.pickup() is Outcome.BUSY and rig.world.claims == ["btq-1"]


def test_needs_human_bead_is_stuck_not_relaunched(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.append(NEEDS_HUMAN)
    rig.runtime.end(rig.key("btq-1"))
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.NEEDS_HUMAN)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_running_bead_without_a_record_is_held(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.end(rig.key("btq-1"))
    del rig.world.beads["btq-1"].metadata[RECORD_KEY]
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.LAUNCH_UNRECORDED)
    rig.pickup()
    assert len(rig.runtime.launches) == 1


def test_held_without_parked_is_unexpected(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].labels.append(HELD)
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.UNEXPECTED_STATE)


def test_session_of_a_bead_not_ours_is_stopped(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.world.beads["btq-1"].assignee = "someone:host:recovery"
    rig.restart()
    result = recover(rig.sched)
    assert result.ok and result.stopped == 1
    assert row_of(rig) == ("stuck", Reason.CLAIM_LOST) and rig.runtime.coders() == []


def test_unconfirmed_stop_of_a_closed_bead_is_stuck_until_confirmed(tmp_path: Path) -> None:
    rig = started(tmp_path, "btq-2")
    rig.world.close("btq-1")
    rig.runtime.stop_failures = 1
    rig.restart()
    assert recover(rig.sched).ok
    assert row_of(rig) == ("stuck", Reason.STOP_UNCONFIRMED)
    assert rig.runtime.coders() == ["btq-1"]
    assert rig.pickup() is Outcome.STARTED            # the sweep's retried stop is confirmed
    assert rig.state("btq-1") == "closed" and rig.runtime.coders() == ["btq-2"]


def test_beads_down_at_startup_holds_and_claims_nothing(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-1")
    rig.world.down = True
    result = recover(rig.sched)
    assert not result.ok
    assert Reason.BEADS_UNREACHABLE in rig.journal.holds(WS)
    assert rig.world.claims == []


def test_runtime_that_cannot_list_holds_recovery(tmp_path: Path) -> None:
    rig = started(tmp_path)
    rig.runtime.list_failures = 1
    rig.restart()
    result = recover(rig.sched)
    assert not result.ok
    assert Reason.RUNTIME_UNAVAILABLE in rig.journal.holds(WS)
    assert NEEDS_HUMAN not in rig.world.beads["btq-1"].labels


def test_unsettled_actions_hold_after_recovery(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    rig.world.add("btq-ap", labels=["kind:approval"], metadata={"action_state": "executing"})
    rig.world.add("btq-aq", labels=["kind:approval"], status="closed", metadata={"action_state": "bogus"})
    assert recover(rig.sched).ok
    assert rig.journal.holds(WS) == {Reason.ACTIONS_UNRECONCILED: "btq-ap,btq-aq"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_recovery.py -q`
Expected: FAIL: `ModuleNotFoundError: No module named 'heterodyne.wsd.recovery'`.

- [ ] **Step 3: Create `$HZ/src/heterodyne/wsd/recovery.py`**

```python
"""Startup recovery for one workstream, in the ADR's order (ADR 0001 §3.3, §4.3, §10). Recovery never
launches anything: it stops what must not run, rebuilds the journal from beads and the runtime, and
leaves every launch to pickup's launch guard.

1. journal integrity check: done when the Journal is opened (a corrupt journal never gets this far);
2. read beads and sessions: every bead held by one of this workstream's per-bead workers, found by
   assignee whatever its labels say, and every session the runtime may still be running;
3. reconcile actions: any action not settled (closed beads included) holds pickup until plan 5 checks
   its target;
4. resume park journals: replay every open park, release and escalation, and read back any claim a
   pickup left uncertain. Pickup and resume operations that would launch are left to pickup;
5. reconcile sessions (`sweep`, which every pickup also runs): a session whose bead is not ours
   (closed, claimed by another worker, gone) is stopped; a recorded running bead whose session is gone
   gets a resume operation that pickup will carry through the guard; anything beads and the journal
   disagree on is held as STUCK, never relaunched;
6. only then does the caller accept events.

Doubt always holds: a bead the journal has no row for (a lost journal), a missing or unreadable session
record, `v2:held` without `v2:parked`, a parked bead with a session, a routing change, or a stop the
runtime can't confirm. Rows that are STUCK or HELD stay so until the operator's release.
"""

from dataclasses import dataclass

from heterodyne.wsd.beads import BeadsUnavailable
from heterodyne.wsd.journal import OpKind
from heterodyne.wsd.runtime import RuntimeUnavailable
from heterodyne.wsd.scheduler import Scheduler
from heterodyne.wsd.states import Reason
from heterodyne.wsd.sweep import sweep

RECOVERY_POINTS = ("lock.waiting", "recovery.read", "recovery.actions", "recovery.journals",
                   "recovery.sessions")


@dataclass(frozen=True)
class Recovered:
    ws: str
    ok: bool                 # False: the workstream stays held until a later recovery succeeds
    replayed: int = 0        # open journals replayed
    resumes: int = 0         # dead sessions handed to pickup as resume operations
    held: int = 0            # beads recovery escalated because beads and the journal disagree
    stopped: int = 0         # sessions stopped because their bead is not ours


def recover(sched: Scheduler) -> Recovered:
    j, name = sched.d.journal, sched.ws.name
    with sched.parker.entry():
        try:
            result = _Recovery(sched).run()
        except BeadsUnavailable as exc:
            j.hold(name, Reason.BEADS_UNREACHABLE, type(exc).__name__)
            result = Recovered(name, ok=False)
        except RuntimeUnavailable as exc:
            j.hold(name, Reason.RUNTIME_UNAVAILABLE, type(exc).__name__)
            result = Recovered(name, ok=False)
        else:
            j.unhold(name, Reason.BEADS_UNREACHABLE)
        sched.publish()
        return result


class _Recovery:
    def __init__(self, sched: Scheduler) -> None:
        self.s = sched
        self.d = sched.d
        self.j = sched.d.journal
        self.name = sched.ws.name

    def run(self) -> Recovered:
        d, j, name = self.d, self.j, self.name
        # 2. read beads and sessions
        d.beads.ours(name)
        d.runtime.sessions(name)
        d.cp("recovery.read")
        # 3. actions
        unresolved = d.reconciler.unresolved(name)
        if unresolved:
            j.hold(name, Reason.ACTIONS_UNRECONCILED, ",".join(sorted(unresolved)))
        else:
            j.unhold(name, Reason.ACTIONS_UNRECONCILED)
        d.cp("recovery.actions")
        # 4. journals that never launch
        replayed = 0
        for op in j.ops_open(name):
            if op.kind is OpKind.PICKUP and op.step == "intent":
                self.s.resolve_claim(op)
            elif op.kind in (OpKind.PARK, OpKind.RELEASE, OpKind.ESCALATE):
                self.s.parker.replay(op)
            else:
                continue            # a pickup past its claim, or a resume: pickup's guard carries it on
            replayed += 1
        d.cp("recovery.journals")
        # 5. sessions, read again: the replays changed both
        swept = sweep(self.s.parker)
        d.cp("recovery.sessions")
        return Recovered(name, ok=True, replayed=replayed, resumes=swept.resumes, held=swept.held,
                         stopped=swept.stopped)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_recovery.py -q`
Expected: `22 passed`.

- [ ] **Step 5: Lint and type-check**

Run: `cd $HZ && uv run ruff check && uv run pyright`
Expected: `All checks passed!` and `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/recovery.py tests/test_wsd_recovery.py
git commit -m "feat(wsd): startup recovery in the ADR's order; never launches, fails closed"
```

### Task 9: Settings, control socket, daemon, CLIs and docs

**Files:**
- Create: `$HZ/src/heterodyne/wsd/settings.py`, `$HZ/src/heterodyne/wsd/ctl.py`, `$HZ/src/heterodyne/wsd/daemon.py`, `$HZ/src/heterodyne/wsd/cli.py`, `$HZ/docs/wsd.md`
- Modify: `$HZ/src/heterodyne/defaults/defaults.toml`, `$HZ/pyproject.toml`, `$HZ/docs/configuration.md`, `$HZ/docs/install.md`, `$HZ/examples/config.toml`
- Test: `$HZ/tests/test_wsd_settings.py`, `$HZ/tests/test_wsd_daemon.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8; from the existing config package `heterodyne.config.load`, `paths`, `secret_scan.is_reference`, `secret_scan.show`, `ConfigError`, `config.layers.table_at`.
- Produces:
  - `heterodyne.wsd.settings`: frozen `WsdSettings(state_dir, backstop_seconds, reconcile_seconds, inbox_attempts_before_human, btq_checkout, btq_locations: dict[str, str], workstreams: tuple[WorkstreamSettings, ...])` with properties `journal`, `instance_lock`, `lock_dir`, `socket`; `workstream_names(config_dir) -> list[str]`; `resolve(env) -> WsdSettings`.
  - `heterodyne.wsd.ctl`: `CtlRequest(op: "tick"|"status"|"pause"|"resume", job: "pickup"|"reconcile"|None = None, ws: str|None = None)`, `CtlReply(result: "ok"|"refused"|"failed", message, data: dict[str, dict[str, str]] = {})`, `CtlUnavailable`, `CtlServer(path, handler)` (`start()`, `close()`), `request(path, req, timeout=600) -> CtlReply`.
  - `heterodyne.wsd.daemon`: frozen `Parts(journal, beads, gate, schedulers: dict[str, Scheduler])`; `assemble(s, journal, factory, runtime, reconciler=HoldingReconciler, cp=nothing) -> Parts` (**plans 4 and 5 pass their runtime and reconciler here**); `class Wsd(s, parts)` with `recovered: set[str]`, `recover_one`, `pickup_one`, `reconcile_one`, `startup`, `set_pause(name, paused) -> CtlReply`, `status(only)`, `handle(req)` (async), `serve(stop)` (async).
  - `heterodyne.wsd.cli`: `EX_CONFIG = 78`, `run(s, factory, runtime) -> int`, `wsd_main(argv=None) -> int`, `wsctl_main(argv=None) -> int`; console scripts `wsd` and `wsctl`.

`wsd run` order: instance lock → open (and so integrity-check) the journal → recover every workstream → a startup pickup each → only then the control socket and the timers. `wsctl pause` goes through wsd, which sets the flag under the claim lock (§4.3), and publishes the workstream's new state before it acknowledges. A failed recovery publishes its hold at once, so `wsctl status` never shows a stale state.

- [ ] **Step 1: Create `$HZ/tests/test_wsd_settings.py`**

```python
from pathlib import Path

import pytest

from heterodyne.config import ConfigError
from heterodyne.wsd.settings import resolve


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", f"""
[profiles.a]
adapter = "codex"
model = "m1"
[profiles.b]
adapter = "claude-code"
model = "m2"
[integrations.beads]
btq = "{tmp_path}/btq"
dolt_port = 3307
credentials = {{ file = "{tmp_path}/btq-credentials.json" }}
""")
    write(tmp_path, "workstreams/alpha.toml", f"""
[roles]
coder = "b"
[repos]
default = "{tmp_path}/repos/proj"
docs = "~/docs"
""")
    return tmp_path


def env(d: Path) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d)}


def test_resolve_defaults_and_workstreams(cfg: Path) -> None:
    s = resolve(env(cfg))
    assert (s.backstop_seconds, s.reconcile_seconds, s.inbox_attempts_before_human) == (60.0, 300.0, 3)
    assert s.state_dir == cfg / ".local" / "state" / "heterodyne" / "wsd"
    assert s.btq_locations == {"dolt_port": "3307", "credentials": str(cfg / "btq-credentials.json")}
    [ws] = s.workstreams
    assert (ws.name, ws.coder_role, ws.coder_profile) == ("alpha", "coder", "b")
    assert ws.repos == {"default": cfg / "repos" / "proj", "docs": cfg / "docs"}
    assert (ws.limits.launch_failures_before_human, ws.limits.park_attempts_before_human) == (2, 3)


def test_wsd_table_is_host_only(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[wsd]\nbackstop_seconds = 1\n')
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        resolve(env(cfg))


def test_inline_credentials_are_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(
        f'credentials = {{ file = "{cfg}/btq-credentials.json" }}', 'credentials = "inline"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError):
        resolve(env(cfg))


def test_workstream_needs_default_repo(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\nother = "/x"\n')
    with pytest.raises(ConfigError, match="default"):
        resolve(env(cfg))


def test_relative_repo_is_refused(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\ncoder = "b"\n[repos]\ndefault = "proj"\n')
    with pytest.raises(ConfigError, match="absolute"):
        resolve(env(cfg))


def test_workstream_file_name_must_be_a_slug(cfg: Path) -> None:
    write(cfg, "workstreams/Bad Name.toml", "")
    with pytest.raises(ConfigError, match="slug"):
        resolve(env(cfg))


def test_coder_profile_must_exist(cfg: Path) -> None:
    write(cfg, "workstreams/alpha.toml", '[roles]\nreview = "a"\n[repos]\ndefault = "/x"\n')
    with pytest.raises(ConfigError, match="roles.coder"):
        resolve(env(cfg))


def test_unknown_wsd_key_is_refused(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text() + "[wsd]\nbackstop = 5\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        resolve(env(cfg))


def test_unknown_beads_integration_key_is_refused(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace("dolt_port = 3307", 'dolt_port = 3307\nrelay = "x"')
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match=r"\[integrations.beads\]: unknown keys"):
        resolve(env(cfg))


def test_btq_checkout_is_required(cfg: Path) -> None:
    text = (cfg / "config.toml").read_text().replace(f'btq = "{cfg}/btq"\n', "")
    write(cfg, "config.toml", text)
    with pytest.raises(ConfigError, match="btq must be a path"):
        resolve(env(cfg))


def test_integrations_marmot_is_left_alone(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text()
          + '[integrations.marmot]\nsocket = "/run/x.sock"\n')
    assert resolve(env(cfg)).btq_checkout == cfg / "btq"
```

- [ ] **Step 2: Create `$HZ/tests/test_wsd_daemon.py`**

`test_startup_recovers_before_accepting_events` asserts the control socket does not exist while recovery runs.

```python
import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest
from fakes.fake_btq import World, factory
from fakes.fake_runtime import FakeRuntime
from wsd_env import PROFILES, WS, git_repo

from heterodyne.wsd import cli, ctl
from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.gate import instance_lock
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.states import Reason, WsState
from heterodyne.wsd.workstream import WorkstreamSettings


def settings(tmp_path: Path) -> WsdSettings:
    repo = git_repo(tmp_path / "repos" / "proj")
    ws = WorkstreamSettings(WS, {"default": repo}, "coder", "p-one", PROFILES)
    return WsdSettings(tmp_path / "state" / "wsd", 0.05, 3600, 3, tmp_path / "btq", {}, (ws,))


def test_startup_recovers_before_accepting_events(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    world.add("btq-1")
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(world), runtime))
    seen_socket_during_recovery: list[bool] = []
    original = daemon.recover_one

    def spy(name: str):  # noqa: ANN202
        seen_socket_during_recovery.append(s.socket.exists())
        return original(name)

    daemon.recover_one = spy  # type: ignore[method-assign]

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        for _ in range(100):
            if s.socket.exists():
                break
            await asyncio.sleep(0.02)
        reply = await ctl.request(s.socket, ctl.CtlRequest("status"))
        stop.set()
        await task
        return reply

    reply = asyncio.run(scenario())
    assert seen_socket_during_recovery == [False]
    assert reply.data[WS]["state"] == "running" and reply.data[WS]["beads"] == "btq-1=running"
    assert not s.socket.exists()
    assert world.claims == ["btq-1"]


def test_tick_and_backstop(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world, runtime = World(tmp_path / "btq-state"), FakeRuntime()
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), runtime))

    async def scenario() -> tuple[ctl.CtlReply, ctl.CtlReply]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        idle = await ctl.request(s.socket, ctl.CtlRequest("tick", job="pickup"))
        world.add("btq-1")
        for _ in range(100):                      # the 0.05 s backstop picks it up
            if world.claims:
                break
            await asyncio.sleep(0.02)
        bad = await ctl.request(s.socket, ctl.CtlRequest("tick", job="reconcile", ws="nope"))
        stop.set()
        await task
        return idle, bad

    idle, bad = asyncio.run(scenario())
    assert idle.data == {WS: {"outcome": "nothing"}}
    assert world.claims == ["btq-1"]
    assert bad.result == "refused"


def test_failed_recovery_is_retried_before_pickup(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.add("btq-1")
    world.down = True
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    assert daemon.startup() == {WS: daemon.pickup_one(WS, _trigger())}
    assert world.claims == [] and WS not in daemon.recovered
    world.down = False
    daemon.pickup_one(WS, _trigger())
    assert WS in daemon.recovered and world.claims == ["btq-1"]


def _trigger():  # noqa: ANN202
    from heterodyne.wsd.scheduler import Trigger, TriggerKind
    return Trigger(TriggerKind.BACKSTOP)


def test_corrupt_journal_refuses_to_start(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    s.state_dir.mkdir(parents=True, mode=0o700)
    s.journal.write_bytes(b"garbage" * 200)
    world = World(tmp_path / "btq-state")
    assert cli.run(s, factory(world), FakeRuntime()) == cli.EX_CONFIG
    assert s.journal.read_bytes() == b"garbage" * 200
    assert "left in place" in capsys.readouterr().err


def test_second_instance_refuses(tmp_path: Path) -> None:
    s = settings(tmp_path)
    fd = instance_lock(s.instance_lock)
    try:
        assert cli.run(s, factory(World(tmp_path / "btq-state")), FakeRuntime()) == 1
    finally:
        os.close(fd)


def test_wsctl_pause_goes_through_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    s = replace(settings(tmp_path), backstop_seconds=3600)    # only explicit ticks pick up
    world = World(tmp_path / "btq-state")
    monkeypatch.setattr(cli, "_settings", lambda: s)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))

    async def scenario() -> list[int]:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        codes = [await asyncio.to_thread(cli.wsctl_main, ["pause", WS])]
        world.add("btq-1")
        codes.append(await asyncio.to_thread(cli.wsd_main, ["tick", "pickup"]))
        codes.append(len(world.claims))
        codes.append(await asyncio.to_thread(cli.wsctl_main, ["resume", WS]))  # resume runs a pickup
        stop.set()
        await task
        return codes

    assert asyncio.run(scenario()) == [0, 0, 0, 0]
    assert world.claims == ["btq-1"]
    out = capsys.readouterr().out
    assert f"{WS}: paused." in out and f"{WS}: resumed." in out and "outcome=started" in out


def test_wsctl_pause_needs_a_running_wsd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    s = settings(tmp_path)
    monkeypatch.setattr(cli, "_settings", lambda: s)
    assert cli.wsctl_main(["pause", WS]) == 1
    assert "not running" in capsys.readouterr().err
    assert not (tmp_path / "btq-state").exists()        # nothing was set behind wsd's back


def test_pause_flag_failure_is_not_acknowledged(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(world), FakeRuntime()))
    world.down = True
    reply = asyncio.run(daemon.handle(ctl.CtlRequest("pause", ws=WS)))
    assert reply.result == "failed" and "nothing is acknowledged" in reply.message


def test_pause_without_a_workstream_is_refused(tmp_path: Path) -> None:
    s = settings(tmp_path)
    daemon = Wsd(s, assemble(s, Journal(s.journal), factory(World(tmp_path / "w")), FakeRuntime()))

    async def scenario() -> ctl.CtlReply:
        stop = asyncio.Event()
        task = asyncio.create_task(daemon.serve(stop))
        while not s.socket.exists():
            await asyncio.sleep(0.02)
        reply = await ctl.request(s.socket, ctl.CtlRequest("pause"))
        stop.set()
        await task
        return reply

    assert asyncio.run(scenario()).result == "refused"


def test_acknowledged_pause_shows_at_once(tmp_path: Path) -> None:
    s = settings(tmp_path)
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(World(tmp_path / "btq-state")), FakeRuntime()))
    daemon.startup()
    assert journal.snapshot(WS).state is WsState.IDLE
    reply = asyncio.run(daemon.handle(ctl.CtlRequest("pause", ws=WS)))
    assert reply.result == "ok"
    assert journal.snapshot(WS).state is WsState.PAUSED       # no pickup ran in between


def test_failed_recovery_publishes_its_hold(tmp_path: Path) -> None:
    s = settings(tmp_path)
    world = World(tmp_path / "btq-state")
    world.down = True
    journal = Journal(s.journal)
    daemon = Wsd(s, assemble(s, journal, factory(world), FakeRuntime()))
    daemon.recover_one(WS)
    snap = journal.snapshot(WS)
    assert snap.state is WsState.HELD and Reason.BEADS_UNREACHABLE in snap.holds
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd $HZ && uv run pytest tests/test_wsd_settings.py tests/test_wsd_daemon.py -q`
Expected: FAIL: `ModuleNotFoundError` for `heterodyne.wsd.settings` (and `heterodyne.wsd.daemon`).

- [ ] **Step 4: Add the `[wsd]` defaults to `$HZ/src/heterodyne/defaults/defaults.toml`**

Append at the end of the file:

```toml

# The workstream daemon (§4.3, §9). Host only; [integrations.beads] says where btq and the queue are.
[wsd]
backstop_seconds = 60
reconcile_seconds = 300
launch_failures_before_human = 2
park_attempts_before_human = 3
inbox_attempts_before_human = 3
coder_role = "coder"
```

The workstream layer already rejects any top-level table outside `roles`, `repos`, `sandbox`, `cron`, `render`, `timeouts` and `restrict`, so `[wsd]` and `[integrations]` are host-only without further code.

- [ ] **Step 5: Create `$HZ/src/heterodyne/wsd/settings.py`**

```python
"""wsd settings from the merged host config and each workstream's layer (ADR 0001 §4.3, §9, §15).

`[wsd]` and `[integrations.beads]` live in host `config.toml` only (the workstream layer rejects both).
`integrations.beads.btq` is the btq checkout; its other keys are btq's locations, passed to its `Queue`
unchanged, and anything not set falls back to btq's own `BTQ_*` environment and defaults. A workstream is
a file `workstreams/<name>.toml` whose stem is a slug; its `[repos]` names its repositories (absolute
paths, one of them `default`) and `roles.coder` its coder profile.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heterodyne.config import Config, ConfigError, load, paths, secret_scan
from heterodyne.config.layers import table_at
from heterodyne.config.secret_scan import show
from heterodyne.wsd import ids
from heterodyne.wsd.workstream import DEFAULT_REPO, Limits, WorkstreamSettings

WSD_KEYS = frozenset({"backstop_seconds", "reconcile_seconds", "launch_failures_before_human",
                      "park_attempts_before_human", "inbox_attempts_before_human", "coder_role"})
BTQ_KEYS = frozenset({"btq", "config_dir", "repo", "dolt_host", "dolt_port", "dolt_database",
                      "tls_cert", "credentials"})
BTQ_PATHS = ("config_dir", "repo", "tls_cert")
BTQ_TEXT = ("dolt_host", "dolt_port", "dolt_database")
BEADS = "[integrations.beads]"
JOURNAL = "wsd.db"
INSTANCE_LOCK = "wsd.lock"
CTL_SOCKET = "ctl.sock"


@dataclass(frozen=True)
class WsdSettings:
    state_dir: Path
    backstop_seconds: float
    reconcile_seconds: float
    inbox_attempts_before_human: int
    btq_checkout: Path
    btq_locations: dict[str, str]
    workstreams: tuple[WorkstreamSettings, ...]

    @property
    def journal(self) -> Path:
        return self.state_dir / JOURNAL

    @property
    def instance_lock(self) -> Path:
        return self.state_dir / INSTANCE_LOCK

    @property
    def lock_dir(self) -> Path:
        return self.state_dir / "claims"

    @property
    def socket(self) -> Path:
        return self.state_dir / CTL_SOCKET


def workstream_names(config_dir: Path) -> list[str]:
    folder = config_dir / "workstreams"
    if not folder.is_dir():
        return []
    names = sorted(p.stem for p in folder.glob("*.toml"))
    for name in names:
        if not ids.SLUG.fullmatch(name):
            raise ConfigError(f"workstreams/{show(name, False)}.toml: the file name must be a slug")
    return names


def resolve(env: Mapping[str, str]) -> WsdSettings:
    host = load(env=env)
    wsd = table_at(host.values, "wsd", "config")
    _only(wsd, WSD_KEYS, "[wsd]")
    btq = table_at(table_at(host.values, "integrations", "config"), "beads", "integrations")
    _only(btq, BTQ_KEYS, BEADS)
    limits = Limits(launch_failures_before_human=_int(wsd, "launch_failures_before_human"),
                    park_attempts_before_human=_int(wsd, "park_attempts_before_human"))
    coder_role = wsd.get("coder_role")
    if not isinstance(coder_role, str) or not ids.SLUG.fullmatch(coder_role):
        raise ConfigError("[wsd] coder_role must be a role name")
    streams = tuple(_workstream(name, load(name, env), coder_role, limits, env)
                    for name in workstream_names(paths.config_dir(env)))
    return WsdSettings(
        state_dir=paths.state_dir(env) / "wsd",
        backstop_seconds=_seconds(wsd, "backstop_seconds"),
        reconcile_seconds=_seconds(wsd, "reconcile_seconds"),
        inbox_attempts_before_human=_int(wsd, "inbox_attempts_before_human"),
        btq_checkout=_path(btq.get("btq"), env, f"{BEADS} btq"),
        btq_locations=_locations(btq, env),
        workstreams=streams)


def _workstream(name: str, cfg: Config, coder_role: str, limits: Limits,
                env: Mapping[str, str]) -> WorkstreamSettings:
    where = f"workstreams/{name}.toml"
    repos: dict[str, Path] = {}
    for repo, value in table_at(cfg.values, "repos", where).items():
        if not ids.SLUG.fullmatch(repo):
            raise ConfigError(f"{where}: repos.{show(repo, False)} must be a slug")
        repos[repo] = _path(value, env, f"{where}: repos.{repo}")
    if DEFAULT_REPO not in repos:
        raise ConfigError(f"{where}: [repos] must name a `{DEFAULT_REPO}` repository")
    profiles = frozenset(table_at(cfg.values, "profiles", "config.toml"))
    coder_profile = table_at(cfg.values, "roles", where).get(coder_role)
    if not isinstance(coder_profile, str) or coder_profile not in profiles:
        raise ConfigError(f"{where}: roles.{coder_role} must name a profile from [profiles]")
    return WorkstreamSettings(name, repos, coder_role, coder_profile, profiles, limits)


def _locations(btq: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in BTQ_PATHS:
        if key in btq:
            found[key] = str(_path(btq[key], env, f"{BEADS} {key}"))
    for key in BTQ_TEXT:
        if key in btq:
            value = btq[key]
            if isinstance(value, int) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{BEADS} {key} must be a non-empty string")
            found[key] = value
    if "credentials" in btq:
        ref: Any = btq["credentials"]
        if not secret_scan.is_reference(ref) or set(cast(Mapping[str, Any], ref)) != {"file"}:
            raise ConfigError(f'{BEADS} credentials must be {{ file = "<path>" }}')
        where = f"{BEADS} credentials.file"
        found["credentials"] = str(_path(cast(Mapping[str, Any], ref)["file"], env, where))
    return found


def _only(table: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {show(unknown)} (allowed: {sorted(allowed)})")


def _path(value: Any, env: Mapping[str, str], where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a path")
    path = paths.expand(value, env)
    if not path.is_absolute():
        raise ConfigError(f"{where} must be an absolute path or start with ~/")
    return path


def _int(table: Mapping[str, Any], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ConfigError(f"[wsd] {key} must be an integer from 1 to 100")
    return value


def _seconds(table: Mapping[str, Any], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 86400:
        raise ConfigError(f"[wsd] {key} must be a number of seconds, more than 0 and at most 86400")
    return float(value)
```

- [ ] **Step 6: Create `$HZ/src/heterodyne/wsd/ctl.py`**

```python
"""wsd's host control socket (ADR 0001 §4.3, §9): `wsd tick <job>` from the timers, and `wsctl pause`,
`resume` and `status`.

One JSON request per connection, one JSON reply. The socket is 0600 inside the 0700 wsd state directory;
anything able to use it can already act as wsd.
"""

import asyncio
import contextlib
import os
import socket
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import msgspec

from heterodyne.fsutil import private_dir

MAX_REQUEST = 4096
READ_SECONDS = 5.0


def _no_data() -> dict[str, dict[str, str]]:
    return {}


class CtlRequest(msgspec.Struct, frozen=True):
    op: Literal["tick", "status", "pause", "resume"]
    job: Literal["pickup", "reconcile"] | None = None      # tick only
    ws: str | None = None       # None: every workstream (tick and status); pause and resume need one


class CtlReply(msgspec.Struct, frozen=True):
    result: Literal["ok", "refused", "failed"]
    message: str
    data: dict[str, dict[str, str]] = msgspec.field(default_factory=_no_data)


class CtlUnavailable(Exception):
    """No wsd is listening (or it did not answer)."""


Handler = Callable[[CtlRequest], Awaitable[CtlReply]]


class CtlServer:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self.handler = handler
        self.server: asyncio.Server | None = None
        self.created: tuple[int, int] | None = None

    async def start(self) -> None:
        """Bind the socket ourselves, so a symlink at the path is refused rather than followed. Only a
        stale real socket is replaced. The caller holds the instance lock, so no live wsd owns it."""
        private_dir(self.path.parent)
        with contextlib.suppress(FileNotFoundError):
            if not stat.S_ISSOCK(os.lstat(self.path).st_mode):
                raise FileExistsError("the control socket path exists and is not a socket")
            self.path.unlink()
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(self.path))
            self.path.chmod(0o600)
            made = os.lstat(self.path)
            self.created = (made.st_dev, made.st_ino)
            sock.listen()
            sock.setblocking(False)
            self.server = await asyncio.start_unix_server(self._handle, sock=sock, limit=MAX_REQUEST + 2)
        except BaseException:
            sock.close()
            raise

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        with contextlib.suppress(OSError):
            current = os.lstat(self.path)
            if (current.st_dev, current.st_ino) == self.created:
                self.path.unlink()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_SECONDS)
                if len(line.rstrip(b"\n")) > MAX_REQUEST:
                    raise ValueError("request too long")
                req = msgspec.json.decode(line, type=CtlRequest)
            except (TimeoutError, ValueError, msgspec.DecodeError):
                reply = CtlReply("refused", "malformed request")
            else:
                if (req.op == "tick") != (req.job is not None):
                    reply = CtlReply("refused", "tick needs a job; nothing else takes one")
                elif req.op in ("pause", "resume") and req.ws is None:
                    reply = CtlReply("refused", f"{req.op} needs a workstream")
                else:
                    try:
                        reply = await self.handler(req)
                    except Exception as exc:  # noqa: BLE001 - fixed wording; the type is enough
                        reply = CtlReply("failed", f"wsd hit an internal error ({type(exc).__name__})")
            writer.write(msgspec.json.encode(reply) + b"\n")
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 - one bad client must not end the server
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def request(path: Path, req: CtlRequest, timeout: float = 600.0) -> CtlReply:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 5)
    except (OSError, TimeoutError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    try:
        writer.write(msgspec.json.encode(req) + b"\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        return msgspec.json.decode(line, type=CtlReply)
    except (OSError, TimeoutError, ValueError, msgspec.DecodeError) as exc:
        raise CtlUnavailable(type(exc).__name__) from None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
```

- [ ] **Step 7: Create `$HZ/src/heterodyne/wsd/daemon.py`**

```python
"""The wsd daemon (ADR 0001 §3.3, §5.2, §9): startup recovery, then pickup on triggers and timers.

Startup order is fixed: the instance lock; the journal, integrity-checked (corrupt: refuse to start);
recovery of every workstream (§3.3 steps 2 to 5); a startup pickup; and only then the control socket,
which is how events (timer ticks, and from plan 6 Marmot) reach wsd. A workstream whose recovery failed
stays held, and every later tick retries its recovery before any pickup: pickup never runs on state
recovery could not confirm.

Pickup and recovery are blocking (btq runs bd as a subprocess), so they run in worker threads; each
workstream's operation lock (`Parker.entry`, also taken by park and release) keeps them one at a time.
"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from heterodyne.wsd.beads import BeadsAdapter, BeadsUnavailable
from heterodyne.wsd.btq import QueueFactory
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.ctl import CtlReply, CtlRequest, CtlServer
from heterodyne.wsd.gate import ClaimGate
from heterodyne.wsd.journal import Journal
from heterodyne.wsd.recovery import Recovered, recover
from heterodyne.wsd.runtime import ActionReconciler, AgentRuntime, HoldingReconciler
from heterodyne.wsd.scheduler import Outcome, Scheduler, Trigger, TriggerKind
from heterodyne.wsd.settings import WsdSettings
from heterodyne.wsd.workstream import Deps


@dataclass(frozen=True)
class Parts:
    journal: Journal
    beads: BeadsAdapter
    gate: ClaimGate
    schedulers: dict[str, Scheduler]


def assemble(s: WsdSettings, journal: Journal, factory: QueueFactory, runtime: AgentRuntime,
             reconciler: Callable[[BeadsAdapter], ActionReconciler] = HoldingReconciler,
             cp: Checkpoint = nothing) -> Parts:
    beads = BeadsAdapter(factory)
    gate = ClaimGate(s.lock_dir, beads, cp)
    deps = Deps(journal, beads, gate, runtime, reconciler(beads), cp)
    return Parts(journal, beads, gate, {w.name: Scheduler(w, deps) for w in s.workstreams})


class Wsd:
    def __init__(self, s: WsdSettings, parts: Parts) -> None:
        self.s = s
        self.parts = parts
        self.recovered: set[str] = set()

    # --- blocking work (worker threads) ---

    def recover_one(self, name: str) -> Recovered:
        result = recover(self.parts.schedulers[name])
        if result.ok:
            self.recovered.add(name)
        else:
            self.recovered.discard(name)
        return result

    def pickup_one(self, name: str, trigger: Trigger) -> Outcome:
        """Pickup, after a recovery if this workstream's last one did not succeed."""
        if name not in self.recovered and not self.recover_one(name).ok:
            return Outcome.HELD
        return self.parts.schedulers[name].pickup(trigger)

    def reconcile_one(self, name: str) -> Outcome:
        """The 5-minute reconcile (§9): rebuild state from beads and the runtime, then pick up."""
        if not self.recover_one(name).ok:
            return Outcome.HELD
        return self.parts.schedulers[name].pickup(Trigger(TriggerKind.BACKSTOP))

    def startup(self) -> dict[str, Outcome]:
        """Recover every workstream, then run one startup pickup each (§3.3 step 6 comes after this)."""
        for name in self.parts.schedulers:
            self.recover_one(name)
        return {name: self.pickup_one(name, Trigger(TriggerKind.STARTUP)) for name in self.parts.schedulers}

    def set_pause(self, name: str, paused: bool) -> CtlReply:
        """§4.3: the flag is set under the claim lock, so once this returns no claim can start. A flag that
        can't be confirmed is reported as a failure: nothing is acknowledged."""
        try:
            if paused:
                self.parts.gate.pause(name)
            else:
                self.parts.gate.resume(name)
        except BeadsUnavailable as exc:
            return CtlReply("failed", f"the pause flag could not be confirmed ({exc}); "
                                      "nothing is acknowledged")
        self.parts.journal.emit(name, None, "paused" if paused else "resumed")
        if paused:
            sched = self.parts.schedulers[name]
            with sched.parker.entry():
                sched.publish()        # the acknowledged pause shows at once, not at the next pickup
            return CtlReply("ok", f"{name}: paused. No new claims start; a running bead finishes and parked "
                                  "beads may resume.")
        outcome = self.pickup_one(name, Trigger(TriggerKind.OPERATOR))
        return CtlReply("ok", f"{name}: resumed.", {name: {"outcome": outcome.value}})

    def status(self, only: str | None) -> dict[str, dict[str, str]]:
        found: dict[str, dict[str, str]] = {}
        for name in self.parts.schedulers:
            if only is not None and name != only:
                continue
            snap = self.parts.journal.snapshot(name)
            found[name] = {
                "state": snap.state.value if snap.state else "unknown",
                "recovered": "yes" if name in self.recovered else "no",
                "holds": ",".join(r.value for r in snap.holds),
                "beads": ",".join(f"{b.bead}={b.state.value}" + (f"({b.reason.value})" if b.reason else "")
                                  for b in snap.beads),
                "open_ops": ",".join(f"{o.bead}:{o.kind.value}@{o.step}" for o in snap.ops),
                "last_event": str(snap.last_event),
            }
        return found

    # --- the event loop ---

    def _names(self, only: str | None) -> list[str]:
        return [n for n in self.parts.schedulers if only is None or n == only]

    async def handle(self, req: CtlRequest) -> CtlReply:
        if req.ws is not None and req.ws not in self.parts.schedulers:
            return CtlReply("refused", "no such workstream")
        if req.op == "status":
            return CtlReply("ok", "status", await asyncio.to_thread(self.status, req.ws))
        if req.op in ("pause", "resume") and req.ws is not None:
            return await asyncio.to_thread(self.set_pause, req.ws, req.op == "pause")
        results: dict[str, dict[str, str]] = {}
        for name in self._names(req.ws):
            if req.job == "reconcile":
                outcome = await asyncio.to_thread(self.reconcile_one, name)
            else:
                outcome = await asyncio.to_thread(self.pickup_one, name, Trigger(TriggerKind.OPERATOR))
            results[name] = {"outcome": outcome.value}
        return CtlReply("ok", f"{req.job} done", results)

    def _guarded(self, job: Callable[[str], object], name: str) -> None:
        """A timer job that fails unexpectedly is recorded, and the workstream is recovered again before
        its next pickup: the timer keeps running, and nothing is assumed about what the failure left."""
        try:
            job(name)
        except Exception as exc:  # noqa: BLE001 - recorded by type; the next tick re-recovers
            self.recovered.discard(name)
            self.parts.journal.emit(name, None, "tick_failed", type(exc).__name__)

    async def _every(self, seconds: float, job: Callable[[str], object]) -> None:
        while True:
            await asyncio.sleep(seconds)
            for name in self.parts.schedulers:
                await asyncio.to_thread(self._guarded, job, name)

    async def serve(self, stop: asyncio.Event) -> None:
        await asyncio.to_thread(self.startup)
        server = CtlServer(self.s.socket, self.handle)
        await server.start()
        timers = [asyncio.create_task(self._every(
                      self.s.backstop_seconds, lambda n: self.pickup_one(n, Trigger(TriggerKind.BACKSTOP)))),
                  asyncio.create_task(self._every(self.s.reconcile_seconds, self.reconcile_one))]
        try:
            await stop.wait()
        finally:
            for task in timers:
                task.cancel()
            for task in timers:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await server.close()
```

- [ ] **Step 8: Create `$HZ/src/heterodyne/wsd/cli.py`**

```python
"""`wsd` (run, tick) and `wsctl` (pause, resume, status) command lines (ADR 0001 §4.3, §9, §16).

Every command except `wsd run` asks the running wsd over its control socket. `wsctl pause` goes through
wsd as §4.3 requires: wsd sets btq's shared pause flag under the workstream's claim lock, so a pause is
acknowledged only once no claim can start. With wsd stopped nothing claims, and nothing is acknowledged.
"""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Sequence

from heterodyne.config import ConfigError
from heterodyne.wsd import btq, ctl
from heterodyne.wsd.daemon import Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.journal import Journal, JournalCorrupt
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings, resolve

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it


def _settings() -> WsdSettings:
    return resolve(os.environ)


def _factory(s: WsdSettings) -> btq.QueueFactory:
    return btq.factory(btq.load(s.btq_checkout), s.btq_locations)


def run(s: WsdSettings, factory: btq.QueueFactory, runtime: AgentRuntime) -> int:
    try:
        lock = instance_lock(s.instance_lock)
    except AlreadyRunning:
        print("wsd: another wsd is already running on this state directory", file=sys.stderr)
        return 1
    try:
        try:
            journal = Journal(s.journal)
        except JournalCorrupt as exc:
            print(f"wsd: the journal failed its check ({exc}); it was left in place for inspection. "
                  "Move it aside to start from beads alone.", file=sys.stderr)
            return EX_CONFIG
        if journal.fresh:
            journal.emit("-", None, "journal_created")
        daemon = Wsd(s, assemble(s, journal, factory, runtime))
        asyncio.run(_serve(daemon))
        return 0
    finally:
        os.close(lock)


async def _serve(daemon: Wsd) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await daemon.serve(stop)


def _ask(s: WsdSettings, req: ctl.CtlRequest) -> ctl.CtlReply | None:
    try:
        return asyncio.run(ctl.request(s.socket, req))
    except ctl.CtlUnavailable:
        print("wsd is not running (no answer on its control socket)", file=sys.stderr)
        return None


def _print(reply: ctl.CtlReply) -> int:
    print(reply.message)
    for name, fields in sorted(reply.data.items()):
        print(name + ": " + " ".join(f"{k}={v}" for k, v in fields.items() if v))
    return 0 if reply.result == "ok" else 1


def wsd_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the workstream daemon")
    tick = sub.add_parser("tick", help="ask the running wsd to run a job now (timers call this)")
    tick.add_argument("job", choices=["pickup", "reconcile"])
    tick.add_argument("--ws", default=None)
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "run":
            return run(s, _factory(s), NoRuntime())
    except ConfigError as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    except btq.BtqUnavailable as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    reply = _ask(s, ctl.CtlRequest("tick", job=args.job, ws=args.ws))
    return 1 if reply is None else _print(reply)


def wsctl_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsctl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} new claims for a workstream").add_argument("ws")
    sub.add_parser("status", help="show what wsd is doing").add_argument("ws", nargs="?")
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "status":
            reply = _ask(s, ctl.CtlRequest("status", ws=args.ws))
            return 1 if reply is None else _print(reply)
        reply = _ask(s, ctl.CtlRequest(args.cmd, ws=args.ws))
        return 1 if reply is None else _print(reply)
    except ConfigError as exc:
        print(f"wsctl: {exc}", file=sys.stderr)
        return EX_CONFIG
```

- [ ] **Step 9: Add the console scripts to `$HZ/pyproject.toml`**

In `[project.scripts]`, after `admind = "heterodyne.admind.cli:main"`:

```toml
wsd = "heterodyne.wsd.cli:wsd_main"
wsctl = "heterodyne.wsd.cli:wsctl_main"
```

Then `cd $HZ && uv sync`.

- [ ] **Step 10: Run the tests to verify they pass**

Run: `cd $HZ && uv run pytest tests/test_wsd_settings.py tests/test_wsd_daemon.py -q`
Expected: `22 passed`.

- [ ] **Step 11: Create `$HZ/docs/wsd.md`**

The operator page, including the seams for plans 4, 5, 6 and 8 (section 5).

```markdown
# wsd: the workstream daemon (queue and state)

`wsd` picks up beads for each configured workstream, one coder bead at a time, and keeps a journal so that a crash never loses or repeats a claim, a launch, a park or a resume (ADR 0001 §3.3, §4.3, §5.2, §9). This page covers plan 3: the queue, the journal, pickup, parking and recovery. Agent launching (plan 4), approvals (plan 5) and Marmot (plan 6) plug into the seams listed at the end.

**Until plan 4 wires in a runtime, `wsd run` holds every workstream (`runtime_unavailable`) and claims nothing.**

## 1. Configuration

All of it is host config (`config.toml`); a workstream file can't set `[wsd]` or `[integrations]`.

| Key | Meaning |
|---|---|
| `wsd.backstop_seconds` | How often pickup runs without a trigger. Default 60. |
| `wsd.reconcile_seconds` | How often state is rebuilt from beads and the runtime, then pickup runs. Default 300. |
| `wsd.launch_failures_before_human` | Failed launches of one bead before it is `stuck` with `needs-human`. 1 to 100, default 2. |
| `wsd.park_attempts_before_human` | Failed park attempts (session not confirmed stopped, git failure) before `stuck`. Default 3. Beads being unreachable never counts. |
| `wsd.inbox_attempts_before_human` | Failed attempts to apply one inbound event before it needs a human. Default 3. |
| `wsd.coder_role` | The role (from a workstream's `[roles]`) that runs beads. Default `coder`. |
| `integrations.beads.btq` | Required. The btq checkout; wsd loads its `bin/btq` and uses its `Queue` as agent `wsd`. |
| `integrations.beads.config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`, `tls_cert` | Optional btq locations, passed to `Queue` unchanged. Unset ones fall back to btq's own `BTQ_*` environment and defaults. |
| `integrations.beads.credentials` | Optional. Must be `{ file = "<path>" }`: btq's credentials file. Inline secrets are refused. |

A workstream is `workstreams/<name>.toml`, where `<name>` is a slug (lowercase letters, digits, `.`, `_`, `-`). wsd needs:

- `[repos]`: repository name to absolute path (or `~/...`). One must be called `default`. A bead's `metadata.repo` picks another one; an unknown name makes that bead `stuck` with `config_invalid`.
- `[roles]`: `coder` (or your `wsd.coder_role`) must name a profile from `[profiles]`.

The journal, locks and control socket live in `<state>/wsd/` (0700): `wsd.db`, `wsd.lock`, `claims/<ws>.claim` and `ctl.sock`.

## 2. Commands

| Command | Does |
|---|---|
| `wsd run` | Runs the daemon. Exit 78 means a configuration problem or a journal that failed its check; the unit should not restart on it. Exit 1 means another wsd holds the state directory. |
| `wsd tick pickup [--ws WS]` | Asks the running wsd to run pickup now. Timers may call it. |
| `wsd tick reconcile [--ws WS]` | Asks for a reconcile (recovery, then pickup). |
| `wsctl pause WS` | Asks wsd to stop new claims for `WS`. Returns only once no claim can start. Needs a running wsd. |
| `wsctl resume WS` | Asks wsd to allow claims again; wsd then runs a pickup. |
| `wsctl status [WS]` | Each workstream's state, holds, beads with their state and reason, open journal steps and the last event number. |

Pause is btq's own pause flag for the workstream's session worker, which wsd sets under the workstream's claim lock. A direct `btq pause` on that worker sets the same flag without the lock: wsd honours it from its next claim check, so at most one claim already under way can still complete. With wsd stopped nothing claims, so `wsctl` refuses rather than acknowledge anything. Pause stops **new claims only**. A running bead carries on, and a parked bead whose blockers closed still resumes. wsd never unclaims a bead.

## 3. States

Each bead wsd holds has one state and, when it is waiting or stuck, a reason:

- `claiming`, `starting`, `running`, `resuming`: being started, working, or coming back from a park.
- `parking`, then `parked` (`blocked_on_bead`), `waiting_input` (`waiting_on_operator`: it waits on an approval, question or confirm bead) or `held` (`held_by_operator`: `/stop`).
- `stuck`: needs a human. The reason says why (`launch_failed`, `launch_unrecorded`, `park_failed`, `config_invalid`, `claim_lost`, `routing_changed`, `worktree_failed`, `journal_lost`, `stop_unconfirmed`, `needs_human`, `unexpected_state`) and the bead gets the `needs-human` label.
- `held` and `stuck` beads move on only through the operator's release (plan 6). Removing `v2:held` or `needs-human` by hand changes nothing in wsd.
- `closed`, `dropped` (no longer ours).

A workstream is `running`, `idle`, `all_blocked`, `paused`, `held` or `stuck`. `held` lists its holds: `beads_unreachable`, `runtime_unavailable`, `claim_uncertain`, `launch_uncertain` (a launch whose outcome the runtime could not report), `actions_unreconciled` (a plan 5 action in any state but `pending`, `succeeded` or `failed`, closed beads included). A hold is retried on every pickup and cleared once its cause is gone. Holds never escalate a bead: an outage is not the bead's fault.

Every state change is also a progress event in the journal, carrying the reason, its detail (for a parked bead, the blocker IDs) and the message or event that caused it when there is one.

## 4. Recovery

On start, before the control socket opens, wsd runs for each workstream:

1. the journal integrity check (a failed check stops wsd with exit 78 and leaves the file in place; move it aside to start from beads alone);
2. read every bead the workstream's per-bead workers hold, found by assignee whatever its labels say, and every session the runtime may still be running;
3. hold the workstream while any action is unsettled, closed beads included;
4. replay every open park, release and escalation, and read back any claim a pickup left uncertain;
5. the sweep: stop the sessions of beads no longer ours, give a running bead whose session is gone a resume operation, and hold as `stuck` anything beads and the journal disagree on;
6. then a startup pickup, and only then events.

Recovery never launches: every launch goes through pickup's launch guard, which checks the runtime, the holds, the coder role, `needs-human`, btq's own post-claim checks, the launched-session record on the bead and its worktree, in that order. A workstream whose recovery failed stays `held` and is recovered again before its next pickup. Nothing is inferred from missing evidence. An unreadable claim, session list, session record or pause flag holds rather than proceeds. A bead the journal has no row for (a lost journal) is held as `journal_lost` until the operator releases it.

The journal is backed up with `Journal.backup(dest)`, a consistent online copy (SQLite's backup API). Scheduling it next to the beads backups is plan 8's job.

## 5. Seams for later plans

- **Plan 4, `AgentRuntime`** (`heterodyne.wsd.runtime`): `available()`, `sessions(ws) -> [Session]` (every session that may be running, until its end is confirmed; never a partial list), `launch(LaunchSpec)` (raises `LaunchFailed` when nothing started, `RuntimeUnavailable` when nothing was attempted, anything else is treated as uncertain) and `stop(session_key)` (returns only once the session has ended). `unknown` liveness is never treated as dead. The launched-session record (`metadata.wsd_session`: role, profile, session key, repository, worktree) is written to the bead before every first launch; plan 4 may add fields. Pass the runtime to `heterodyne.wsd.cli.run`.
- **Plan 5, `ActionReconciler`**: `unresolved(ws) -> [approval bead IDs]`. The default `HoldingReconciler` reports every action not `pending`, `succeeded` or `failed`, closed beads included, so the workstream stays held until plan 5 settles them. Approval beads must carry the `ws:<ws>` label and `metadata.action_state`.
- **Plans 5 and 6, parking**: `Parker.park(bead, blockers, why, hold, ref)` parks a running bead on blocking beads, or for the operator with `hold=True`. It takes the workstream's operation lock, raises `OpConflict` while another operation is open on the bead, and raises `BeadsUnavailable` when beads can't be reached; the caller keeps the request and retries after the next pickup.
- **Plan 6, release**: `Parker.release(bead, ref)` is the only way out of `held` or `stuck`. It raises `NotReleasable` for any other bead.
- **Plan 6, events**: `Journal.inbox_add` (deduplicated by surface and event ID), `inbox_pending`, `inbox_failed`, `inbox_finish`; `Journal.events_since(seq)` for progress; `Journal.snapshot(ws)` for status. Triggers enter as `Scheduler.pickup(Trigger(kind, ref))`.
- **Plan 8, backups**: `Journal.backup(dest)`.
```

- [ ] **Step 12: Update the configuration and install docs and the example config**

In `$HZ/docs/configuration.md`, replace the `[integrations]` bullet under "Host config" with:

```markdown
- **`[integrations]`:** external tools (btq, the `wn-agent` socket and its token) are configured by location here. `wsd` reads `[integrations.beads]` (see [wsd.md](wsd.md#1-configuration)); nothing reads `[integrations.marmot]` yet.
- **`[wsd]`:** the workstream daemon's timers and limits; see [wsd.md](wsd.md#1-configuration).
```

and, under "Workstream config", after the `[roles]` bullet, add:

```markdown
- **`[repos]`** names the workstream's repositories for `wsd`: absolute or `~/` paths, one of them `default` (see [wsd.md](wsd.md#1-configuration)).
```

In `$HZ/docs/install.md`, replace the btq row of the requirements table with:

```markdown
| btq, the Beads task-queue client | beads integration | by `wsd`, from `config.toml` `[integrations.beads]` (`btq` is the checkout) |
```

In `$HZ/examples/config.toml`, replace the `[integrations.beads]` table with:

```toml
[integrations.beads]
btq = "<path-to-btq-checkout>"           # wsd loads <checkout>/bin/btq
# credentials = { file = "<path-to-btq-credentials.json>" }   # optional; inline secrets are refused
```

- [ ] **Step 13: Run the whole gate**

Run: `cd $HZ && uv run ruff check && uv run pyright && uv run pytest -q && uv run python scripts/check_install_agnostic.py`
Expected: ruff and pyright clean; every test passes (the BTQ contract test is skipped without `BTQ_REPO`; the full suite takes about ten minutes); the install-agnostic check prints nothing and exits 0.

- [ ] **Step 14: Run the btq contract test against the real btq**

Run: `cd $HZ && BTQ_REPO=$BTQ_REPO uv run pytest tests/test_wsd_beads.py -q`
Expected: `37 passed`. This loads `$BTQ_REPO/bin/btq` but runs a fake `bd`: no queue is touched.

- [ ] **Step 15: Commit**

```bash
cd $HZ
git add src/heterodyne/wsd/settings.py src/heterodyne/wsd/ctl.py src/heterodyne/wsd/daemon.py src/heterodyne/wsd/cli.py src/heterodyne/defaults/defaults.toml pyproject.toml uv.lock docs/wsd.md docs/configuration.md docs/install.md examples/config.toml tests/test_wsd_settings.py tests/test_wsd_daemon.py
git commit -m "feat(wsd): daemon, control socket, wsd/wsctl CLIs and operator docs"
```

## Self-review notes (plan author)

**Spec coverage** (roadmap row 3 and the task brief):

| Requirement | Where |
|---|---|
| SQLite journal and inbox (§3.3) | Task 3 (`Journal`; inbox dedup by surface and event ID; `inbox_failed` escalates to `needs_human` after `inbox_attempts_before_human`) |
| Beads adapter with the `wsd` identity and per-bead workers | Task 1 (`ws_session`, `bead_session`), Task 4 (`BeadsAdapter`, through btq's `Queue` as agent `wsd`) |
| Shared pause gate and claim lock | Task 5 (`ClaimGate`), Task 7 (`test_wsd_pause.py` interleavings), Task 9 (`wsctl pause` through wsd) |
| Pickup, never idle while an unblocked bead exists | Task 7 (`Scheduler.pickup`, hypothesis property) |
| Park/resume journal | Task 6 (`Parker`: park, resume, release, escalate, the launch guard), Task 7 (resume ordering, the sweep) |
| Startup recovery order | Task 8 (`recover`, which never launches), Task 9 (`Wsd.serve`: socket only after recovery) |
| Journal backed up with the beads backups (§3.3) | Task 3 (`Journal.backup`); scheduling is operator decision (b) |
| Waiting-on-input, held, stuck with concrete reasons; per-message progress events | Task 2 (`Reason`), Task 3 (`set_state` emits `state:<value>` with `ref`), Task 6 (`waiting_input`, `held`), Task 7 (`Trigger.ref`) |
| Seams for plans 4, 5, 6 (interfaces and fakes only) | Task 5 (`AgentRuntime`, `ActionReconciler`, fakes), Task 6 (`Parker.park`), Task 3 (inbox, events), Task 9 (`assemble`, docs §5) |
| Crash-window and interleaving tests for every journaled transition | `PARK_POINTS`, `RESUME_POINTS`, `RELEASE_POINTS`, `ESCALATE_POINTS` and the conditional `!` barriers (Task 6), `POINTS` (Task 7), `RECOVERY_POINTS` (Task 8), `gate.checked`, `gate.pause.waiting`, `lock.waiting` (Tasks 5–7) |
| Fail closed | Global Constraints list; "Simplifications vs r1" maps each fail-closed rule to its test |

**Placeholder scan:** no step says "TBD", "similar to" or "add error handling"; every code step carries the full file. Edits to existing files show the exact text.

**Type consistency:** checked by building the tree task by task from these code blocks and running each task's tests, ruff and pyright at each step (Task 1: 24 passed; 2: 13; 3: 19; 4: 40 + 1 skipped, 37 with `BTQ_REPO`; 5: 9; 6: 53; 7: 50 new, 103 with Task 6's; 8: 22; 9: 22), then the full gate on the result with `BTQ_REPO` set. Every Python block compiles (`py_compile`), and test function names are unique across `tests/`.

**Known limits, deliberately left to later plans:** no real `AgentRuntime` and no crash-loop detection for a session that dies on every relaunch (plan 4), no action reconciliation against targets (plan 5), no inbox consumer, `/stop` or release command, and no Marmot rendering of progress events (plan 6), and no systemd units, timers for `wsd tick` or journal backup schedule (plan 8).

**Open before approval:** operator decisions (a)–(d). Design approval is not set.
