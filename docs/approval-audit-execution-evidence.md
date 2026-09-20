# Approval audit and proposed Codex profiles: publication evidence

Task: `btq-harness-326a06b1084c6bc455a02b5e`.
Authority: [current approval policy](../spec/approvals.md) and the bound operator
request. This task proposes defaults; it does not change native approval policy.

## Published artifacts

Private brain repository `liamhelmer/belthanior-hermes`, signed commit
`687dc1dedf7c49a5f8af6b4b7329939cae9884be`, signature `G`, author
`[operator-email-redacted]`. Created in isolated worktree
`/tmp/brain-326a06b-review`, branch `bead/326a06b-approval-audit`.
The authorized non-force push to GitHub main succeeded from base `a3c7268`.
GitHub API confirmed repository privacy and the published config blob
`f5494e614fe43933be1d3d0615dc8deaeee7134b`.

- [Proposed config](https://github.com/liamhelmer/belthanior-hermes/blob/687dc1dedf7c49a5f8af6b4b7329939cae9884be/config/codex-allowlists.json)
- [Audit report](https://github.com/liamhelmer/belthanior-hermes/blob/687dc1dedf7c49a5f8af6b4b7329939cae9884be/reports/approval-audit-2026-09-18.md)
- [Audit map](https://github.com/liamhelmer/belthanior-hermes/blob/687dc1dedf7c49a5f8af6b4b7329939cae9884be/reports/approval-audit-2026-09-18.json)

No raw transcript, command argument, credential, private backup or live config
was published. Only these three paths were staged. The canonical local brain
checkout was not changed; the manager should fast-forward it before scheduled
brain sync so its local main incorporates the GitHub publication.

| Brain path | SHA256 |
|---|---|
| `config/codex-allowlists.json` | `953a2e4cdeb835c8a735cb03442c27f97e527e2fc0b1423031912c693fc66828` |
| `reports/approval-audit-2026-09-18.json` | `f000a62604067bbeb27f0b0728b1f0b1ba9032071c5ad2dc2adbaed59b96c335` |
| `reports/approval-audit-2026-09-18.md` | `c73c510094bd726def129c728256b89a12309d8022421851441666841d0db87d` |

## Audit interpretation

185 native escalation-call candidates: 139 MDK, 46 maintenance. Thirteen separate
manager approval attestations overlap those candidates and are not additive.
Four dynamic command expressions remain unresolved. Candidates do not prove a UI
prompt appeared, executed, or was approved. No native approved/denied/pending
outcome is invented from command success, quoted prose or current pane content.

The inclusive named dates September 11–18 are eight calendar dates. The report
explicitly uses September 11 00:00 through September 19 00:00 America/Vancouver,
rather than silently excluding the last day to satisfy the task's seven-day label.
All 35 retained native JSONL files and nine current run checkpoints were examined.
Checkpoints cannot establish historical pending state. In-window outbox and
permission-relay/consent tables retain no records. status_notices has no timestamps.
This is a retained-record audit, not a claim that every historical ask survived.

Read-only extraction script and private intermediate map remain in `/tmp`:
`approval-audit-326.py`, `approval-audit-326-private.json`. The published JSON
includes source hashes and transcript line references; raw content stays private.
Seven extraction checks passed (literal calls, quoted source, comments, variable
commands, concatenation, ordinary sandbox requests). JSON structure, profile
exclusions and count consistency checks passed. Brain diff whitespace check passed.
No package, plugin, hook or permission setting was installed or changed.

## Proposed policy boundaries

The JSON is a reviewable policy description with `activate: false`, not a native
Codex rules file or a loader. General work stays sandboxed in the owned checkout;
elevated writes require reviewed, backed-up manifests over resolved exact paths.
Arbitrary interpreter/build prefixes are not unrestricted allowances. Credentials,
force/destructive operations, service control and external publication remain
operator-escalated. Native protected Git paths and recovery gates remain intact.
Application/rendering to native policy requires a separate reviewed task.

## Close-out and single notification

The GitHub link notice has NOT yet been sent. Complete lifecycle review/closure,
then enqueue exactly one durable outbox item after preceding channel statuses.
Suggested stable key: `approval-audit:326a06b:687dc1d:published`.
Exact body:

```
Proposed Codex allowlists and approval audit published (not applied): https://github.com/liamhelmer/belthanior-hermes/blob/main/config/codex-allowlists.json
```

Target the existing bound maintenance group `1cead9a9921044b3236ddb271e9a4cac`.
Use existing FIFO/retry delivery and verify its delivered receipt; never retry by
creating a new message identity. Do not send a second free-text completion after
that link. Unrelated future messages cannot be guaranteed absent indefinitely.
No notification delivery or task closure is claimed by this evidence.
