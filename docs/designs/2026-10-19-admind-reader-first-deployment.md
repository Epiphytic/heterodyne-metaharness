# Admind reader-first deployment

Status: author incorporation after ONE actual Claude Opus built-in HIGH review returned REVISE; all nine findings retained and addressed or explicitly disclosed below. No second review or validation. Requires explicit Marmot approval before execution. Author: Codex. Existing source task btq-x84mg; no new runtime code in this proposal.

## Decision requested

Deploy exact accepted product source `5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156` using the existing pinned platform installer with `--no-wsd --no-verity`. Keep WSD inactive and fulfillment disabled. Permit a product-reader-first rollout before the unfinished management queue producer and Hermes first-profile activation. This changes deployment order only; it grants no task closure, admission, migration of the fulfillment projection, request replay or management operation.

Primary approved design: https://github.com/Epiphytic/heterodyne-metaharness/blob/62e7ecf1433c601a36ad23b350da025b86a4c7dc/docs/adr/2026-10-19-readable-management-approvals.md . Fulfillment activation design: https://github.com/Epiphytic/heterodyne-metaharness/blob/f8a80b8fad51c0be015a797e11f4a968f90c4bfb/docs/adr/2026-10-18-fulfilled-question-activation.md . Source delivery: PR128 and PR129, both merged after exact-head full CI and actual Claude built-in reviews.

## Exact deviations and limitations

1. The readable-card ADR requires a separately reviewed combined product/queue artifact before either side is installed. This proposal explicitly permits the reviewed product reader first, leaving the old queue producer and its historical rendering unchanged. A new combined artifact, genuine deployment approval and native enrollment still precede enabling new format2 management submissions. Reader installation alone does not fix the old JSON cards.
2. Use the existing whole-platform installer, not a nonexistent admind-only installer. It updates the pinned store helper, reconciles/enables its existing system socket, builds the product release, writes the admind user unit, reloads system/user unit definitions and starts admind. `--no-wsd` leaves the currently absent/inactive WSD unit inactive; `--no-verity` makes no filesystem feature change. No gateway, user manager restart, policy/config edit, group membership change or unrelated service change is authorized.
3. Explicit operational deviation: with admind stopped, invoke the exact accepted Store constructor directly, then the existing accepted prepare helper's exact preservation check against the stopped backup, BEFORE allowing traffic. This uses the same additive core initializer as ordinary daemon startup, but moves that invocation earlier so the existing equality comparator is valid. It is not a new migration algorithm or a claim that the runbook already supplies this command. Do not apply the separate fulfillment projection migration or enable producers in this rollout. Explicit projection migration/activation stays separately gated; its offline rehearsal is evidence only. No new migration runner or installer is introduced.
4. Before restart, hold a stopped consistent SQLite backup and an exact before-state inventory privately. Perform the accepted exact equality preservation comparison while stopped, after explicit core initialization. Never use that comparator on an active database or claim it allows new rows. Restart may then receive legitimate traffic; snapshot-before-traffic is the clean verification boundary. Unexpected deletion, mutation of protected history, authority expansion or readiness failure stops admind and retains all state for reconciliation. Never automatically restore an older database after any new effect.

## Artifact and evidence

Source `5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156` is exact and clean in the private accepted-source checkout. Installer SHA256 `48b87826530a282ab0ad5ccbbb4bbb0c3c3c2f5acd4bf89bf13dfe39945bdf1e`; installer archive SHA256 `134b0d77528bd125652616c48e02031b18f940f9a2828712306ce911f9f316a7`. The archive is exactly the existing manifest's deploy tree plus store_core resource; release assembly separately archives the complete pinned commit with replacements/config/hooks ignored. Runtime dependencies use existing uv.lock/offline installer handling; no --fetch or --adopt is authorized by this proposal.

Root's initial exact-script preview exited0: existing store user/directories/config and system socket match; store_core differs and will be replaced; release and admind unit will be written; no WSD activation or verity mutation. Before execution repeat the supported pinned root-private bootstrap with --dry-run, checking the copied installer checksum before root runs it. Refuse drift or additional effects; changes to this scope require a new decision.

Full CI for exact PR129 candidate:8667 passed/103 skipped/12 subtests, natural exit0. Earlier PR128 exact candidate:8439 passed/103 skipped/12 subtests, natural exit0. Actual product review findings remain open and unwaived. Selected source excludes scheduling PR114, now source-merged separately. Its duplicate push test failure is retained and not declared passed.

Private offline preparation used the actual SQLite mode=ro backup API, then actual new Store on private copies. All original history and identity were preserved; outbox933sent/2quarantined. Projection plan/apply/reapply passed on another private copy, but is NOT part of this deployment. Actual old076 and accepted5db Store constructors/accessor reads then reopened a new migrated private copy preserving all rows/schema and14 triggers. Zero fulfillment receipts means nonempty receipt/old-writer compatibility remains unproven. Evidence directory: handoff/2026-10-15/codex/autonomous-resume/admind-accepted-source-offline-rehearsal. Private database snapshots must never be published.

