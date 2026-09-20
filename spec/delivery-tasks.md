# Linked delivery ownership

An explicit `workstream task RUN delivery --file PLAN.json` creates a new delivery
parent and three separately claimable task Beads. PLAN contains nonempty key, title,
description and routes (implementation, review, deployment), each naming an existing
workstream. The parent routes to RUN. Routes do not transfer another worker's identity
or grant review/deployment permission. Existing tasks keep the full [change lifecycle](changes.md);
there is no automatic migration of active claims.

All four records carry versioned harness_delivery metadata with role and the same
four member IDs. Each child has a native parent-child edge to the parent. Review is
blocked by implementation; deployment is blocked by review. Do not add a parent
blocking edge to its own children: native Beads propagates blocked-parent state and
rejects that dependency cycle. Instead, the facade withholds parent eligibility until
all mandatory children are closed with verified lifecycle evidence. Parent closure
revalidates the same conditions. Parent-child grouping alone is not delivery evidence.

Admission uses deterministic per-role request keys and the existing request fingerprints.
Retry the same complete plan after uncertain writes; changed content/routes under the
same key fail. Missing members or edges fail eligibility closed until the exact plan
reconciles. Admission sends deduplicated manager inbox notices and read-only projections;
it never claims, releases or replaces tasks. Normal route, design approval, dependency,
recovery and single implementation ownership checks still apply. Claude design approvals
must be supplied through the existing authorized approval workflow; creating a delivery
group does not supply them.

## Step completion and artifacts

- Implementation owns committed, tested, pr-open. Once its signed exact commit passes
  the full suite and is pushed for private review, this step may close independently.
- Review owns merged, final-tested. Actual operator review/merge evidence and full
  post-merge test evidence must follow the closed implementation's pinned PR commit.
- Deployment owns deployed, close-ready. Actual operator applicability/live verification
  must match the closed review's tested merged commit. Non-applicable deployment still
  requires the existing explicit operator reason.
- Parent has no duplicate execution stages. It closes only after all three mandatory
  children close and their complete combined evidence passes the lifecycle validators.

Each step records harness_delivery_artifact (absolute checkout, full signed commit,
branch) with its lifecycle metadata. Stage writes and step closure require the owner's
clean checkout at the evidence commit. Later steps and parent closure validate retained
commit objects/signatures and evidence in those pinned checkouts, without requiring old
HEADs to remain current. Keep artifact checkouts available until delivery closes; missing
artifacts block verification. Never infer provenance from another worker's current cwd.

Read-only `task show` adds delivery_steps with linked status, artifact and lifecycle
projections after validating the requested task's routing and group membership. These
snapshots are context, not current execution authority. Native queue reads revalidate
before lifecycle writes/closure. Implementation, review and deployment may use different
existing workstreams; the stable worker identity and exact native session remain unchanged.

Lifecycle/artifact metadata are evidence, not new execution scope. Contract/route/scope
changes still require receipt. Evidence checks do not authenticate arbitrary operator
attestations: preserve real logs and approvals. Closing implementation, an idle process,
or a completed model turn never means the parent delivery is done.

See [task admission](tasks.md), [worktrees](worktrees.md), and
[queue dependencies](queue-order.md). No package or service configuration is added.

Version-2 [workflow formulas](formulas.md) reuse this machinery with explicit alternative role and evidence contracts.
