# Persistent workflow formulas

Authority and ownership: [index](README.md), [delivery groups](delivery-tasks.md),
[task admission](tasks.md), [execution evidence](changes.md).

`workstream task RUN formula --file PLAN.json` admits a persistent molecule of
ordinary Beads with native parent-child grouping and blocking dependencies.
PLAN has exactly key, title, description, formula, routes. Select a versioned name:

| Formula | Independently claimable roles, in dependency order |
|---|---|
| deployable-v1 | implementation → review → deployment |
| library-v1 | implementation → review → integration |
| research-v1 | investigation → recommendation |
| configuration-v1 | change → verification |

Routes maps every role to an existing workstream; RUN owns the delivery parent.
The checked-in JSON formulas in `formulas/` define those graphs. Contract version 2
records formula name and file SHA256 on every member. Published versions are immutable:
add a new version for changed semantics; retain old versions for outstanding/history checks.
Existing version-1 delivery contracts and their admission fingerprints remain unchanged.

Admission uses the shared deterministic request identity and native create/dep operations,
not blind `mol pour` retries: Beads 1.1.0 pour lacks a caller-selected stable identity.
The same key/content/routes reconciles missing members or edges; changed formula,
content or routes under that key fails before creating another group. The parent is
created first. No partially assembled graph is eligible through the facade. Native
writes are not a multi-record transaction: uncertain effects require exact replay.
No queue DB initialization, schema-skew override, upgrade, auto-claim or approval is implied.

Roles represent independently verifiable outcomes and ownership, not every edit or
command. Use the smallest applicable formula; do not turn one investigation into many
microtasks. Configuration is for genuine configuration changes, never a way to classify
source implementation as exempt from review. Existing design/approval dependencies are
added only where actually required through the authorized workflow; formula admission
neither manufactures approvals nor removes gates. No automatic migration of bound work.

## Completion contracts

Implementation/review retain the existing signed-commit, full-suite and private PR/merge
checks. Deployable deployment requires applicable=true, real operator deployment and
live verification; it cannot use a non-applicability waiver. Library integration records
stage integrated: reviewed final-tested commit, target consumer, command, result=passed,
evidence_ref and retained artifacts. It has no fictitious service deployment stage.

Research stages are investigated then recommended; configuration stages are changed then
verified. Each requires evidence_ref, substantive summary and nonempty artifacts (absolute
path and SHA256 per retained sanitized file). Later stages pin the preceding event digest
as input_digest. Verified additionally requires command and result=passed. These stages
do not require Git, PRs or deployment. Recommendation must report evidence, conclusions
and limitations; configuration change evidence records actual before/after and rollback
reference, with secrets excluded. Structural validation cannot judge those claims' truth.

Stages use the existing `task RUN stage ID --stage NAME --evidence-file JSON` command.
Evidence digests pin retained bytes, and group closure rechecks every prior artifact and
stage. Keep files immutable and available until delivery closes. Each role closes
independently; the parent closes only when every required role is closed with valid
evidence. Task scope receipts, stable current ownership, recovery, routes and native
approval constraints continue to apply. Research/configuration cannot use concurrent
PR handoff: finish the active step before taking another.
