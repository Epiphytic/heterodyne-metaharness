# Independent reviewer rollout

Contract: [current specification](../spec/reviews.md). No package installation or
new provider hooks. The existing native Hermes venv supplies the reviewer.

After source review, the manager retains a coherent private harness database and
checkpoint backup, merges the signed revision and runs the exact-commit suite.
Set `reviews.enabled: true` in the harness configuration using the existing
verified-backup installer. Optional `reviews.hermes_repo` points at the native
Hermes checkout (default `~/.hermes/hermes-agent`). Restart the supervisor from
the merged checkout. Worker and manager sessions remain intact. Disabled rollout
creates no review jobs and preserves existing closure behavior; enabling applies
the completion gate to current open tasks as well as new ones.

`workstream review hermes-maintenance inspect` lists retained jobs. Completion
handoff uses `workstream review hermes-maintenance handoff --file PRIVATE_JSON`.
The JSON contains `key`, full current `commit`, nonempty `artifacts` array of
absolute retained `path`/`sha256` objects, and `native_turn_key` (exact native
session and turn). Submit during that working turn, then end it normally. The
confirmed matching native completion releases the review; an idle pane does not.
Do not fabricate a native event to release a handoff.

The independent process resolves normal native provider credentials, then uses
a private per-job Hermes home with user plugins disabled, no worker context or
memory, and zero tools. Provider credential refresh may use the normal auth
store. This is tool isolation, not an OS filesystem sandbox. Native accounting
and logs may write to the private job directory. Credentials/config values must
not be copied into execution evidence. Evidence collection reads Git without
external diff/textconv and captures 500 terminal lines; large files are explicitly
bounded. The model must report these limits and missing evidence.

Each job has private evidence/result/error files under the harness state root's
`reviews` directory. Retain these for audit; do not publish raw transcripts.
A started job is never automatically replayed after uncertain process death.
After verifying its process exited, an operator may use `review NAME retry ID
--evidence-file FILE`: evidence includes `authority_basis: operator`, `actor`,
`review_id`, `result_digest` (canonical digest of the stored result, including null
for a missing result), `old_process_exited: true`, and retained `path`/`sha256`.
This creates a distinct attempt and preserves the old one. Transport retries reuse
the original outbox identity and never invoke another model.

Blocking findings require a new artifact-bound handoff/review after fixes, or
`review NAME accept ID --evidence-file FILE` with operator authority, actor,
review ID, exact result digest and retained acceptance path/hash. These are
structural checks on trusted operator evidence, not authentication of arbitrary
actor strings. The coder must not fabricate operator acceptance.

Live verification requires one real scheduled reviewer: check independent native
identity, evidence digest, full summary chunks and acknowledged outbox delivery.
Verify closure refuses an undelivered summary. Record actual results separately;
fixtures, this runbook, and successful process construction are not live model
verification. The manager owns deployment and live verification. No physical
reboot or real reviewer invocation is claimed by this source document.
