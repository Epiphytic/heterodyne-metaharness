# Maintenance outcome audit — 2026-09-20

Policy: [outcome refinement](../spec/task-refinement.md). This report is historical
execution evidence, not task state. [Native inventory](maintenance-outcomes-audit.json)
contains IDs, routes, record hashes and counts; use Beads for current status.

The bounded native inventory found 25 records: 21 closed, three open and the active
audit. After refinement: 27 records, five open, one active, same 21 closed records.
All closed JSON records compare equal before/after. All existing descriptions are
unchanged; only the open transition record received appended notes. No claim moved,
no completed work reopened, no approval broadened. Original private native records
remain in Beads; this report deliberately exports only titles/IDs/routes/hashes.

## Remaining execution paths

All rows route to the existing codex/hermes-maintenance stable worker
`b61d651b-8206-52d9-8965-d30d937dd103`. They are queued and unclaimed, not falsely
assigned to an executing agent. Objective: deterministic evidence-based workstream
communication. Task descriptions retain detailed acceptance and authorization.

| Canonical Bead | Independent outcome | Acceptance boundary |
|---|---|---|
| btq-harness-3f284a1f6cfca24044abf240 | Transition delivery | Ordered two-minute batches, stable retries, whole first handoff, steering/approval provenance, stop states; deterministic detection |
| btq-harness-98d757a000aa1d75d4c99280 | Independent progress/completion review | 10/30/90/150 elapsed deadlines; retained evidence; artifact+turn handoff; delivered R6 and resolved/accepted findings gate closure |
| btq-harness-fa72b786ac157f7c2d051bdd | Outstanding asks and scoped consent | Activity-aware hold; admin tags; hourly damping; exact reaction/ask attribution; stream+question-line heart scope; native approvals preserved |
| btq-harness-4c52658c8c1b5806e845cb41 | On-demand /status | Queue and bounded pane tail, deterministic script, no model, <5s, exact route |
| btq-harness-326a06b1084c6bc455a02b5e | Approval audit and proposed defaults publication | Retained audit, reviewed scoped config, verified private brain GitHub URL; no live allowlist application |

The reviewer and ask outcomes come from the operator-approved design/addendum in
this persistent conversation, not invented new requirements. They were missing
from native task text. New metadata links their source (3f284a1), refinement audit
(ff386b2), and shared objective. Reviewer has a native blocking edge to transition
delivery. Ask policy is independently shippable atop existing ask/consent machinery.
Existing transition description remains byte-identical; appended notes explicitly
exclude implementing competing reviewers/consent. This supersedes only implicit
bundled execution ownership, never original authority or completed implementations.
The audit/publication steps of 326a06b1 are one outcome: a verified published proposal;
applying that proposal is explicitly outside it and must not happen automatically.

## Closed foundations: preserve, reuse, never recreate

| Concern from historical 259f bundle | Canonical historical records |
|---|---|
| Full permission relay and exact consent handoff | btq-harness-259f65643d37dc3464c4dec4; btq-harness-ca87352d5f650e4786833c94 |
| Dedup/rate limit and stable pane digest | btq-harness-0915045cb906607b6df230d8; btq-harness-3a5654bab1e18ffacd5157c7 |
| Terminal activity/approval observations | btq-harness-3157e53fbf58faab13719a2e |
| Dead-pane recovery and boundary accounting | btq-harness-53ac6f7ea00122996fbb6709; btq-harness-928fc46b2edb8305c764dc88 |
| Admin-tagged asks | btq-harness-cb8fe048d297336039c81e2c |
| Delivery/concurrency, formulas, gates, handoffs, routing | btq-harness-d290cf60e03357167888a515; btq-harness-0e771da3ce9ed353a10c8609; btq-harness-5a954683b196b1f42cc172e1; btq-harness-da81014f29d4099246775b15; btq-harness-0c5a5ae445fa3772e9bee368 |

Other closed records are enumerated unchanged in the inventory. No seven-improvement
Bead was duplicated. The accepted live successor claim verifies ordinary dependency
unblocking and routing; neither this task nor its predecessor has a formula contract,
so this report does not describe that as a generated formula's full lifecycle test.

## Integration and verification

No package, runtime code, hook, service configuration or DB schema changes. Manager
reviews/merges the policy and report through the existing private patch lifecycle;
no service restart is needed for documentation. Native admissions/append/edge already
used the deployed facade and are inspectable. Verify live IDs, exact routes, reviewer
blocking edge and source mapping. Preserve all tasks and history on rollback; amend
with explicit supersession instead of deletion. Do not start these tasks from this
audit's report; execute only after the ordinary safe-boundary claim checks.
