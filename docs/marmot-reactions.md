# Marmot reaction restoration

Contract: [reaction specification](../spec/reactions.md).
Modules: [facade](../harness/marmot.py), [durable intents](../harness/reactions.py),
[delivery](../harness/delivery.py).

## Findings

Read-only inspection of mdk-fork commit
`09a8ad5a8b4d30d48afee875126faaba57fc4100` found existing protocol variants
`SendReaction` and `RemoveReaction` in `crates/agent-control/src/lib.rs`, including
`reaction_requests_have_stable_wire_shapes`. Connector
`crates/agent-connector/src/messaging.rs:send_reaction_response` calls the runtime
reaction operation and returns `AppEventSent`. Connector tests in
`crates/agent-connector/src/tests.rs` add a reaction twice and assert identical
message IDs. The application client checks its own active reaction event IDs and
returns an existing ID without publishing again. This is active-state tuple
deduplication, not a durable arbitrary-key protocol feature.

The harness lacked this facade operation and an outbox representation for it.
This change adds them and a generic CLI enqueue entrypoint. No mdk-fork source
change, rebuild or new dependency is currently required. Inspection alone does not
verify the running wn-agent binary; operator verification remains necessary.

## Reviewed deployment procedure

1. Run the full harness suite outside the worker sandbox, clearing inherited
   HERMES_WORKSTREAM_RUN, HERMES_WORKSTREAM_ROLE and BTQ_SESSION_ID. At minimum:
   `python3 -m unittest tests.test_reactions tests.test_marmot tests.test_delivery`.
   Unix-socket fixture binding is unavailable inside this worker sandbox.
2. Back up harness SQLite coherently and privately before deploying source. Store
   initialization adds the bounded `outbox_reactions` table; existing outbox data
   and run/session records are preserved. Integrate reviewed source and restart
   the supervisor/delivery process to load it. Resume any restarted manager by
   exact native identity. Keep the persistent worker and task claim.
3. Verify the installed wn-agent supports the existing reaction operation using
   an explicitly authorized group/message, then enqueue through the harness:

   ```sh
   workstream react RUN --message-id EXACT_MESSAGE_ID --emoji '👀' --key STABLE_REQUEST_KEY
   ```

   Repeat unchanged: expect one local outbox/payload pair and the same event ID.
   Confirm durable acknowledgement and the actual reaction in Marmot. Repeating
   a delivered key must not publish again. Changing content under that key fails.
   Record service state, message acknowledgement and actual verification evidence.
4. If the running connector lacks source-supported operations, have the operator
   review its binary/version before deployment. Any rebuild touching shared
   Marmot home requires restarting `wn-agent-hermes.service` with the matching
   binary per its runbook. The worker does not restart that service or claim
   source inspection proves binary compatibility.

Reaction retries retain their original account/group/target/content. Account drift
or missing payload fails visibly. Pending intents seek an active reaction; do not
remove that same reaction externally before resolving an uncertain outstanding
intent. No removal/toggle operation or automatic inbound-reaction policy is added.

A rollback must drain or reconcile pending reaction intents before reverting the
supervisor: old delivery code would otherwise treat reaction outbox rows as text.
Never downgrade with pending reaction rows. Retain the private database backup;
do not blindly restore it over new messages or session changes.

Execution evidence and completion are recorded only after operator deployment and
live verification. Fixture outcomes are not claims of a live reaction or reboot.
