# Operator ask deployment

Contract: [active specification](../spec/operator-asks.md).
Task: `btq-harness-cb8fe048d297336039c81e2c`.

The worker authored source in its owned worktree. No live installer, admin lookup,
service restart, model workload, or external test ask was executed by the worker.
The manager confirmed `wn groups admins` output and plain-text npub references;
the worker verified the CLI source's account/group/admin response contract.

## Review and deployment

1. Review the signed private patch and merge through the existing lifecycle.
   Preserve the current manager/worker/native bindings and bound Bead.
2. Reconcile the live-home side-connection risk **before enabling the lookup**.
   `babysitter.py` and the active hourly-backstop prompt explicitly attribute prior
   WAL deletion/wedges to live `wn` side connections. This implementation does not
   repair MDK database ownership. A successful `admins` response is not evidence
   that the running agent retained healthy handles. Verify the exact deployed
   CLI/agent combination, database ownership safeguards and logs, or use a reviewed
   safe authoritative source. Do not silently substitute stale copied admin data.
3. Generate a private plan from the merged canonical checkout:

   ```sh
   python3 install_operator_asks.py \
     --babysitter /home/operator/.hermes/workstreams/babysitter.py \
     --config /home/operator/.hermes/workstreams/harness-config.json \
     --binary /home/operator/repos/mdk-fork/target/release/wn \
     --marmot-home /home/operator/.marmot-agents/hermes \
     --output /tmp/operator-asks-reviewed-plan.json
   ```

   The plan embeds private configuration: do not print or publish it. It changes
   only the two babysitter notification functions and the `marmot.group_admins`
   configuration. Existing approval handling remains untouched.
4. Back up harness SQLite coherently with its checkpoint projections. In a quiet
   window stop the supervisor and babysitter, then apply the reviewed plan:

   ```sh
   python3 install_brain.py --apply /tmp/operator-asks-reviewed-plan.json \
     --quarantine /home/operator/.local/state/hermes-quarantine/operator-asks
   ```

   Use a fresh quarantine directory outside active recall and Git repositories.
   The installer preserves verified rollback copies and source modes. Generate
   another plan with a new filename and verify zero mutations.
5. Update native cron job `a71c1d8ecd2f` (`hourly-backstop-agent`) through the native
   cron API, preserving unrelated sweep policy. Replace its question-delivery rule
   with a pointer to the canonical `spec/operator-asks.md` and the command:
   `workstream ask hermes-maintenance enqueue --hourly-backstop --file QUESTION
   --summary SUMMARY --key DURABLE_KEY`. The job currently delivers locally and
   calls the socket directly for questions; that bypass must be removed. Its
   unrelated auto-approval/recovery policy is outside this change.
6. Restart supervisor/babysitter on the merged clean source. Existing sessions
   remain exact resumes. Inspect retained pre-deploy pending asks: migrate/re-enqueue
   only genuine unanswered asks with explicit stable keys; do not replay historical
   approval evidence. New producers register asks automatically.
7. Verify a single authorized ask: exact group/account, current admin union plus
   both designated operators, one acknowledged outbox event, retained open-ask row.
   Then a distinct status must end with a labeled short reminder; it must not
   repeat the original question body. Retry the enqueue key and verify no new row.
   Inspect captured approval multipart delivery without accepting a native prompt.
8. Resolve the verified ask with actual response/inspection evidence and verify
   subsequent status has no reminder. Check agent database integrity/handles and
   fresh logs after lookup, plus supervisor/babysitter health. Record exact deployed
   revisions, plan/backup identities and live evidence before completing governance
   or closing the Bead. No physical reboot or mobile push-notification claim is
   implied by fixtures or plain-text references.

## Verification record

Focused tests initially passed 17 cases. The first full native suite ran 270 tests
with one native-only skip and two failures: generic brain-delivery mocks supplied
an implicit Mock as account identity, which cannot be persisted. Those fixtures
now provide explicit string account IDs; production validation was not weakened.
The sandbox full-suite attempt encountered local socket restrictions and is not
completion evidence. Final source verification ran **272 tests in 41.506 seconds,
OK with one native-only skip**, including real isolated sockets/tmux. Command:

```sh
env -u HERMES_WORKSTREAM_RUN -u HERMES_WORKSTREAM_ROLE -u BTQ_SESSION_ID \
  timeout 240 python3 -m unittest discover -s tests -q
```

Retained log: `/tmp/hermes-cb8fe0-asks-source-final.log`, SHA-256
`2a5297171c078ff058ce92a43cc343bdc677e2b205445ba6263d0c4767298b86`.
Governance validation and `git diff --check` pass. The private installer plan
contains two existing-file mutations and no creates; its fixture verifies backup
application and repeat generation yielding zero mutations. These results do not
assert live deployment or mobile notification receipt.

Ripwire quality-delta returned nonzero: test fixture clone/dynamic-dispatch findings,
transport class verbosity, and minor dispatcher growth/churn. No clean quality-gate
claim is made. Native permissions and consent handling remain unchanged. No new
Python package is introduced; the reviewed existing `wn` CLI becomes an explicit
runtime dependency for admin resolution only when its configuration is installed.
