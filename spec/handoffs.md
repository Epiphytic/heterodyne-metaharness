# Structured retained handoffs

Authority: [formulas](formulas.md), [delivery ownership](delivery-tasks.md),
[execution stages](changes.md), [prerequisite gates](gates.md).

New `deployable-v2` and `library-v2` formulas strengthen independently owned Git
handoffs. Published v1 formulas, existing delivery groups and ordinary bound tasks
retain their contracts; there is no implicit migration or duplicate admission.
Deployable-v2 has implementation, review, deployment and verification roles; their
stages are committed/tested/pr-open, merged/final-tested, deployed, live-verified.
Library-v2 has implementation, review and integration (stage integrated).

Every stage records full signed commit, absolute checkout and repository identity
(the absolute `git rev-parse --path-format=absolute --git-common-dir` result),
evidence_ref and retained artifacts (absolute path/SHA256 pairs). Evidence_ref must
select exactly one retained artifact. Each event after committed includes input_digest
matching the preceding event digest, including transitions within a role. Repository
identity stays constant across worktrees/owners. Cross-repository deliverables use
explicit prerequisite gates, not a substituted repository identity.

Implementation records its clean owned checkout/current signed SHA. Later owners
may use the retained checkout to inspect signed commit objects; their unrelated
current cwd/HEAD is not required. Beads owns lifecycle/artifact records; checkpoint
and read-only linked snapshots are projections. Retain checkouts and artifact files
until delivery closes. A moved retained HEAD does not invalidate pinned history.

Test and final-test evidence preserve the actual command, full_suite=true,
result=passed, and log_sha256 equal to verified evidence_ref bytes. Test SHA must
match committed/merged respectively. PR-open retains remote, private review URL and
pushed commit. Merge additionally records approved_revision equal to the submitted
PR SHA, operator merge_authority, retained review_ref and authority_ref, and a list
of remaining restrictions (empty means none). The merged commit must contain the
reviewed PR revision. Post-merge tests pin that exact merge commit.

Deployment pins the post-merge-tested commit, target, environment, actual passing
result, applicable=true, deployed_revision and operator deployment_authority with
retained authority_ref. Review restrictions carry forward unchanged; nonempty
restrictions require retained restriction_resolution_ref from the operator. Recording
a deployment does not itself prove live acceptance or close the parent.

Live verification pins the deployed event digest, target, environment, deployed_revision
and observed running_revision equal to the tested commit. It requires actual command,
result=passed, retained live_verification_ref and nonempty acceptance_checks (each:
check, observed, result=passed). Library integration instead records target consumer,
environment, command and retained passing results against the final-tested commit.
Parent completion revalidates every mandatory closed step, event digest and artifact.
Missing deployment/verification, wrong SHA, missing/drifting logs or stale inputs block.

Exact stage replay reconciles a lost write acknowledgment without appending history;
changed contents under an already-recorded stage fail pending explicit reconciliation.
The scripts validate structure, relationships and retained bytes, not whether arbitrary
attestations are truthful or identities authentic. Preserve real operator evidence;
no native permission, merge, deployment, reaction approval or external call is granted
by recording a handoff. Detection and evidence checks invoke no model or polling loop.
