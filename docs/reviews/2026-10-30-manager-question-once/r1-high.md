The review skill returned 9 unverified findings. Before giving a verdict I'll check the ones that matter against the frozen source.

Pins and head match. Now the PR diff for the source files:

Now `post_ask`, to check the nested-transaction rollback and audit-ordering findings:

No code deletes asks, so a "missing retained ask" only happens if the store is damaged. Next, the wire check and the peer gating ahead of dispatch:

I need to see the end of `post_ask`. If it can return `"posted"` without `ask`, the ask commits with no binding and a retry would post it again. I'll also check for tag collisions:

I've now checked every finding directly. One last step: run the new test file so the verdict doesn't rest only on the PR's own `tests.log`.

**Verdict: approved.** PR 152 at `ce60c5e` (base `a3da7b0`) has no blocking correctness, concurrency, crash-durability, scope or compatibility problems. Two low-severity issues are worth fixing later. The head and all four source/test hashes match your pins, and I ran `tests/test_admind_question_once.py` myself: 9 passed. Nothing was edited, posted or started.

**What holds up:**
- **Atomic binding:** `post_question_once` takes the post lock, then the outer `BEGIN IMMEDIATE`. The key lookup, `post_ask` (including the 100-unanswered check, the ask insert and the outbox rows) and `bind_question` all join one transaction. A crash before COMMIT rolls back all of it.
- **Bound ask ID:** a `"posted"` reply always carries `ask`, so a committed ask always gets its binding.
- **Refused posts:** a refused post (latched, hourly limit, capacity) writes no binding, so a retry can post later.
- **Replay order:** exact replay returns the stored ask ID before the latch, hourly and capacity checks. Changed content, or a missing or non-question stored ask, is refused. No code deletes asks, so "missing" only happens if the store is damaged.
- **Scope:** the key must be `sandbox-manager:<canonical lowercase UUID>:<1–128 chars of [A-Za-z0-9._-]>`. URN and braced UUID forms fail the round-trip check. The post must be an ordinary question with no bead, PR, presentation or management fields. The `question.once.v1` tag is unique across the socket's request types.
- **Compatibility:** the new table is `CREATE TABLE IF NOT EXISTS` in the startup schema, so existing databases pick it up. Existing request types are untouched.

**Findings (none blocking):**

1. **Low – false "posted" audit record** (`daemon.py:1479`). Because `post_ask` now runs inside the outer transaction, it writes `action=posted` before `bind_question` and COMMIT. If either fails (a sqlite I/O error, a full disk), the ask rolls back but the audit says it was posted. Fix: move the posted audit to after the outer commit, or have `post_question_once` write a correcting record on failure.
2. **Low – lost inner rollback** (`daemon.py:1465`). `post_ask`'s catch of `asks.management.Refusal` after `insert_ask` relied on its own transaction rolling back. Nested, that block only joins the outer one, so an orphan ask row would be committed. It can't happen today because `question_once_check` rejects presentation and management. It is still a trap for anyone who widens the scope later.
3. **Low – stored content tied to today's `AskPost` layout** (`asks.py:120`). `msgspec.json.encode(req.post)` writes out every field. If admind is upgraded with a new field on `AskPost` between the original post and a retry, the retry is refused as "different content". That refuses rather than double-posting, but the lost-ack recovery fails across that upgrade.
4. **Info – replays look like fresh posts.** An exact replay returns `result="posted"` whatever the ask's status. It also isn't audited separately, and the audit record doesn't include the key or run ID. Callers have to read `ask.summary.status`, and an operator can't trace a conflict back to its run.
5. **Info – binding isn't tied to the peer.** Any `ask.sock` client that knows a run UUID could claim its key first. The design leaves ownership to the Hermes bridge, so this is accepted, but worth stating.
6. **Cleanup:**
   - The readback after INSERT in `bind_question` can never fail.
   - The full `request_json` is stored and compared alongside its SHA-256, and those rows are never capped.
   - Each request is validated and hashed twice, once at the socket and again in the handler.

On quality: the exit-2 rows are existing-symbol duplication, churn and class growth, plus `AskQuestionOnce` flagged as dead code even though it is decoded and dispatched. None of them is a correctness problem.

**Verdict: approved.** PR 152 at `ce60c5e` (base `a3da7b0`) has no blocking correctness, concurrency, crash-durability, scope or compatibility problems. Two low-severity issues are worth fixing later. The head and all four source/test hashes match your pins, and I ran `tests/test_admind_question_once.py` myself: 9 passed. Nothing was edited, posted or started.

**What holds up:**
- **Atomic binding:** `post_question_once` takes the post lock, then the outer `BEGIN IMMEDIATE`. The key lookup, `post_ask` (including the 100-unanswered check, the ask insert and the outbox rows) and `bind_question` all join one transaction. A crash before COMMIT rolls back all of it.
- **Bound ask ID:** a `"posted"` reply always carries `ask`, so a committed ask always gets its binding.
- **Refused posts:** a refused post (latched, hourly limit, capacity) writes no binding, so a retry can post later.
- **Replay order:** exact replay returns the stored ask ID before the latch, hourly and capacity checks. Changed content, or a missing or non-question stored ask, is refused. No code deletes asks, so "missing" only happens if the store is damaged.
- **Scope:** the key must be `sandbox-manager:<canonical lowercase UUID>:<1–128 chars of [A-Za-z0-9._-]>`. URN and braced UUID forms fail the round-trip check. The post must be an ordinary question with no bead, PR, presentation or management fields. The `question.once.v1` tag is unique across the socket's request types.
- **Compatibility:** the new table is `CREATE TABLE IF NOT EXISTS` in the startup schema, so existing databases pick it up. Existing request types are untouched.

**Findings (none blocking):**

1. **Low – false "posted" audit record** (`daemon.py:1479`). Because `post_ask` now runs inside the outer transaction, it writes `action=posted` before `bind_question` and COMMIT. If either fails (a sqlite I/O error, a full disk), the ask rolls back but the audit says it was posted. Fix: move the posted audit to after the outer commit, or have `post_question_once` write a correcting record on failure.
2. **Low – lost inner rollback** (`daemon.py:1465`). `post_ask`'s catch of `asks.management.Refusal` after `insert_ask` relied on its own transaction rolling back. Nested, that block only joins the outer one, so an orphan ask row would be committed. It can't happen today because `question_once_check` rejects presentation and management. It is still a trap for anyone who widens the scope later.
3. **Low – stored content tied to today's `AskPost` layout** (`asks.py:120`). `msgspec.json.encode(req.post)` writes out every field. If admind is upgraded with a new field on `AskPost` between the original post and a retry, the retry is refused as "different content". That refuses rather than double-posting, but the lost-ack recovery fails across that upgrade.
4. **Info – replays look like fresh posts.** An exact replay returns `result="posted"` whatever the ask's status. It also isn't audited separately, and the audit record doesn't include the key or run ID. Callers have to read `ask.summary.status`, and an operator can't trace a conflict back to its run.
5. **Info – binding isn't tied to the peer.** Any `ask.sock` client that knows a run UUID could claim its key first. The design leaves ownership to the Hermes bridge, so this is accepted, but worth stating.
6. **Cleanup:**
   - The readback after INSERT in `bind_question` can never fail.
   - The full `request_json` is stored and compared alongside its SHA-256, and those rows are never capped.
   - Each request is validated and hashed twice, once at the socket and again in the handler.

On quality: the exit-2 rows are existing-symbol duplication, churn and class growth, plus `AskQuestionOnce` flagged as dead code even though it is decoded and dispatched. None of them is a correctness problem.
