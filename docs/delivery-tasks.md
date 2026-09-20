# Delivery task operations

Authority: [linked delivery contract](../spec/delivery-tasks.md).

After reviewed source integration, the operator restarts hermes-workstreams.service
from the clean merged canonical checkout. No installer, package, hook or database
schema migration is needed. Preserve the gateway, manager and worker native sessions.
Existing active Beads are not migrated. Verify the existing run/claim before and after.

For a newly authorized delivery, retain a JSON plan, for example:

```json
{
  "key": "operator-request-stable-key",
  "title": "Authorized change",
  "description": "Actual accepted scope and evidence references",
  "routes": {
    "implementation": "existing-implementation-workstream",
    "review": "existing-review-workstream",
    "deployment": "existing-deployment-workstream"
  }
}
```

Run `workstream task PARENT_RUN delivery --file /retained/plan.json`. Routes may
share a workstream; no new session is created. Repeating the exact plan reconciles
partial creation and returns the same IDs. Do not change request content on retry.
Inspect `task RUN ready --all` for native dependency edges and `task RUN show ID`
for linked artifact projections. The facade filters the parent until all steps verify;
native bare bd ready is not the governed pickup interface.

Use existing claim/stage/close commands on each step, in the order specified by the
contract. For review/deployment use the pinned predecessor commit and retained checkout
from delivery_steps; prepare the step owner's isolated checkout at the actual reviewed
result before recording evidence. The facade does not merge, deploy, switch HEADs or
transfer ownership on another agent's behalf. Read-only projections may be stale;
lifecycle commands revalidate the live queue.

Operator deployment verification should include an authorized new delivery plan or an
isolated test queue: retry creates exactly four Beads and five edges, no extra claims;
implementation completion leaves parent open; review/deployment remain ordered; parent
closure refuses incomplete evidence. Do not create fake production tasks or claim live
verification from unit fixtures. Keep all artifact checkouts through parent closure.

The isolated tests exercise real facade admission, read-only projections and close
routing using a simulated native queue and signature boundary. They do not prove a
live native Dolt round trip or operator deployment. Existing native Beads source rejects
cycles involving parent-child hops, so the plan intentionally has no parent-on-child
blocking edge. Parent readiness/closure is enforced by the facade.
