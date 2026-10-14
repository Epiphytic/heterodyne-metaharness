# btq-y3e88: the admind test flakes that fail CI under load

Base: main 093ede5.

Files involved:
- `tests/test_admind_daemon.py` (`Harness`, `run_with`)
- `tests/admind_asks_fixture.py` (`sent_mid`)
- `tests/test_admind_r13_replies.py`
- `tests/test_admind_asks_bump.py`
- `tests/fakes/fake_wn_agent.py`
- for reference only: `src/heterodyne/admind/daemon.py` (`outbox_pass`, `autobump`, `check_group`)

**Verdict:** both flakes are races in the tests, and so is a latent hang the investigation found (root cause 3). Production behaves as specified in all three cases, and this design changes no production code. The tests treat a state as reached before the daemon has reached it. Each fix waits on the daemon's own record of that state. No assertion is removed or weakened.

## The two flakes

| # | Test | Seen | Failure |
|---|---|---|---|
| 1 | `test_admind_asks_bump.py::test_a_pending_reminder_blocks_until_it_fails_and_a_restart_keeps_that` | PR #67 job 115524643780, #71 job 115554104861, #72 job 115568467092 | `assert [] == ['asknote:<id>:autobump:1:0']` at line 1341, logged with an `asyncio` `ConnectionResetError` in `client_connected_cb` |
| 2 | `test_admind_r13_replies.py::test_a_batch_that_cannot_be_delivered_is_reopened_and_resent` | PR #77 run 38485104771 attempt 1, job 115596739124; again on PR #78 | `assert 'pending' == 'sent'` at line 470 |

Each failed alone in a full run on the self-hosted runner (1 failed, about 4,000 passed, 11.5–12 min). Each passed on rerun.

## Root cause 2: "the fake listed it" is not "the daemon recorded it"

**Mechanism.** `FakeWnAgent._send_final` appends the request to `fake.sent` (`fake_wn_agent.py:191`) and then awaits `_reply` (`:196`). The daemon only marks the outbox row `sent` and records its message ID (`daemon.py:2827`, `store.mark_sent`) after it reads that reply (`outbox_pass`, `:2811`). Between those two points, the send is in `fake.sent` but its row is still `pending`.

`h.texts()`, `batches()` and `sent_mid()` all read `fake.sent`. A test that waits on one of them and then reads the daemon's state can run inside that window. `Harness.until` polls every 50 ms. On a loaded runner the poll's timer is already overdue when the loop reaches it, so it fires before the daemon's reader callback.

Flake 2 is exactly this:
1. `batch_arrives` waits for the batch to show up in `fake.sent`.
2. `outbox_status(h, "batch:1.1")` reads the row the daemon has not yet marked.

The suite already knows about this window in two places:
- `test_admind_r13_replies.batch_message_id` documents it: "The fake lists a send before it replies, so `batch_arrives` can return while the outbox row has no message ID yet."
- `test_admind_r13_details.summary_delivered` waits for both the fake's list and the `sent` row.

Neither fix was made general.

**Evidence.**
- *Reproduced on demand.* A probe plugin added only one thing: it awaited 0.3 s inside the fake's `_reply` before writing a `final_sent`. With it, flake 2 fails every run with the CI assertion (`assert 'pending' == 'sent'`, line 470). With `await waited(h, lambda: outbox_status(h, "batch:1.1") == "sent", …)` in place of the direct assert, it passes under the same delay.
- *The same pattern elsewhere.* The same 0.3 s delay across all of `tests/test_admind_*.py` gave **45 failed, 1,336 passed**. Most failures are immediate wrong answers, not timeouts. For example, `'' == "That was a reminder…"`: `bumped()` returns the reminder's ID from `sent_mid()` (a `fake.sent` read), and the test reacts to that ID. The daemon's `bump_for_message` correctly ignores a message whose row is not yet `sent` (B6: "pending or failed rows never match"). So the reaction gets no hint.
- *One helper explains most of it.* I then patched `sent_mid` alone, through the plugin, to return the fake's ID only once the daemon's row is `sent` with that same ID. With the 0.3 s delay still on, 40 of the 45 then passed.
- *The other five are the same race.* They are flake 2 itself, and four tests that wait for a summary or batch in `h.texts()` and then send `!details`:
  - `test_admind_r13_details.py::test_details_full_on_a_turn_without_a_span`
  - `test_admind_r13_details.py::test_an_alert_is_delivered_while_a_large_details_is_still_being_staged`
  - `test_admind_au11.py::test_login_dirs_never_leave_through_replies_tail_details_summaries_or_audit`
  - `test_admind_r13_replies.py::test_the_batch_keeps_the_order_replies_reach_the_backstop`
  
  `!details` with no target looks up the latest *sent* summary or batch (`store.py:571–575`). These tests make 2–5 sends each and normally take 0.18–3.7 s. Three of the four still fail at a 0.05 s delay, so the cause is not send volume.

