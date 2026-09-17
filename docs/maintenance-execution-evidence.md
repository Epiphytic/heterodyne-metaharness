# Hermes maintenance execution evidence — 2026-09-16

This is a sanitized record of observed execution, not an operational specification.
Authority remains the [active specification](../spec/README.md); procedures are in
the [deployment runbook](maintenance-deployment.md). Results below were reported by
the root operator after executing reviewed changes. Private backups, raw transcripts,
credentials and user message bodies are intentionally excluded.

## Implementation and deployment

Root integrated maintenance source commit `1bd23b7`. The maintenance installer and
existing harness installer succeeded. Root reported supervisor and gateway services
running and four native PluginManager smoke checks passing. The maintained binding,
scoped AGENTS policy, concise SOUL pointer and supported pre-tool guard were installed.
The real repository trust prompt was explicitly approved by the user; no blanket
approval bypass was introduced.

The installed session-sync plugin was already disabled before deployment:
`plugins.enabled` contained `marmot`, and afterward contained `marmot` plus
`hermes-maintenance`. Cleanup now records session-sync as installed but disabled;
its implementation description does not imply active context synchronization.
It was not implicitly enabled.

## Session identity and recovery checks

- Stable maintenance run: `b61d651b-8206-52d9-8965-d30d937dd103`.
- Persistent name: `hermes-maintenance`.
- Marmot association: `1cead9a9921044b3236ddb271e9a4cac`.
- Source Codex conversation: `01a0ac8d-fce3-7a61-aadf-b1ead3b4ad81`.
- Verified real Codex fork: `01a0acf9-dfb2-7d43-93f6-e90472d7c123`.
- Existing Hermes manager: `20260916_182541_e8cb77`, title
  `workstream-hermes-maintenance-manager`.

The operator verified the fork and removed bootstrap-only fork arguments from durable
configuration so recovery uses exact native resume. Provider tests cover one-time
fork admission, refusal to repeat an uncertain fork, exact successor resume, and
preservation of stable ownership independent of native IDs.

A live manager discovery defect was found: its native row had NULL cwd. The reviewed
fix adds a Hermes-specific fallback requiring exact deterministic title, CLI source,
no parent and the launch time window. Ambiguous matches remain unresolved. A read-only
check against the actual database independently rediscovered the exact manager above.
Additional fixtures cover title/source/time mismatches, ambiguous matches and forks.

Root integrated the manager discovery fix as `fb08265`, fast-forwarded live main
and restarted the supervisor. The first manager restart raced the dying old owner;
root verified the old PID had exited before retrying. The original manager
`20260916_182541_e8cb77` then resumed alive. The original Codex fork remained alive
and unchanged. No replacement conversation was created.

No physical reboot was performed as part of this deployment evidence. Recovery
behavior is exercised by lifecycle tests and exact native identity checks; those are
not a claim of a completed host-reboot drill. No interrupted workload was replayed.

## Cleanup execution

Root reported these outcomes from the reviewed quiet-window cleanup:

| Operation | First execution | Repeat execution |
|---|---:|---:|
| Reviewed file/memory mutations | 54 | 0 |
| Detached historical sessions removed | 2 | 0 |
| Messages removed with those sessions | 1,016 | 0 |

The removed sessions were `20260902_182917_59e984` and
`20260903_151858_597526`, selected for obsolete migration/harness guidance and verified
detachment. There was no blanket OpenClaw string deletion or message-text deduplication.
Root verified preservation of the other 31 sessions, their lineage and gateway
registry. SQLite integrity and foreign-key checks passed after cleanup.

Raw recovery material remains in private quarantine outside active Hermes, recall,
skill and brain publication roots. Native deletion, rather than UI-only archiving,
removed the selected sessions from the native database. Backups precede mutations;
repeat execution reconciles completed work rather than repeating it.

Brain mirror changes were committed as `4e195eb`. Root reported no private backups
inside that repository. The mirror retains a canonical-spec pointer rather than a
second normative spec. Private transcript exports were not included in the mirror.

## Verification results and remaining confirmation

Root's prior complete operator test run passed **149 tests**, including isolated
real-tmux checks. After the manager-discovery correction, the worker passed **16
focused adapter/maintenance tests**, plus governance validation and diff checks.
The final operator full suite, including the manager-discovery correction, passed
**151 tests in 12.061 seconds**. The earlier 149-test result is retained here only
as the prior deployment checkpoint.

The worker sandbox could not run the earlier real-tmux check and an earlier full run
stalled; root cancelled that owned test process. Those runs were not counted as
passing. Root subsequently ran the full suite with operator access. Isolated worker
test commands cleared managed session identity variables to avoid live hook effects.

Ripwire's quality gate remains non-clean: it reported CLI dispatch complexity growth,
new cleanup complexity, and static callback/test findings. This evidence does not
claim a clean quality gate. The report was reviewed separately from functional tests.

## Dependencies and practical limits

No dependency upgrade was performed for this maintenance change. The implementation
reuses the existing Python/SQLite runtime, Hermes plugin and cron APIs, tmux/systemd,
shared Beads queue, Marmot routing and native Codex/Hermes session stores. Existing
PyYAML is used by the maintenance installer; no new package installation was required.

The native Hermes runtime emitted a warning identifying **SQLite 3.50.4 and a WAL-reset
vulnerability**. This is an observed warning, not a claim that this task proved an
exploit or repaired the runtime. The existing dependency was unchanged, and database
integrity/foreign-key checks passed. No unreviewed SQLite upgrade was attempted.

The pre-tool guard covers recognized file-edit tools and explicit shell mutation
forms. It permits tested inspection commands, but is not an OS security boundary or
an interpreter for arbitrary programs, aliases or obfuscated shell commands. Recorded
approval scope is checked with proposal digests and immutable evidence references;
this does not cryptographically authenticate the human author of an evidence string.
Cleanup requires an operator-controlled quiet window for external writers; content
hashes alone cannot synchronize arbitrary concurrent processes.

The historical change remains approved, not completed, at this revision. This final
execution evidence must be committed before a completion event can reference its
immutable revision. The persistent worker remains open; writing this record does
not complete or stop the whole run.
