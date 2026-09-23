# On-demand channel status

Authority: [entrypoint](README.md), [workflow progress](workflow-routing.md),
[terminal observation](terminal-observation.md), [transition delivery](transitions.md).

An authenticated Marmot `/status` is intercepted before workstream inbox routing
and gateway session/model dispatch. It never launches a model or changes a task,
checkpoint, observation, queue position, approval, or native session. Unauthorized
status commands are consumed without disclosing state. Arguments receive usage;
channel identity, not user-supplied workstream names, selects the view. Non-status
messages retain their existing routing. Unknown channels list active workstream
names and terminal runs with nonclosed cached routed Beads; ambiguous ownership returns unavailable, never another run's pane.

The renderer opens harness SQLite in read-only mode and one read transaction.
It reads at most 200 runs (including terminal runs with open cached tasks) and
5000 cached task projections, reusing exact route validation,
queue ordering and workflow classification. It reports in-flight tasks, pending
order with blockers, and counts of latest recorded lifecycle positions (closed
is done; close-ready is deployed). Counts describe cached tasks, not all historical
native Beads. Missing cached dependencies are blocked/unknown, never claim authority.
No Beads subprocess, live queue refresh, config/credential read or model is allowed.

Terminal reads use the dedicated hermes-workstreams tmux socket. Verify exact
session ownership and immutable pane membership before capture. Return the last
25 non-trailing-empty lines of capture including scrollback, labeled with run ID,
pane state and UTC capture time. No signal, keypress, pane inspection repair or
process launch beyond the read-only tmux commands is permitted. Missing/dead panes
are reported honestly. Output is byte-identical for identical snapshot, capture
and supplied timestamp; separate live captures have their own capture timestamps.

SQLite has a one-second query deadline and 0.2-second lock timeout; tmux reads
share two seconds. The gateway subprocess has a 3.5-second deadline and is killed
and reaped on timeout/cancellation. Reply RPC is bounded to one additional second;
these budgets target under five seconds on a normally scheduled host, not a real-time
OS guarantee. UTF-8 output is at most 6000 bytes, with bounded queue sections and
explicit local follow-up hints. No silent truncation of queue or pane output.

The direct gateway connector response uses an idempotency key derived from account,
group and incoming message ID, with no facade retry or alternate-key recreation.
Transport failure stays a failed command, never a model fallback or success claim.
The query itself has no side effects; its explicitly requested chat reply is the
only write. Existing gateway event replay/dedup remains authoritative. A timeout
can leave delivery uncertain; no exactly-once delivery guarantee is inferred.

Install only through the guarded adapter plan and private verified backup workflow.
The exact pre-routing anchor is required; mismatch fails before mutation. Reload the
gateway after reviewed installation, preserve sessions, and verify an authenticated
live `/status` before claiming deployment. No upstream Hermes core edit is needed.