## Root cause 1: the restarted scenario runs before the outbound gate opens

**Mechanism.**
- `run_with` starts the scenario as soon as `fake.wait_subscribed()` sees a subscriber (`test_admind_daemon.py:117`). That happens when the fake *receives* the subscribe request, before it acks.
- The daemon's outbound gate opens one group-info round trip later. `may_post()` (`daemon.py:476`) needs `observing`. `observing` is set only after the ack sets `acked` and a following `check_group()` passes (`daemon.py:1506–1508`, `1758–1759`).
- `autobump()` returns without doing anything or writing an audit record when `may_post()` is False (`daemon.py:938`). That is B14 working as designed: "The gate closed defers the whole check and moves no clock". The loop runs it again later.
- The test's `restarted` scenario calls `check(h)` first thing. Inside that window it is a no-op, so `auto_rows == []`.

**Evidence.**
- *Seen under contention.* A probe recorded the gate state on each `autobump()` call. I ran 20 loops of this test pinned to CPU 0 next to three busy loops on CPU 0. All passed. But in iteration 16, a `check` ran with `may_post=False, observing=False, group_ok=True`. That was the third run (`again`), whose assertion `len(auto_rows) == 1` passed *vacuously*, because nothing was checked.
- *Reproduced on demand.* A plugin held only the group-info request that follows a subscription, for 0.5 s, from the test's second daemon run on. With it, the test fails every run with the CI assertion (`assert [] == ['asknote:<id>:autobump:1:0']`, line 1341). Adding `await h.until(h.daemon.may_post)` at the start of `restarted` and `again` makes it pass under the same delay.
- *No other site in this file.* An awk scan of `test_admind_asks_bump.py` lists every `await check(h)`. Only those two scenarios make the call before anything that implies the gate is open (`joined`, `card`, `posted`, a wait).

**The `ConnectionResetError` is a symptom, not the cause.**
- `first_run` ends with `gate.set()`. The fake's gated `send_final` then resumes while `run_with` is cancelling the daemon. Its reply's `drain()` hits the closed connection, and asyncio logs the error from `client_connected_cb`.
- It goes with the failure because both come from a slow runner. It also means the reminder row reaches the restart as `pending` some of the time and `sent` the rest. The test then overwrites the row to `failed`, so that variation doesn't change the assertion. It does make the restart's starting state nondeterministic.

## Root cause 3 (latent, found by the sweep): `no_sleep` never yields

This one has not failed CI yet. When it does, it shows as a hung job, not a failed assertion.

- `test_admind_r13_replies.no_sleep` is `return None`. Three tests set `h.daemon._sleep = no_sleep` to skip the send-retry backoff: the backstop summary test (line 436), `fail_first_batch` (line 453) and the order test (line 686). That seam is also the poll sleep of `alerts_loop` (`daemon.py:2854`) and of the agent poll (`:1434`).
- `relay_alerts` returns without an await while `may_post()` is False (`:2861`). With the gate closed, `alerts_loop` becomes `while True: await no_sleep(); relay_alerts()`, and neither call ever suspends. The event loop never runs again, so the gate can't open, and the test's own `asyncio.wait_for` can't fire either.
- Today the gate is usually open before the scenario sets `_sleep`, and `relay_alerts` then yields in `asyncio.to_thread`. A slow subscription ack (root cause 1) closes that window.

**Evidence.**
- With the post-subscribe `group_info` held for 0.5 s on every run, the backstop test hangs every time. It passes alone in 1.5 s, and the run was killed by `timeout` at 60–90 s. The faulthandler dump (`-o faulthandler_timeout=15`) shows the main thread in `alerts_loop` (`daemon.py:2855`) → `relay_alerts` (`:2861`) → `may_post` → `latched` → `store.get`, under `supervised`/`guarded`.
- With `no_sleep` changed to `await asyncio.sleep(0)`, the three `no_sleep` tests pass 3 of 3 runs under the same delay (about 9 s for the three).
- *Whole-suite sweep.* With that change and the 0.5 s hold on every daemon run, all 38 `tests/test_admind*.py` files ran: **1 failed, 1380 passed** in 7 min 56 s. The one failure is flake 1 at line 1341, which that run did not fix. Nothing else in the admind suite depends on the gate opening quickly.

