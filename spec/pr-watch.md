# PR-watch reconciliation

Authority: [signed blockers](blockers.md), [delivery groups](delivery-tasks.md),
[recovery](sessions.md).

A pr-review gate is the non-executable watch Bead. Assign it to a registered agent
route to dispatch the initial review work. `task RUN blocker watch GATE --file FILE`
attaches an immutable `{adapter,repository,review_id,head}` source; head must match
the gate revision. Watch metadata persists in the existing run checkpoint.

Configured adapters specify executable argv and authorized reviewer identities.
The adapter receives JSON source on stdin and returns a bounded JSON object with
that exact source and state pending, approved, or changes-requested. Approved
requires full signed gate evidence. Changes-requested requires at most 100 comments
with immutable id, author and body. The adapter must authenticate review authors at
the provider boundary. Transport success and unsigned approval text are not consent.
There is no default provider adapter or inferred executable from task data.

The supervisor's regular tick is the hourly cron backstop: it persists next_check
before I/O and reconciles at most once per hour, even if the watching agent exited.
It does not wait on a model, reclaim its task or launch replacements. A timeout,
invalid response or offline adapter retains a visible checkpoint error and schedules
one later bounded retry. Stopped/recovery-held runs and disabled blockers do not run
this backstop. Successful resolution is not repeated.

Authorized comments create stable ordinary fix Beads through existing admission,
then block the review parent on those fixes. Changed content under one comment
identity fails closed. Fixes retain normal code/test/review lifecycle. Adding a fix
changes parent scope: create an explicitly superseding gate for the next reviewed
head. Old comments and watch evidence are retained. Closure and deployment remain
subject to existing formula, operator and retained evidence requirements.
