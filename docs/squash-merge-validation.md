# Squash/rebase lifecycle validation

Design for btq-harness-4a7f8db44129a3ec2113b737: reuse task_stages.validate_evidence
and its prior_history callers. Catch only merge-base exit 1, preserving the existing
workspace.git checked contract. Isolate hosting-provider validation in
harness/merge_identity.py. Active contract: [merge identity](../spec/merge-identity.md).

The suggested changed-file containment check was rejected: changing the same paths
can omit or reverse every reviewed change. A commit-message PR reference is likewise
not proof. Exact provider URL/commit-membership/merge identity is the stronger available binding.
Official interface: https://cli.github.com/manual/gh_pr_view (JSON fields url, state,
headRefOid, mergeCommit, commits). The existing gh executable/auth is required only for the
rewritten-history path; no new Python package, config, schema or native hooks.

Manager deployment: run full suite on frozen/signed source, open private Radicle
patch, merge through existing stages, rerun post-merge suite, restart supervisor
from reviewed canonical checkout. Revalidate the blocked octo-sts-rust PR9 delivery
claim via its existing facade and owner. Retain gh PR identity output privately
and bind it to deployment evidence. Do not mark live unblocking passed until the
actual deployment-step claim succeeds. Existing approval/ownership guards apply.

Tests use disposable Git topology and mocked provider responses, never live merge
or claim mutations. Targeted and full-suite results are reported separately from
live deployment. No live deployment or unblocking is claimed by this document.

Read-only incident probe found PR9 final head 01ee6b82cf8366111456e5e0a1f7093b678b37f8,
with recorded 97733fcd59952aa68490ba4767e72d07021f8c93 present in its commit list;
merge d7aecb582c5bc1914cdf5e1f72dad46fe24892d7. Thus exact final-head equality alone
would incorrectly reject the reported review-fix case. Membership is required
when the final head differs; overlapping files are not used as evidence.

Quality-tool disclosure: the final working-tree ripwire pass reported 28
regressions (17 gating), including dynamically invoked test fixtures classified
as dead code and short-horizon churn on validate_evidence. This is not a clean
quality gate claim. Targeted integration tests and the full suite validate the
behavior; provider verification remains fail closed when unavailable.
