# Permission consent rollout

Contract: [native permission relay](../spec/permission-relay.md).

This runbook is not deployment evidence. The operator reviews/merges the private
patch and retains a coherent private harness DB/checkpoint backup first. From the
merged canonical harness repository, prepare the adapter plan:

```sh
python3 install_permission_relay.py --adapter /home/operator/.hermes/plugins/marmot/adapter.py --output /tmp/hermes-permission-plan.json
python3 install_brain.py --apply /tmp/hermes-permission-plan.json --quarantine /home/operator/.local/state/hermes-quarantine/permission-consent
```

Keep the plan private: it embeds adapter source. The pinned shim package path is
the repository running the installer; generate from canonical merged source,
not a temporary checkout. The existing cleanup installer retains verified private
backups and rejects drift; repeat application is idempotent. No connector rebuild,
package update or wn-agent restart is required. Reload the Hermes gateway and
harness supervisor under operator authorization, preserving native sessions.

Before live testing, run the suite from merged source with `PYTHONPATH` set to that
repository and managed identity variables removed. Invoke the installed shim from
an unrelated cwd with the native Hermes venv and a non-reaction fixture; it must
return false without sending a message. Confirm the actual adapter mutation
hook still matches the reviewed anchor. Record source, suite log/hash and service
revision. A passing fixture suite alone does not certify deployment.

The manager observation correction also requires a supervisor reload. Before
reload, inspect retained manager inbox entries: a prior failed `send` may have
queued its message before reporting `Native approval pending`. Do not enqueue a
second copy or assume the first was lost. In particular, the old transport-design
question has been superseded by the operator's durable-consent direction.
After reload, verify a normal manager input screen with historical "I approved
it" prose is no longer `awaiting_approval`; verify its exact native ID is unchanged.
Do not clear approval state manually or send approval keys to force this check.

At the next real native approval, verify captured complete region and numbered
parts reach the exact channel, all acknowledged message IDs are retained, and a
permitted operator reaction creates one `permission_consents` row and manager
inbox item. Verify the record contains the connector event, target text and full
captured region. Replay that event through the adapter without a second handoff.
The manager must inspect the exact pending native request and full command/reason
before acting; record the actual native resolution separately. If the native UI
clips that command or the request changed, hold instead of accepting. Do not
create an extra model workload or self-approve a prompt just to test this path.

The transport retains any original signed Nostr event fields provided by the
connector; it does not assert those fields are present or independently verified
when only the authenticated connector projection is available. There is no
app-server migration, automatic key injection or blanket approval in this slice.
