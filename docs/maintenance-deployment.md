# Reviewed maintenance deployment

Implementation contract: [active spec](../spec/README.md). This runbook does not
claim deployment has occurred. Source owner is the existing Codex maintenance fork.
Operator executes reviewed operations outside the worker sandbox; never restart the
worker or mark the persistent run terminal.

1. Review diff and run tests with inherited HERMES_WORKSTREAM_RUN,
   HERMES_WORKSTREAM_ROLE and BTQ_SESSION_ID unset. Worker sandbox passed focused
   tests; real tmux needs operator execution. Full unittest run stalled in routing
   mocks under the sandbox and must be cancelled, not counted as passing.
2. Commit reviewed source on the existing workstream branch; integrate into canonical
   `/home/operator/repos/hermes-workstream-harness`. No remote publication is implied.
3. From canonical repo use Hermes venv Python:
   `python install_maintenance.py --home /home/operator/.hermes`, followed by existing
   `install.py` to refresh the concise managed SOUL block and current wrappers.
   The maintenance installer preserves other local AGENTS instructions, registers
   the supported user plugin, pins existing run/group and enables persistence.
   Restart supervisor and gateway through the established operator restart route.
   Smoke-load plugin with actual Hermes PluginManager and invoke pre_tool_call:
   protected write_file/patch block; read_file, git status/diff and sed -n work;
   ordinary MEMORY/config writes remain allowed. This guard is not shell isolation.
4. Choose private quarantine outside Hermes/brain/recall roots, for example
   `/home/operator/.local/state/hermes-maintenance-quarantine/20260916` (0700).
   Pause cron/gateway context writers during the short cleanup window; retain
   this Codex worker and all native identities. Do not claim arbitrary shell
   writers are synchronized by file hashes alone.
5. `python prepare_context_cleanup.py --home /home/operator/.hermes --output /tmp/context-plan.json`.
   Review manifest. It removes six incompatible imported skill frameworks and
   three obsolete delegation reference/backup files, with narrow MEMORY changes.
   USER keeps the English preference. No unverified milestone completion is claimed.
   `python -m harness.cleanup --manifest /tmp/context-plan.json --quarantine PRIVATE/files`.
   Re-run once: changed=0. Verified backups carry original paths/modes; restore
   individual files only after checking whether current content has changed.
6. `python migrate_maintenance_cron.py --home /home/operator/.hermes --backup PRIVATE/cron`.
   Native API retires duplicate relay and wake-sweep, preserves general failure
   monitor with read-only harness checks. No job is restarted or rerun.
7. `python -m harness.transcript_cleanup --home /home/operator/.hermes --quarantine PRIVATE/transcripts`.
   Review emitted plan: only audited detached IDs 20260902_182917_59e984 and
   20260903_151858_597526. Then same command with `--execute` in quiet window.
   Native deletion checks exact expected IDs; guarded callback checks ended state,
   all parent/children, operational references and content hash in the transaction.
   External registry/harness references checked before deletion; their writers must
   remain quiesced. Consistent native DB backup preserves full messages, prompts
   and schema; exact raw files are then quarantined with verified backups.
   Re-run must delete zero. Verify native search no longer returns selected IDs,
   current search still works, protected route/native lineage unchanged, DB integrity.
   Never restore the whole old DB over new live activity; restore into an isolated
   copy and review targeted recovery if rollback is needed.
8. `python install_brain_pointer.py --brain /home/operator/belthanior-hermes --backup PRIVATE/brain` patches
   explicit mirror inputs only: SOUL snapshot plus canonical spec pointer. It does
   not execute sync, git add, commit or push. Review the brain diff separately.
   Keep private quarantine outside all sync inputs. Existing sync uses broad git
   staging; do not run it over unrelated private untracked files.
9. Record actual deployment, dependencies (including explicit no-new-dependency
   result), test and cleanup counts in a sanitized committed evidence file.
   Append completion lifecycle evidence referencing immutable Git revisions and
   SHA-256 artifacts only after these checks. Approval remains approved until then.
   Close the bound Bead with evidence; task completion leaves run idle/routable.

Known limits: lexical hook does not interpret arbitrary Python/shell aliases;
metadata validates recorded approval scope, not human identity cryptographically;
quiet-window file cleanup cannot lock arbitrary third-party writers. Reboot tests
can exercise checkpoint behavior without physically rebooting the host; report which
was performed. Preserve original private backups until review completes.