## Execution, verification and failure

1. Retain the real approval, its full bound plan/source/installer/archive digest, current state and before/after journal. The operator's request to deploy does not manufacture this deviation approval. In a read-only snapshot inspect asks table_info first: absent presentation/render_version columns means no tagged rows are representable; otherwise require zero rows WHERE presentation IS NOT NULL OR render_version IS NOT NULL OR management IS NOT NULL. Historical format2 questions without these tags do not fail the check. Record the actual posting latch state; do not clear or set it.
2. Recheck exact clean source, installer/archive sums, managed-unit classification, WSD inactive state and installer dry-run. Refuse drift. Explicitly copy the actual managed admind unit and ~/.local/share/heterodyne/install/record.json into the fresh private before-state directory, preserving bytes and checksums, and retain complete release076; the installer does not back up its own rewrite branch. Never edit an existing release in place; never edit an existing release in place.
3. Stop only heterodyne-admind.service; verify stopped MainPID0. Take a fresh consistent stopped backup using the accepted prepare helper into a new private0700 directory. The helper rehearses migration on a copy, emits a nonexecuted installer command and preservation counts. Any failed preservation stops here. Snapshot files0600; no secrets/history in public logs. Then run the exact stopped core-initialization and comparison block below. A failed comparison keeps admind stopped and retains the live database and snapshots. Do not execute the helper-generated installer-command.sh: it lacks the two required narrowing flags.
4. Execute the existing documented pinned root-private bootstrap with the exact two hashes and `--service-user openclaw --no-wsd --no-verity`. No --adopt, --fetch or widening flags. Capture actual exit and summary; no blind retry after uncertain outcome. If config/readiness fails, stop admind, retain all evidence, diagnose against the exact failure.
5. Verify exact running interpreter/release/argv, unit and process agreement, stable PID/NRestarts for the installer's15seconds, existing subscription and confirmed operator/current-policy intersection. Preservation was already verified before restart. Do not rerun its equality comparator against active traffic. Verify retained readiness, current policy/confirmed intersection and latch privately through read-only access; unexpected authority expansion stops the service. New traffic remains retained, never erased. Report actual delivery/quarantine counts, migration schema and latch state. No automatic rearm or group membership modification.
6. Verify old ask repeat/details and one clearly marked operator-directed request only if genuinely approved for that exact test. Do not post fake approvals or duplicate outstanding cards. Offline exact native renderer tests are evidence for the new presentation; live readability remains pending the separately installed queue producer. The deployment acceptance is the stable new reader, preserved authority/history and old-card support, not a claim that the full harness is released.
7. On failure stop only admind and preserve database, release, logs and all new effects. Never restore a stale snapshot, drop additive tables, clear latches, cancel uncertain asks or downgrade automatically. A reconciled operator rollback decision is required after any new writes. Legacy store reopen compatibility alone is not rollback authorization.


## Explicit residual decisions and accepted limitations

Reader-first does not prevent another same-UID client from posting a correctly tagged management card after installation. No posting latch or producer enforcement is added in this deployment. Such cards retain native question authority and cannot by themselves authorize a queue management change. The operator must explicitly accept this mixed-version exposure and unavailable automatic downgrade: stop new management producers before any future downgrade, inspect exact tagged unresolved requests, and refuse rollback if any remain. A posting latch would be a separately designed code change, not an improvised deployment edit.

The ADR's old-writer and both-direction rollback acceptance is NOT met. Private old/new reopen tests prove only reads of empty-receipt snapshots. This plan requests permission to deploy the reader despite that gap, retaining the stronger no-automatic-rollback rule. After any live core initialization or restart writes, restoration/downgrade needs a separate reconciled decision. No clean rollback is promised. The corrected stopped equality check preserves the runbook's compare-before-traffic gate; only the initializer timing deviates.

Admind is the approval transport and will be unavailable during the stopped migration and installer build. Repeat offline-cache preflight/dry-run while it is up. Build/cache failure can extend the outage; the active operator terminal remains the documented escalation path. Do not silently restore a database to shorten the outage. Capture the last installer phase and ask through the terminal if transport cannot recover.

Runtime findings remain OPEN and UNWAIVED. Exact source reports are [PV R12](../reviews/pvbxr-main-custody-r12/actual-report.md), its dispositions, [X84 R7](../reviews/x84mg-main-custody-r7/actual-report.md) and its dispositions; earlier reports remain in the same repository. Material items include custody shutdown/cancellation and queued-command loss, malformed-bundle crash, contention and ordering, missing worker-startup regression, and stale metadata diagnostics. The dormant fulfillment verifier may warn every 30 seconds until separately approved projection migration. No custody enrollment/commands or fulfillment activation are part of this rollout. Existing legacy ingress and answers still face accepted PoC durability and supervision limitations. This decision accepts the specified reader deployment risk; it does not close those findings or qualify the full harness.

