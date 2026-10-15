# Guard release preparation and recovery

Membership events and counts are observations. They neither latch admind nor
grant operator authority. Confirmed membership intersected with the current
validated policy is the authority. Existing startup/unknown-operation latches
still require explicit recovery; this change never clears them on restart.

## Prepare the concrete release

After code review, supply the exact reviewed commit, its clean isolated checkout,
the admind SQLite database, a new private output directory outside the checkout,
and the file recording the actual operator grant. As the service user:

```sh
PYTHONPATH="$CHECKOUT/src" "$CHECKOUT/.venv/bin/python" \
  "$CHECKOUT/deploy/prepare_admind_guard.py" \
  --checkout "$CHECKOUT" --reviewed-commit "$REVIEWED_COMMIT" \
  --database "$ADMIND_DATABASE" --output "$PREPARATION_DIRECTORY" \
  --authority-file "$OPERATOR_AUTHORITY_FILE"
```

This single preparation invocation checks the revision, clean checkout and
migration import location. It preserves a SQLite-consistent `before.db`, rehearses
the migration on `rehearsal.db`, and checks original history, identities and
delivery rows privately. `report.json` prints counts and release hashes only.
Unproven legacy ownership stays quarantined. Review the actual pending/system/
request/quarantine/delay counts, rather than assuming an `asknote` prefix is safe.
The evidence-file hash records supplied authority; it does not authenticate or
create an operator approval. Existing output refuses, preserving earlier backups.

`release.tar` and `installer-command.sh` use the existing
[pinned installer protocol](../deploy/store-helper/README.md). Preparation never
executes the installer, touches services, migrates live state, changes policy,
rearms, creates a group or sends a message. The installer command is deliberately
not executable. The platform installer also manages store-helper and wsd: its
whole host scope needs an explicit deployment decision. Do not treat permission
to restart admind alone as permission for that whole-platform install. Do not
edit the deployed checkout or a live release target to avoid that decision.

## Deployment window and explicit recovery

1. Verify the reviewed artifact, deployment entrypoint and pinned installer sums
   against the preparation report. The existing pinned command copies the
   installer into a root-private file and verifies it before execution. Operator
   scope must cover the actual installer effects and any options; `--no-wsd`
   also stops a managed wsd and is not a harmless preview flag.
2. Stop admind for a bounded migration window. Take a second SQLite-consistent
   stopped backup into a fresh preparation directory. Use the reviewed migration
   on the stopped database only under the concrete deployment authority. Compare
   original identities, confirmed set, asks, histories and delivery rows before
   allowing traffic. Keep both backups private. Never access encrypted stores.
3. Start admind and confirm its service and subscription are stable. Report the
   **actual live migration and quarantine counts before rearm**. Any preservation
   failure or authority expansion stops recovery. Restart retains the old latch;
   never clear SQLite latch keys manually.
4. The operator checks actual admin-group membership. On explicit recovery
   authority run `admind rearm`; verify the latch clears and confirmed membership
   intersected with current policy did not expand. Count one is now valid.
5. On explicit go, use `admind operators add bel` for the already configured bel
   identity. Only explicit successful RPC settlement grants confirmation. A
   duplicate response grants nothing. If the earlier operation was unknown,
   verify membership, rearm the unresolved hold, then remove the unconfirmed
   policy key and add it again. Do not replace credentials or edit policy.
6. Verify ingress, delivery receipt and authenticated decision/reply correlation
   with an operator-directed, clearly marked test. Preserve message/ask/Bead
   correlation and idempotency. Reuse outstanding asks rather than duplicating
   them. This document and preparation script send nothing.

Rollback after any new writes needs a reconciled operator plan. An old daemon
cannot safely consume the new statuses/provenance, and restoring an earlier
database can lose decisions or replay external effects. Never restore the
snapshot automatically. Before traffic, with no new effects and explicit rollback
authority, use the verified stopped backup. Otherwise leave the service safely
stopped and retain the release, database and audit history for reconciliation.

## Recovery when the approval transport is broken

Run the local Hermes controller diagnostic using the current run identity:
`workstream task RUN --controller --workstream heterodyne doctor`. It is independent
of admind and a live Hermes manager. Escalate through the active operator terminal
or conversation, record the scoped out-of-band grant, and use reviewed native
recovery APIs with before/after readback. Queue task repairs use the supported
`btq manage` interface when authenticated Marmot decisions are available; the
queue OPS recovery procedure remains the operator route when they are not.
Never fabricate a card, approval digest, task completion or another worker's
identity. Worktrees and genuine approval requirements survive recovery.
