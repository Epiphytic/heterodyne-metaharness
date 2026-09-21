# Hermes harness integration audit — 2026-09-20

**Question (operator):** are the merged/ingested Beads actually integrated into the live deployment?

**Method:** for all 22 closed hermes-maintenance Beads: final commit in canonical main?
Deployed-revision vs live supervisor start-time? Feature artifacts present in the working tree?

## Verdict

Git-level integration is CLEAN: every closed Bead's final commit is an ancestor of
canonical main, and HEAD (492464a) equals the newest deployed_revision.
The live supervisor (restarted 21:34:42 PDT for new-workstream provisioning) runs
canonical HEAD from the canonical checkout. No deployed fix was lost to a stale restart.

Features NOT yet live are, without exception, Beads that are still OPEN, not lost work:

| Open Bead | Feature | Status |
|---|---|---|
| 98d757a | Independent evidence-based reviewers | in_progress (worker active) |
| fa72b786 | Operator-reaction consent + outstanding asks | open (next in queue) |
| (follow-up) | Continuation close-after-boundary re-arm | defect logged on 53ac6f7e 2026-09-20 |

## Known genuinely-unintegrated item

- **Codex allowlists (Bead 326a06b):** published as PROPOSED-NOT-APPLIED by design
  (brain repo config/codex-allowlists.json, status field verbatim). Live ~/.codex/config.toml
  still has approval_policy="never", sandbox="workspace-write" and only 2 custom rules —
  the proposed expanded allow-list was never applied. This is an operator-reviewed
  follow-up by explicit decision, not drift. **Decision needed: apply, amend, or retire.**

## Per-Bead table (closed, hermes-maintenance)

| Bead | Final stage | Commit | In main | Sup>deploy |
|---|---|---|---|---|
| a40956ff | pre-lifecycle | - | - | n/a |
| ca87352d | pre-lifecycle | - | - | n/a |
| e472ea0c | close-ready | e9e477933 | Y | n/a |
| 259f6564 | close-ready | f37f6b145 | Y | n/a |
| 928fc46b | close-ready | 3a8a891e0 | Y | Y |
| cb8fe048 | close-ready | 63b0ddf8d | Y | Y |
| 53ac6f7e | close-ready | 2d6c672c6 | Y | Y |
| 7fa04aa8 | pre-lifecycle | - | - | n/a |
| 1c551cdb | close-ready | 1fd8cf737 | Y | Y |
| 0915045c | close-ready | 6e1da3d98 | Y | Y |
| 3157e53f | close-ready | f8ccbc215 | Y | Y |
| d290cf60 | close-ready | cc36ecf7b | Y | Y |
| 0df13f49 | close-ready | f7281d7b8 | Y | Y |
| 3a5654ba | close-ready | 58b5169a4 | Y | n/a |
| 0e771da3 | close-ready | 73ac00f82 | Y | n/a |
| 5a954683 | close-ready | c24d3f619 | Y | n/a |
| da81014f | close-ready | 3a2ec6a6d | Y | n/a |
| 0c5a5ae4 | close-ready | b3608c745 | Y | n/a |
| ff386b2c | close-ready | eb312baea | Y | Y |
| 3f284a1f | close-ready | 3b8eacec0 | Y | N |
| 326a06b1 | close-ready | f63984be1 | Y | Y |
| 4c52658c | close-ready | 492464ad8 | Y | n/a |

(pre-lifecycle = closed before lifecycle gates existed, 2026-09-17 era)

## Feature-in-code spot checks (canonical working tree)

| Feature | Present |
|---|---|
| queue ordering --at-top/prioritize/drop-everything | yes (5 modules) |
| admin-tagged escalations (admin_references/group_admins) | yes (3 modules + harness-config group_admins set) |
| operator_asks durable machinery | yes |
| transitions engine | yes |
| dedup suppression | yes (5 modules) |
| deterministic channel /status | yes (status.py, channel_status.py, hook) |
| marmot wedge-watch | yes |
| dead-pane recovery + backoff | yes |
| continuation close-re-arm | partial — see known defect below |

## Known open defect (this audit's trigger)

Second idle-with-ready-queue stall confirmed and root-caused: observe() in
harness/tasks.py only re-arms continuation via task_changed() for the run BOUND to
the closing Bead. When dependency 3f284a1 closed (14:46 PDT) while the run was bound
to 4c52658c, no re-arm fired; the close-path re-arm lost to boundary ordering when
4c52658c itself closed (18:50 PDT). Worker idle 18:50-21:15 until manager claim.
Fix scope logged as addendum on Bead 53ac6f7e. Also observed: the false-approval
viewport pin (Bead-text keywords like 'Operators approve' matching approval heuristics)
blocks manager claims until a supervisor observe tick clears it — exact-modal footer
recognition remains the top hardening item.

## Open queue snapshot (all workstreams, 2026-09-20 ~22:00 PDT)

- hermes-maintenance: 98d757a (in_progress, workers/reviewers), fa72b786 (open, reaction consent)
- harness-improvements (NEW, created 21:32): btq-dik roles design (in_progress, worker writing docs/open-workstream-roles.md), btq-1ji, btq-2sa queued
- mdk: 3 in_progress (QUIC corruption 1bdb3731, SQLite locks 6f5254ab, PR#1937 round2 76601505), 5 open
- unassigned/other: btq-akm (nostr probe), btq-ryv (crash snapshot mining)
