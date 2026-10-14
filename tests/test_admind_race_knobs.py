"""The admind harness races of btq-y3e88, made deterministic with the fake wn-agent's timing knobs.

Each knob test runs a test that once flaked under load, with the knob that reproduces its race:
- `reply_delay` (`HZ_FAKE_WN_REPLY_DELAY`) holds `final_sent` after the fake has listed a send, so the
  fake's list runs ahead of the daemon's outbox record.
- `ack_check_delay` (`HZ_FAKE_WN_CHECK_DELAY`) holds the first `group_info` after a subscription, so the
  outbound gate opens late.

The acceptance sweeps run the whole admind suite with each knob set (about 17 minutes each; not in CI):

    HZ_FAKE_WN_REPLY_DELAY=0.3 timeout 3600 uv run pytest -q tests/test_admind*.py
    HZ_FAKE_WN_CHECK_DELAY=0.5 timeout 3600 uv run pytest -q -o faulthandler_timeout=120 tests/test_admind*.py

A check-delay hang is a fake sleep that never yields; the faulthandler dump shows where it spins.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import test_admind_asks_bump as asks_bump
import test_admind_r13_replies as r13_replies
from admind_asks_fixture import listed_mid
from test_admind_daemon import CHECK_DELAY_ENV, REPLY_DELAY_ENV, Harness, needs_tmux, run_with
from test_admind_r13_replies import outbox_status

REPO = Path(__file__).resolve().parent.parent
CHILD_TIMEOUT = 90


@needs_tmux
def test_recorded_and_delivered_texts_wait_for_the_daemons_record(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.until(lambda: not h.store.pending())    # the ready notice is out of the way
        h.fake.reply_delay = 2.0
        await h.say("hello there")
        await h.until(lambda: "echo: hello there" in h.texts())
        key = next(r["idempotency_key"] for r in h.fake.sent if r["text"] == "echo: hello there")
        mid = listed_mid(h, key)
        assert mid is not None
        assert outbox_status(h, key) == "pending"       # held: the fake has not replied yet
        assert h.recorded(key) is None
        assert "echo: hello there" not in h.delivered_texts()
        await h.until(lambda: outbox_status(h, key) == "sent")
        assert h.recorded(key) == mid
        assert "echo: hello there" in h.delivered_texts()
        h.store.db.execute("UPDATE outbox SET status = 'failed' WHERE key = ?", (key,))
        assert h.recorded(key) is None                  # the message ID still matches; the status does not
        assert "echo: hello there" not in h.delivered_texts()
        assert listed_mid(h, key) == mid

    run_with(tmp_path, scenario)


@needs_tmux
def test_a_resent_batch_is_recorded_before_it_is_checked_with_a_slow_reply(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REPLY_DELAY_ENV, "0.3")
    r13_replies.test_a_batch_that_cannot_be_delivered_is_reopened_and_resent(tmp_path)


@needs_tmux
def test_a_restarted_reminder_check_waits_for_a_slow_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CHECK_DELAY_ENV, "0.5")
    asks_bump.test_a_pending_reminder_blocks_until_it_fails_and_a_restart_keeps_that(tmp_path, monkeypatch)


@needs_tmux
def test_a_fake_sleep_does_not_spin_while_the_gate_is_closed() -> None:
    """A spin blocks the event loop, so no timeout inside the test process can end it: the test runs in
    a child pytest, in its own process group, which is killed if it outlives CHILD_TIMEOUT."""
    test = "tests/test_admind_r13_replies.py::test_a_summary_that_cannot_be_delivered_goes_to_the_backstop"
    env = {**os.environ, CHECK_DELAY_ENV: "0.5"}
    env.pop(REPLY_DELAY_ENV, None)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "faulthandler_timeout=30",
            test]
    child = subprocess.Popen(argv, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, start_new_session=True)
    try:
        out, _ = child.communicate(timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        out, _ = child.communicate()
        pytest.fail(f"the child run did not finish in {CHILD_TIMEOUT} s\n{out}", pytrace=False)
    assert child.returncode == 0, out
    assert "1 passed" in out, out
