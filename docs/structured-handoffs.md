# Structured handoff deployment and use

Normative contract: [handoffs](../spec/handoffs.md).

Manager reviews/merges the signed patch, runs the full suite on merged main and
restarts the supervisor from that clean checkout under existing deployment authority.
No packages, hooks, database migration or Beads upgrade are required. Rollback uses
the previously deployed harness revision; avoid creating v2 groups while rolled back
because an older harness cannot validate their new profile. Preserve their metadata.

Admit new work with `workstream task RUN formula --file PLAN.json`, using
formula=deployable-v2 and routes for implementation, review, deployment, verification;
all routes must identify existing workstreams. Library-v2 uses implementation, review,
integration. Repeat precisely the same plan to reconcile uncertain admission.
Do not migrate a currently bound legacy ticket or create duplicate implementation work.
This implementation ticket retains its existing lifecycle; v2 full-flow verification
is isolated integration evidence, not a claim of live production molecule dogfooding.

Record stages via `workstream task RUN stage ID --stage STAGE --evidence-file FILE`.
Use the prior event digest from linked task show evidence as input_digest. Retain
sanitized files outside disposable test directories. Each reference selects a retained
artifact entry, for example `{"path":"/retained/test.log","sha256":"<64 hex>"}`.
Use actual `git rev-parse HEAD`, `git rev-parse --show-toplevel` and
`git rev-parse --path-format=absolute --git-common-dir` values for commit, checkout,
repository. Record actual test command and log; hashes cannot prove a test ran.
The specification lists the additional per-stage fields and authority requirements.

Before parent close, verify all required children are closed, live acceptance matches
the deployed revision and all referenced files remain available. Missing or changed
files fail closed. Recover an uncertain stage write by reading authoritative Beads
metadata and replaying the exact evidence, never by inventing a new stage or approval.
Post-deploy smoke: formula parser accepts the new profiles; existing ready/claim and
legacy stages remain usable. Do not create throwaway production tasks or restart any
coding session merely to demonstrate formula admission.
