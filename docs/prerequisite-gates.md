# Gate deployment and verification

Normative contract: [spec/gates.md](../spec/gates.md).
Implementation: `harness/task_gates.py`. No package, database schema, provider hook
or service configuration changes. Manager deploys the signed reviewed harness tree
and restarts the supervisor using the existing deployment procedure.

Beads 1.1.0 (8e4e59d39) was inspected at its exact source revision. Native
`create --dry-run --type gate --id ...` accepted a gate without writing an issue.
`gate resolve` uses native closure; native cross-rig checking is unsupported.
No timer/GitHub watcher, direct DB access or queue upgrade was used.

For real admission supply a retained JSON plan to `workstream task RUN gate create
--file PLAN`. Retain its gate ID and captured scope. The resolution file must pin
the same task, kind, revision, scope and actual retained evidence. Review that
proof before `gate resolve GATE --evidence-file FILE`. `gate check TASK` validates
without resolving. Verify blocked ready/claim before resolution and eligibility
of only the intended role afterward. Repeat the exact create and resolve commands:
no duplicate gates, edges or resolution evidence should appear. Do not fabricate
an approval or resolve a real prerequisite merely for a smoke test.

This implementation ticket was already bound as a legacy task. Formula admission
creates new groups and explicitly prohibits automatic active-claim migration.
Therefore this task keeps its current lifecycle; isolated integration tests exercise
a deployable formula role with a native gate end-to-end through admission and
resolution. A new real formula delivery can use gates after operator deployment.
This is not a claim of production formula or gate-resolution dogfooding.

Evidence checks authenticate retained bytes and revision correspondence, not the
identity/truth of a supplied issuer string. Privileged direct native writes can
bypass facade policy. Native tool permissions and separate design approval remain
required. Quality-tool findings are reviewed separately, not a claimed clean gate.
