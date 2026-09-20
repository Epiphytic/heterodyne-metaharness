# Workflow formula operation and verification

Contract: [workflow formulas](../spec/formulas.md). Implementation:
[admission/validation](../harness/task_formulas.py),
[shared delivery](../harness/task_delivery.py).

Example research admission (choose real existing routes and persist the request key):

```json
{"key":"research-request-unique-id","title":"Investigate the reported failure",
 "description":"Authorized investigation scope and acceptance evidence",
 "formula":"research-v1",
 "routes":{"investigation":"hermes-maintenance","recommendation":"hermes-maintenance"}}
```

Call `workstream task hermes-maintenance formula --file /private/plan.json`.
Repeat the identical plan after uncertain outcomes; never change its key to get past
an uncertain write. Inspect returned parent/role IDs and native dependencies. The facade
uses the existing configured BTQ credentials without exposing them or altering TLS.
`task show ID` exposes delivery_steps and formula identity in metadata. No model launch
or task claim happens on admission.

## Native compatibility evidence

On 2026-09-20 the installed CLI reports bd 1.1.0 (8e4e59d39). All four checked-in
JSON files compiled successfully through native `bd cook ABSOLUTE_FILE --readonly
--json`, with the existing TLS-configured BTQ client. Output retains version=1,
phase=liquid, ordered IDs and exact needs edges. No schema-skew override or DB upgrade
was used. Plain cook still initializes the configured DB connection on this version;
it is not an offline validation command.

The exact CLI source revision 8e4e59d39f3459a43cf21a3236a13eca4dd874f7 is available
in the local Beads checkout. cmd/bd/pour.go delegates creation to spawnMolecule and
exposes no caller-selected ID/idempotency key. cmd/bd/mol_show.go reads ordinary issue
subgraphs through loadTemplateSubgraph. This implementation materializes those persistent
parent-child/blocking graphs via existing deterministic native create/dep calls instead
of invoking a non-idempotent pour operation. Native compilation was exercised; no
production throwaway molecule or live workflow completion is claimed by these checks.

## Review and deployment

Manager reviews the private patch and merges through the existing lifecycle. Deploy the
whole repository including formulas/ (runtime resolves it relative to the package), then
restart the supervisor using the standard runbook. No new package, database migration,
provider hook or configuration is needed. Existing groups retain their old contract.

At deployment verify a read-only `task show` for the active task and service health.
When admitting the next genuinely authorized formula request, retain its plan privately,
run admission twice, and compare member IDs, formula digest and dependency edges. Confirm
only its first role is eligible and no claim changed. This is the live admission check;
do not create work solely to manufacture completion evidence. Rollback uses the previous
repository revision; do not admit v2 formula tasks until its handler is deployed, and do
not roll back past v2 support with active v2 tasks without an explicit recovery plan.