**Not a production bug.** `alert_poll_seconds` must be more than 0 (`settings._seconds`), so the real `asyncio.sleep` always suspends. The spin exists only with a test sleep that does not yield. The other test fakes of `_sleep` already yield (`no_wait` in `test_admind_t9r2.py:65` and `test_admind_t9r9.py:188`), or are bounded (`test_admind_r1.py:435` cancels after 8 calls).

## Production: no change

- The daemon ignoring a reply or reaction to a message whose row is not yet `sent` is the specified fail-closed rule (R7, B6, P6: pending rows never count). Before `final_sent`, the daemon cannot know the message ID is its own.
  - In production, the window is one local socket round trip between wn-agent publishing and admind recording. It only matters if an operator reacts within that time. Covering it would need a design change to the ID flow, not a test fix. It is out of scope and noted under Risks.
- `autobump()` deferring while the gate is closed is B14 as written.

## The fix (tests only)

1. **`Harness.recorded(key) -> str | None`** in `test_admind_daemon.py`. It returns the message ID the fake returned for the send with idempotency key `key`, but only once the daemon's outbox row for `key` is `sent` *with that same message ID*. Otherwise it returns None.
   - `admind_asks_fixture.sent_mid` becomes `h.recorded(key)`, with its docstring changed to say so.
2. **Negative checks keep the raw list.** A test that asserts something was *not* sent must not get weaker. The two `stays(lambda: sent_mid(...) is None)` calls (`test_admind_approvals.py:1262`, `test_admind_asks_bump.py:1284`) switch to a new `listed_mid(h, key)`, which is today's `fake.sent` read. So does any other negative `sent_mid` use the implementation finds.
   - `h.texts()` stays the raw fake list. Its 11 `not in h.texts()` uses keep their full strength.
3. **`Harness.delivered_texts()`.** It returns the texts from `fake.sent` whose idempotency key's outbox row is `sent` with the fake's message ID, in send order.
   - `test_admind_r13_replies.batches()` **stays raw** (it reads `fake.sent`). Its exact-count and absence checks keep their full strength: `len(batches(h)) == 1` at lines 298, 314 and 328, and `batches(h) == []` at line 364. A filtered `batches()` would let a wrongly published batch whose ack is late or lost slip past them.
   - A new `delivered_batches(h)` is built on `delivered_texts()`. `batch_arrives` (also used by `test_admind_au11.py:417`) waits on and returns from `delivered_batches()`, so it returns only once the daemon has recorded the batch. Flake 2's line 470 assertion then stays exactly as written.
   - `batch_arrives` is the only positive use of `batches()`. Every other use is a count or absence check and stays on the raw helper.
   - Positive waits that go on to depend on the daemon's record use it. These are the four `!details` tests above, plus any that the sweep in step 7 finds.
   - Positive waits on texts with no daemon-state step after them may stay on `h.texts()`. The rule goes into `Harness`'s docstring: **wait on `delivered_texts()` or `recorded()` before acting on the daemon's state; check absence on `texts()` or `listed_mid()`.**
4. **Gate readiness.** Add `async def posting(h)` to `test_admind_daemon.py`: `await h.until(h.daemon.may_post, 20)`, failing with the gate's fields (`latched`, `group_ok`, `observing`, `changing`, operator seen) on a timeout.
   - Flake 1's `restarted` and `again` call it before `check(h)`. This also makes `again`'s "no second bump" assertion meaningful, where today it can pass vacuously.
   - `run_with` itself does not wait for the gate. Some tests start with it closed on purpose (latched, unconfirmed membership).
