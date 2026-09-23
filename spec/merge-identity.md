# Rewritten merge identity

The [change lifecycle](changes.md) retains operator merge authority, signed pinned
commits, tested PR head, post-merge tests and deployment gates. Ordinary ancestry
remains a local fast path and needs no hosting-provider access.

Only Git exit 1 from merge-base --is-ancestor permits rewritten-history fallback.
Other Git failures block validation. For fallback, review_ref must exactly equal
pr-open.pr_url, a canonical https://github.com/OWNER/REPO/pull/NUMBER URL.
A bounded, read-only gh pr view request must return that URL, state MERGED,
the tested pr-open commit as headRefOid or an exact commits[].oid member, and
mergeCommit.oid equal to the
recorded merged commit. This verifies squash or rebase identity even when trees
changed during integration. It is not a claim of tree equality or content review.

PR-title/message references and overlapping filenames alone are insufficient.
Missing gh/auth/network, malformed responses, unsupported hosts, wrong PR/head or
merge revisions fail closed with an actionable error. No Git fetch, checkout,
merge, publication or fallback to latest main occurs. Revalidation of prior
steps uses the same check and may require GitHub access. Deleted/unavailable PR
records require operator reconciliation; no cached success bypass is introduced.

Review fixes may advance the PR head: membership binds the earlier tested revision
without replacing its evidence. Post-merge tests still cover the integration result.
An omitted commit (including a provider-truncated list) blocks reconciliation;
absence is never treated as membership. Force-rewritten heads without membership
require operator reconciliation.
Non-GitHub rewritten merges remain unsupported; ancestor merges remain compatible.