## Exact operational commands

Commands are run by the controller with genuine bound approval, not copied from the nonexecutable preparation output. AUTH is a private JSON/text readback of the actual Marmot/native decision. PREP is a new nonexistent private directory per attempt. These variables are task-specific; never override HOME. No credentials are printed.

```bash
set -euo pipefail
umask 077
reader_checkout="$HOME/.hermes/handoff/2026-10-15/codex/autonomous-resume/admind-accepted-source-offline-rehearsal/accepted-source"
reader_python="$HOME/repos/heterodyne-metaharness-v2-btq-btq-qzkdk/.venv/bin/python"
reader_database="$HOME/.local/state/heterodyne/admind/admind.db"
reader_commit=5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156
# PREP and AUTH must be filled from the recorded genuine execution intent.
test -n "${PREP:?new private preparation path}"
test -n "${AUTH:?genuine authority readback path}"
test "$(git -C "$reader_checkout" rev-parse HEAD)" = "$reader_commit"
test -z "$(git -C "$reader_checkout" status --porcelain --untracked-files=all)"
# Root-private bootstrap; preview while admind is still running.
sudo -n bash -s -- "$reader_checkout" preview <<'ROOT'
set -euo pipefail
umask 077
reader_checkout=$1
reader_phase=$2
reader_boot=$(mktemp /run/hz-install-boot.XXXXXX)
trap 'rm -f "$reader_boot"' EXIT
cat "$reader_checkout/deploy/install.sh" >"$reader_boot"
printf '%s  %s\n' 48b87826530a282ab0ad5ccbbb4bbb0c3c3c2f5acd4bf89bf13dfe39945bdf1e "$reader_boot" | sha256sum --check --strict --quiet
reader_extra=()
case "$reader_phase" in preview) reader_extra=(--dry-run);; install) ;; *) exit 2;; esac
bash "$reader_boot" --checkout "$reader_checkout" --commit 5db4c0c27c51b4e3db5a5dee8ab94ef25d3af156 --expect-sha256 134b0d77528bd125652616c48e02031b18f940f9a2828712306ce911f9f316a7 --service-user openclaw --no-wsd --no-verity "${reader_extra[@]}"
ROOT
# Expected preview: matching existing host state; store_core differs and
# release/admind unit will change; WSD remains inactive; no verity mutation.
# Refuse any additional effect or nonzero preview before stopping admind.
systemctl --user stop heterodyne-admind.service
test "$(systemctl --user show heterodyne-admind.service -p MainPID --value)" = 0
PYTHONPATH="$reader_checkout/src" "$reader_python" "$reader_checkout/deploy/prepare_admind_guard.py" --checkout "$reader_checkout" --reviewed-commit "$reader_commit" --database "$reader_database" --output "$PREP" --authority-file "$AUTH"
cp --preserve=mode,timestamps "$HOME/.config/systemd/user/heterodyne-admind.service" "$PREP/old-admind.service"
cp --preserve=mode,timestamps "$HOME/.local/share/heterodyne/install/record.json" "$PREP/old-install-record.json"
chmod 600 "$PREP/old-admind.service" "$PREP/old-install-record.json"
# Explicit live CORE initializer while stopped; projection stays unmigrated.
PYTHONPATH="$reader_checkout/src:$reader_checkout/deploy" "$reader_python" - "$reader_database" "$PREP/before.db" <<'PYTHON'
import sqlite3,sys
from urllib.parse import quote
from pathlib import Path
from heterodyne.admind.store import Store
from prepare_admind_guard import preserved
original=sqlite3.connect('file:'+quote(sys.argv[2])+'?mode=ro',uri=True)
live=Store(Path(sys.argv[1]))
try:
    preserved(original,live.db)
    print('Stopped core initialization: exact original history/identity preserved')
finally:
    live.close()
    original.close()
PYTHON
# Re-run the IDENTICAL root bootstrap above replacing only the final
# positional argument preview with install. All flags/hashes stay exact.
# Never run preparation/installer-command.sh. Capture actual terminal result,
# then perform installer readiness plus private authority/subscription checks.
```

The pinned README's example says its block stays unchanged. This proposal explicitly authorizes the two additional narrowing options --no-verity and preview --dry-run in the otherwise identical checksum-verified root-private bootstrap. Root copying/hashing precedes execution in both modes. The controller records the exact expanded argv and scripts before effects and never treats nonempty AUTH as authentication by itself.