5. **A deterministic restart state for flake 1.** After `gate.set()`, `first_run` waits until every `asknote:<id>:bump:` row is `sent`. The restart then always starts from a delivered reminder, and the teardown `ConnectionResetError` goes away. `restarted` still marks those rows `failed`, as now. That is what the test checks: a failed reminder no longer blocks, across a restart.
6. **Knobs on the fake, so the races stay reproducible.** These replace the probe plugins.
   - `FakeWnAgent.reply_delay: float = 0.0`: seconds slept inside `_send_final` after the send is listed and before the `final_sent` reply.
   - `FakeWnAgent.ack_check_delay: float = 0.0`: holds the first `group_info` after each subscription by that long.
   - `Harness` reads both from `HZ_FAKE_WN_REPLY_DELAY` and `HZ_FAKE_WN_CHECK_DELAY`, default 0.
   - Three fast regression tests (the knob tests) run the two flake scenarios with the knobs set (0.3 s and 0.5 s), and the root cause 3 scenario with the check delay (below). The fixed tests must pass with the knobs set. These knob tests are the deterministic proof the fix holds. The unchanged tests, with the fix reverted, must fail them; the implementation PR shows that once, as in the repro.
7. **Acceptance sweep.**
   - The whole admind suite passes with `HZ_FAKE_WN_REPLY_DELAY=0.3`, and separately with `HZ_FAKE_WN_CHECK_DELAY=0.5`. The implementation records both results in its PR.
   - These sweeps are not added to per-PR CI. They add about 17 minutes each. They are a documented command in `tests/README` or the test docstring, for whoever touches the admind harness. A nightly job is a possible follow-up, not part of this design.
8. **`no_sleep` yields.** Its body becomes `await asyncio.sleep(0)`, matching `no_wait` in `test_admind_t9r2.py`. It still skips every delay, so the tests keep their speed and their assertions, but each poll loop now gives the event loop a turn.
   - **Permanent regression, supervised from outside.** A hang blocks the event loop, so no in-process timeout can catch it. The third knob test runs `test_a_summary_that_cannot_be_delivered_goes_to_the_backstop` in a child `python -m pytest` with `HZ_FAKE_WN_CHECK_DELAY=0.5` and `-o faulthandler_timeout=30`. It starts the child with `start_new_session=True` and waits up to 90 s. On expiry it kills the child's process group with `os.killpg`, so the daemon and fake go too. A timeout or a non-zero exit fails the test, with the child's output (and faulthandler dump) in the message. Reverting only the `no_sleep` fix makes it time out, and the implementation PR shows that once.

## Tests

- The three knob tests from steps 6 and 8. Each fails (or, for the third, times out) on today's tests and passes on the fixed ones.
- A unit test of `Harness.recorded` and `delivered_texts`, run against a live harness:
  - A send held in the fake by `reply_delay` is listed by `texts()`/`listed_mid()`, but not by `recorded()`/`delivered_texts()`, until `mark_sent` runs.
  - A row the daemon marks `failed` is never `recorded()`.
- The three `no_sleep` tests pass with `HZ_FAKE_WN_CHECK_DELAY=0.5`. Today they hang there. They are covered by the step 7 check-delay sweep, so no new test is needed.
- The full offline suite stays green. That suite runs with no knobs set, so that run covers no timing.

## Risks

- **Stricter positive waits can time out** where a test relied on seeing a send the daemon never records. An example would be a test that kills the daemon between the send and the record. Those tests must use the raw `texts()`/`listed_mid()` on purpose; the full suite and the sweeps find them. Stricter waits never make a test pass that should fail.
- **Converting the 60 `sent_mid` and 138 `h.texts()` uses one by one is error-prone.** That's why the change sits in the helpers, and why only negative checks are moved by hand (2 + 11 found, all reviewed). The reply-delay sweep is the check that nothing positive was missed.
- **A production window remains.** An operator reaction that arrives before admind records `final_sent` is ignored, as specified. Not changed here; worth a separate bead if anyone wants replies to in-flight sends to count.
- **Other busy loops behind a test seam.** Root cause 3 can come back wherever a test replaces a sleep with one that doesn't yield. The `Harness` docstring gets the rule: **a fake sleep must await something.** The check-delay sweep is what would find a new one (as a hang, so run it under `timeout` with `-o faulthandler_timeout`).

## Out of scope: a third CI flake

`tests/test_session_git.py::test_snapshot_refuses_links_special_files_and_hard_links[fifo]` failed on PR #67, job 115520595610, with `FileExistsError: …/private/objects/cd`.

- The `fifo` plant runs `mkdir()` on a fixed fan-out directory `cd/`, and the `object-dir-link` plant symlinks a fixed `ab/`.
- The session commit's hash depends on its timestamp, so its fan-out directory is random. About 1 in 256 runs per plant, it is `cd` (or `ab`).
- Not load-related.
- Fix: plant into a fan-out directory chosen to be absent from `objects/`.
- Recommended as its own bead. Folding it into this one is the reviewer's call.
