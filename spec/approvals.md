# Harness approval policy

Authority: [tasks](tasks.md), [change lifecycle](changes.md), [sessions](sessions.md).
Manager answers sequencing questions already resolved by current policy. Missing
private PR infrastructure is a recorded blocker, not permission to create a public
remote. Actual decisions outside existing authority go to the operator.

`workstream approvals RUN ask --file JSON` accepts key, issue_id, argv (an array,
never shell code), cwd and evidence_ref (bounded retained locator, no values).
The current native Bead owner/status is verified. The scope is the exact owned
workdir; task relevance is a caller attestation against that bound issue, not a
semantic proof about arbitrary commands. Each unique key durably records a request
hash, classification, decision and evidence; a changed request cannot reuse a key.
No command is executed. This is the manager/harness policy layer: native permission
prompts remain native, with no typing approval keys or modifying native rules.
The supervisor's separate native approval relay creates a manager Bead when it
observes a prompt. A manager can act in the native dialog only under existing
standing authority, then provide a signed resolution and fresh observation.
Unknown or dangerous requests route to the operator before any native approval.

Default allows are exact scoped reads: git status --short, git diff --no-ext-diff
--stat, git log -5 --oneline, git rev-parse HEAD, and sed -n with a numeric print
range and an existing resolved file inside the owned workdir. Recovery holds route.
Credential-like paths, force flags, service/deletion/network commands, shell syntax,
outside paths and unknown execution forms route. Tests, Python entrypoints, git
hooks/commit and PR creation may execute arbitrary code or use credentials; their
names alone are insufficient for automatic safety. Executable resolution and
sandbox access remain native gates. This conservative classifier is not shell
security enforcement. Operator allow overrides cannot bypass dangerous/unknown
classification; explicit native approval is still needed for those operations.

Three distinct safe asks for the exact argv digest promote a run policy. Two runs
meeting that threshold promote the global tier. Promotion records threshold/run
counts in append-only version evidence. Repeated delivery of one ask does not count.
`inspect` lists bounded asks, current run/global policies and version history.
`override --pattern SHA256 --decision allow|route --scope run|global --evidence REF`
records actual operator policy review. Any route override wins across both tiers.
`incident --pattern SHA256 --evidence REF` demotes both tiers; subsequent tuning
cannot resurrect the pattern until operator review explicitly replaces each denial.
An old ask receipt cannot revive an incident-demoted allow. These commands record
trusted operator evidence; they do not cryptographically authenticate its author.

Uncertain/dangerous requests atomically enqueue one durable manager inbox item and
bound Marmot outbox question; delivery owns retry. Before group binding the manager inbox remains durable; replay the same ask key
after exact group binding to enqueue its visible question. This reconciliation is
explicit, not a background model poll.
No forced model turn or approval interruption occurs. The current facade exposes
messages, not a verified arrow-key question widget: routed text includes a question
ID and evidence locator, and the manager must make it actionable. This interface
does not intercept every native Codex prompt; managers submit harness asks explicitly.
No unsupported native hook or broad script trust is installed.
