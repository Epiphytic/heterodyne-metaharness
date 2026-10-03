import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from admind_waits import hold, lock_waiters, stays, wait_until
from fakes.fake_wn_agent import ACCOUNT
from fakes.settings import OPERATOR_HEX, SECOND_HEX, operator
from test_admind_daemon import Harness, needs_tmux, run_with

from heterodyne.admind.daemon import applied, reconcile
from heterodyne.admind.membership import Pending, settle
from heterodyne.config import ConfigError
from heterodyne.tmux import TmuxError

P = Pending("add", "b", "d4" * 32, 2, 3, "2026-10-02T00:00:00+00:00", "c1" * 16)


@pytest.mark.parametrize(("reported", "count", "outcome"), [
    ("ok", 3, "commit"),
    ("ok", 2, "latch"),            # success reported, count disagrees
    ("ok", None, "latch"),         # count unreadable
    ("failed", 2, "abort"),
    ("failed", 3, "latch"),        # failure reported, but the change happened
    ("failed", None, "latch"),
    ("unknown", 2, "latch"),       # lost reply: count alone never confirms
    ("unknown", 3, "latch"),
])
def test_settle(reported: str, count: int | None, outcome: str) -> None:
    assert settle(reported, count, P) == outcome  # type: ignore[arg-type]


def test_pending_round_trips() -> None:
    assert Pending.load(P.dump()) == P


def test_a_record_without_a_change_id_still_loads() -> None:
    raw = json.loads(P.dump())
    del raw["change_id"]
    assert Pending.load(json.dumps(raw)) == Pending("add", "b", "d4" * 32, 2, 3, "2026-10-02T00:00:00+00:00")


def test_applied() -> None:
    assert applied({"x"}, P) == {"x", P.member_hex}
    remove = Pending("remove", "b", P.member_hex, 3, 2, P.started)
    assert applied({"x", P.member_hex}, remove) == {"x"}


POLICY = (operator("op", OPERATOR_HEX), operator("b", SECOND_HEX))


BOTH_KEYS = {OPERATOR_HEX, SECOND_HEX}
ADD_B = Pending("add", "b", SECOND_HEX, 2, 3, P.started).dump()


@pytest.mark.parametrize(("count", "confirmed", "pending", "expected"), [
    (2, {OPERATOR_HEX}, None, ({OPERATOR_HEX}, "confirmed")),
    (3, {OPERATOR_HEX}, ADD_B, (BOTH_KEYS, "confirmed")),       # the add took effect
    (3, None, None, (BOTH_KEYS, "policy")),
    (2, None, None, (None, "0")),
    (4, {OPERATOR_HEX}, None, (None, "1")),
])
def test_reconcile(count: int, confirmed: set[str] | None, pending: str | None,
                   expected: tuple[set[str] | None, str]) -> None:
    assert reconcile(count, confirmed, pending, POLICY) == expected


def test_reconcile_ignores_a_pending_change_that_did_not_happen() -> None:
    pending = Pending("add", "b", SECOND_HEX, 2, 3, P.started).dump()
    assert reconcile(2, {OPERATOR_HEX}, pending, POLICY) == ({OPERATOR_HEX}, "confirmed")


def test_reconcile_drops_a_key_that_left_policy() -> None:
    assert reconcile(2, {OPERATOR_HEX, SECOND_HEX}, None, POLICY[:1]) == ({OPERATOR_HEX}, "confirmed")


# --- the daemon -----------------------------------------------------------------------------------
SETTINGS = {"operators": POLICY, "group_check_seconds": 60}
BOTH = json.dumps(sorted([OPERATOR_HEX, SECOND_HEX]))
SECOND_IN = {"type": "group_state_changed", "account_id_hex": ACCOUNT, "group_id_hex": "b2" * 32}


def state(*, members: int = 2, keys: tuple[str, ...] = (OPERATOR_HEX,)) -> Callable[[Harness], None]:
    def before(h: Harness) -> None:
        h.fake.member_count = members
        h.store.set("expected_members", str(members))
        h.store.set("group_operators", json.dumps(sorted(keys)))
    return before


def run(tmp_path: Path, scenario: Callable[[Harness], Awaitable[None]],
        before: Callable[[Harness], Any] | None = None, **settings: Any) -> Harness:
    return run_with(tmp_path, scenario, before or state(), {**SETTINGS, **settings})


async def join(h: Harness) -> None:
    """The operator's first message opens the outbound gate; wait for its echo and an empty outbox."""
    await h.say("hello")
    await wait_until(lambda: "echo: hello" in h.texts() and h.store.next_pending() is None)


def requested(h: Harness, kind: str) -> bool:
    return any(r.get("type") == kind for r in h.fake.requests)


def count_requests(h: Harness, *kinds: str) -> int:
    return sum(1 for r in h.fake.requests if r.get("type") in kinds)


def audit(h: Harness) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]


def member(event_change: str) -> dict[str, str]:
    return {**SECOND_IN, "change": event_change}


def add_b(h: Harness) -> "asyncio.Task[tuple[str, str]]":
    return asyncio.create_task(h.daemon.change_membership("add", "b"))


def keys(h: Harness) -> list[str]:
    return json.loads(h.store.get("group_operators") or "[]")


async def latch_by_disagreement(h: Harness) -> None:
    h.fake.membership_mode = "ok-no-count"
    assert (await h.daemon.change_membership("add", "b"))[0] == "latched"


@needs_tmux
def test_commit(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        result = await h.daemon.change_membership("add", "b")
        assert result[0] == "committed"
        assert h.store.get("expected_members") == "3" and h.store.get("membership_pending") is None
        add = next(r for r in h.fake.requests if r.get("type") == "group_member_add")
        assert add["members"] == [SECOND_HEX]
        assert [r["action"] for r in audit(h) if r["kind"] == "membership"] == ["pending", "committed"]
        await wait_until(lambda: "Operator b was added to the group." in h.texts())
        assert h.daemon.operators == {OPERATOR_HEX: "op", SECOND_HEX: "b"}
        assert keys(h) == sorted([OPERATOR_HEX, SECOND_HEX]) and not h.daemon.changing
    run(tmp_path, scenario)


@needs_tmux
def test_two_changes_in_one_second_have_distinct_notice_keys(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heterodyne.admind.daemon.now", lambda: "2026-10-03T00:00:00+00:00")

    async def scenario(h: Harness) -> None:
        await join(h)
        assert (await h.daemon.change_membership("add", "b"))[0] == "committed"
        assert (await h.daemon.change_membership("remove", "b"))[0] == "committed"
        await wait_until(lambda: len([t for t in h.texts() if t.startswith("Operator b was")]) == 2)
        notice_keys = [r["idempotency_key"] for r in h.fake.sent if r["text"].startswith("Operator b was")]
        assert len(set(notice_keys)) == 2
    run(tmp_path, scenario)


@needs_tmux
def test_abort(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = "fail"
        assert (await h.daemon.change_membership("add", "b"))[0] == "aborted"
        assert h.store.get("expected_members") == "2" and not h.daemon.latched()
        assert h.store.get("membership_pending") is None and not h.daemon.changing
        assert keys(h) == [OPERATOR_HEX]
    run(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("mode", ["ok-no-count", "fail-count"])
def test_latch_on_disagreement(tmp_path: Path, mode: str) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = mode
        assert (await h.daemon.change_membership("add", "b"))[0] == "latched"
        assert h.daemon.latched() and h.store.get("membership_pending") is not None
        assert not h.daemon.changing
    run(tmp_path, scenario)


@needs_tmux
def test_latch_on_a_lost_reply(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.timeout = 0.5
        h.fake.membership_mode = "hang"
        assert (await h.daemon.change_membership("add", "b"))[0] == "latched"
        assert h.daemon.latched() and h.store.get("membership_pending") is not None
    run(tmp_path, scenario)


@needs_tmux
def test_an_event_during_the_transition_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = "gated"
        task = add_b(h)
        await wait_until(lambda: requested(h, "group_member_add"))
        await h.fake.push_event(member("member_removed"))
        await wait_until(lambda: h.daemon.group_events == 1)
        h.fake.membership_gate.set()
        assert (await task)[0] == "latched"
    run(tmp_path, scenario)


@needs_tmux
def test_a_replaced_subscription_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = "gated"
        task = add_b(h)
        await wait_until(lambda: requested(h, "group_member_add"))
        gen = h.daemon.sub_gen
        await h.fake.drop_subscriptions()
        await wait_until(lambda: h.daemon.sub_gen != gen)
        h.fake.membership_gate.set()
        assert (await task)[0] == "latched"      # the add succeeded and the count is right (B5)
    run(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("held", ["dispatch_lock", "send_lock"])
def test_a_paste_or_send_in_progress_is_drained(tmp_path: Path, held: str) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        before = count_requests(h, "group_info", "group_member_add")
        lock: asyncio.Lock = getattr(h.daemon, held)
        await lock.acquire()
        task = add_b(h)
        await wait_until(lambda: h.daemon.changing)
        await stays(lambda: h.daemon.changing and not h.daemon.may_post() and not h.daemon.authorised()
                    and count_requests(h, "group_info", "group_member_add") == before, 0.3)
        lock.release()
        assert (await task)[0] == "committed"
    run(tmp_path, scenario)


@needs_tmux
def test_the_message_in_hand_finishes_first(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        before = count_requests(h, "group_info", "group_member_add")
        await h.daemon.work_lock.acquire()
        task = add_b(h)
        await stays(lambda: not h.daemon.changing and count_requests(h, "group_info", "group_member_add")
                    == before, 0.3)
        h.daemon.work_lock.release()
        assert (await task)[0] == "committed"
    run(tmp_path, scenario)


@needs_tmux
def test_a_message_during_a_transition_waits_and_is_then_processed(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = "gated"
        task = add_b(h)
        await wait_until(lambda: requested(h, "group_member_add"))
        mid = await h.say("hello during the change")
        await stays(lambda: mid not in h.store.inbound_with_status("dropped")
                    and "hello during the change" not in h.log.read_text(), 0.3)
        h.fake.membership_gate.set()
        assert (await task)[0] == "committed"
        await wait_until(lambda: "echo: hello during the change" in h.texts())
        assert not any("could not verify" in t for t in h.texts())
    run(tmp_path, scenario)


@needs_tmux
def test_group_checks_during_a_transition_are_deferred(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.fake.membership_mode = "gated"
        task = add_b(h)
        await wait_until(lambda: requested(h, "group_member_add"))
        assert await h.daemon.check_group() is False
        assert {"kind": "guard", "action": "group-check-deferred"} in [
            {"kind": r["kind"], "action": r.get("action")} for r in audit(h)]
        assert not h.daemon.latched() and h.daemon.group_ok
        h.fake.membership_gate.set()
        assert (await task)[0] == "committed"
    run(tmp_path, scenario)


async def boom(*_: object, **__: object) -> None:
    raise RuntimeError("boom")


def fail_latch(*_: object) -> None:
    raise OSError("disk")


@needs_tmux
def test_an_exception_after_journaling_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.group_member_add = boom     # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await h.daemon.change_membership("add", "b")
        assert (h.store.get("latched") or "").startswith("a membership change failed before it was settled")
        assert h.store.get("membership_pending") is not None and not h.daemon.changing
    run(tmp_path, scenario)


@needs_tmux
def test_if_even_the_latch_fails_the_hold_stays(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.group_member_add = boom     # type: ignore[method-assign]
        h.daemon.latch = fail_latch                 # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await h.daemon.change_membership("add", "b")
        assert h.daemon.changing and not h.daemon.may_post()
        assert h.store.get("membership_pending") is not None
        assert {"kind": "membership", "action": "held-unsettled"} in [
            {"kind": r["kind"], "action": r.get("action")} for r in audit(h)]
    run(tmp_path, scenario)


@needs_tmux
def test_after_a_failed_latch_rearm_clears_the_hold(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.group_member_add = boom     # type: ignore[method-assign]
        original = h.daemon.latch
        h.daemon.latch = fail_latch                 # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await h.daemon.change_membership("add", "b")
        h.daemon.latch = original                   # type: ignore[method-assign]
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert not h.daemon.changing and h.store.get("membership_pending") is None
        assert h.daemon.may_post()
    run(tmp_path, scenario)


@needs_tmux
def test_refusals_change_nothing(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)

        async def refused(op: str, name: str) -> None:
            result = await h.daemon.change_membership(op, name)
            assert result[0] == "refused", result
            assert not requested(h, "group_member_add") and not requested(h, "group_member_remove")
            assert h.store.get("expected_members") == "2" and not h.daemon.changing
        await refused("add", "nobody")
        await refused("remove", "nobody")
        await refused("add", "op")                      # already an operator
        await refused("remove", "op")                   # the last operator
        h.daemon.observing = False
        await refused("add", "b")
        h.daemon.observing = True
        h.daemon.load_operators = lambda: (_ for _ in ()).throw(ConfigError("bad"))  # type: ignore[assignment]
        await refused("add", "b")
        h.daemon.load_operators = lambda: POLICY
        h.store.set("membership_pending", P.dump())
        await refused("add", "b")
        h.store.delete("membership_pending")
        h.daemon.latch("test")
        await refused("add", "b")
    run(tmp_path, scenario)


@needs_tmux
def test_a_pending_record_latches_at_startup(tmp_path: Path) -> None:
    def before(h: Harness) -> None:
        state()(h)
        h.store.set("membership_pending", P.dump())

    async def scenario(h: Harness) -> None:
        assert (h.store.get("latched") or "").startswith("a membership change was interrupted")
        await h.say("hello")
        await wait_until(lambda: any(r["kind"] == "drop" for r in audit(h)))
        assert not any("echo:" in t for t in h.texts())
        assert any(r["kind"] == "guard" and r["action"] == "latch"
                   and str(r["reason"]).startswith("a membership change was interrupted") for r in audit(h))
    run(tmp_path, scenario, before)


@needs_tmux
def test_rearm_after_a_latch(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        await latch_by_disagreement(h)
        assert not h.daemon.may_post()
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert h.store.get("expected_members") == "2" and not h.daemon.latched()
        assert h.store.get("membership_pending") is None
        h.daemon.post("after", "posting resumed", None)
        await wait_until(lambda: "posting resumed" in h.texts())
    run(tmp_path, scenario)


@needs_tmux
def test_rearm_refusals(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        await latch_by_disagreement(h)
        h.fake.fail_group_info = True
        assert (await h.daemon.rearm())[0] == "refused"
        h.fake.fail_group_info = False
        h.fake.member_count = 1
        assert (await h.daemon.rearm())[0] == "refused"
        h.fake.member_count = 2
        before = count_requests(h, "group_info")
        h.daemon.reading = False
        assert (await h.daemon.rearm())[0] == "refused"
        assert count_requests(h, "group_info") == before
        h.daemon.reading = True
        assert h.daemon.latched() and h.store.get("membership_pending") is not None
    run(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize("how", ["event", "resubscribe"])
def test_rearm_refuses_if_the_group_changed_while_it_read(tmp_path: Path, how: str) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        await latch_by_disagreement(h)
        before = count_requests(h, "group_info")
        h.fake.info_gate = asyncio.Event()
        task = asyncio.create_task(h.daemon.rearm())
        await wait_until(lambda: count_requests(h, "group_info") > before)
        if how == "event":
            events = h.daemon.group_events
            await h.fake.push_event(member("member_added"))
            await wait_until(lambda: h.daemon.group_events > events)
        else:
            gen = h.daemon.sub_gen
            await h.fake.drop_subscriptions()
            await wait_until(lambda: h.daemon.sub_gen != gen)
        h.fake.info_gate.set()
        assert (await task)[0] == "refused"
        assert h.daemon.latched() and h.store.get("expected_members") == "2"
    run(tmp_path, scenario)


@needs_tmux
def test_rearm_refuses_while_acknowledged_but_not_reading(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        await latch_by_disagreement(h)
        before = count_requests(h, "group_info")
        h.fake.info_gate = asyncio.Event()
        await h.fake.drop_subscriptions()
        await wait_until(lambda: count_requests(h, "group_info") > before)   # the new check, now blocked
        assert h.daemon.acked and not h.daemon.reading
        events = h.daemon.group_events
        await h.fake.push_event(member("member_added"))              # buffered: nobody is reading yet
        seen = count_requests(h, "group_info")
        assert (await h.daemon.rearm())[0] == "refused"
        assert count_requests(h, "group_info") == seen
        h.fake.info_gate.set()
        await wait_until(lambda: h.daemon.group_events > events)
        assert h.daemon.latched()
    run(tmp_path, scenario)


@needs_tmux
def test_lost_reply_on_an_add_then_rearm(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.timeout = 0.5
        h.fake.membership_mode = "lost"
        assert (await h.daemon.change_membership("add", "b"))[0] == "latched"
        assert keys(h) == [OPERATOR_HEX]
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert keys(h) == sorted([OPERATOR_HEX, SECOND_HEX])
        await h.say("from b", SECOND_HEX)
        await wait_until(lambda: "echo: from b" in h.texts())
    run(tmp_path, scenario)


@needs_tmux
@pytest.mark.parametrize(("mode", "remaining"), [("lost", [OPERATOR_HEX]),
                                                 ("hang", [OPERATOR_HEX, SECOND_HEX])])
def test_lost_reply_on_a_remove_then_rearm(tmp_path: Path, mode: str, remaining: list[str]) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.client.timeout = 0.5
        h.fake.membership_mode = mode
        assert (await h.daemon.change_membership("remove", "b"))[0] == "latched"
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert keys(h) == sorted(remaining)
        await h.say("from b", SECOND_HEX)
        await h.say("from a")
        await wait_until(lambda: "echo: from a" in h.texts())
        assert ("echo: from b" in h.texts()) == (SECOND_HEX in remaining)
    run(tmp_path, scenario, state(members=3, keys=(OPERATOR_HEX, SECOND_HEX)))


def two_policy_unknown_group(h: Harness) -> None:
    h.fake.member_count = 3
    h.store.set("expected_members", "3")      # no group_operators: startup latches (Task 3)


@needs_tmux
def test_rearm_reconciles_to_policy_without_a_record(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await wait_until(h.daemon.latched)
        await wait_until(lambda: h.daemon.reading)    # rearm refuses until startup has begun reading events
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert keys(h) == sorted([OPERATOR_HEX, SECOND_HEX])
        assert any(r["kind"] == "guard" and r["action"] == "rearm" and r["operators"] == "policy"
                   for r in audit(h))
        await h.say("from b", SECOND_HEX)
        await wait_until(lambda: "echo: from b" in h.texts())
    run(tmp_path, scenario, two_policy_unknown_group)


@needs_tmux
@pytest.mark.parametrize("members", [2, 4])
def test_rearm_refuses_a_count_it_cannot_account_for(tmp_path: Path, members: int) -> None:
    def before(h: Harness) -> None:
        h.fake.member_count = members
        h.store.set("expected_members", str(members))

    async def scenario(h: Harness) -> None:
        await wait_until(h.daemon.latched)
        h.daemon.reading = True
        result = await h.daemon.rearm()
        assert result[0] == "refused" and "policy.toml" in result[1]
        assert h.daemon.latched() and h.store.get("group_operators") is None
    run(tmp_path, scenario, before)


@needs_tmux
def test_restart_after_a_commit_authorises_from_the_group(tmp_path: Path) -> None:
    async def first(h: Harness) -> None:
        await join(h)
        assert (await h.daemon.change_membership("add", "b"))[0] == "committed"
    run(tmp_path, first)

    async def second(h: Harness) -> None:
        h.seq = 100         # the state directory is shared: a new harness must not reuse message IDs
        await h.say("from b", SECOND_HEX)
        await wait_until(lambda: "echo: from b" in h.texts())
    run(tmp_path, second, lambda h: setattr(h.fake, "member_count", 3))

    async def third(h: Harness) -> None:
        h.seq = 200
        await h.say("sneaky", SECOND_HEX)
        await h.say("from a")
        await wait_until(lambda: "echo: from a" in h.texts())
        assert not any("sneaky" in t for t in h.texts())
    run(tmp_path, third, lambda h: setattr(h.fake, "member_count", 3), operators=POLICY[:1])


@needs_tmux
@pytest.mark.parametrize("text", ["!interrupt", "plain text for the agent"])
def test_a_revoked_operators_queued_message_is_not_acted_on_after_a_rearm(tmp_path: Path, text: str) -> None:
    # Review finding 1: the message waits for dispatch_lock, its sender is removed outside admind, a rearm
    # clears the latch with only the other operator confirmed; the message must not run when the lock frees.
    async def scenario(h: Harness) -> None:
        await join(h)
        await h.daemon.dispatch_lock.acquire()
        mid = await h.say(text, SECOND_HEX)
        await wait_until(lambda: lock_waiters(h.daemon.dispatch_lock) >= 1)
        await h.fake.push_event(member("member_removed"))
        await wait_until(h.daemon.latched)
        h.fake.member_count = 2
        h.daemon.load_operators = lambda: POLICY[:1]
        assert (await h.daemon.rearm())[0] == "rearmed"
        assert h.daemon.operators == {OPERATOR_HEX: "op"}
        h.daemon.dispatch_lock.release()
        await wait_until(lambda: mid in h.store.inbound_with_status("dropped"))
        assert not any(r["kind"] == "command" for r in audit(h))
        assert f"echo: {text}" not in h.texts() and text not in h.log.read_text()
    run(tmp_path, scenario, state(members=3, keys=(OPERATOR_HEX, SECOND_HEX)))


@needs_tmux
def test_the_last_eligible_operator_cannot_be_removed(tmp_path: Path) -> None:
    # Review finding 2: two confirmed, policy lists only `op`: removing `op` leaves nobody to command.
    async def scenario(h: Harness) -> None:
        await join(h)
        h.daemon.load_operators = lambda: POLICY[:1]
        result = await h.daemon.change_membership("remove", "op")
        assert result[0] == "refused" and "last operator" in result[1]
        assert not requested(h, "group_member_remove")
        assert h.store.get("expected_members") == "3" and h.daemon.operators and not h.daemon.latched()
    run(tmp_path, scenario, state(members=3, keys=(OPERATOR_HEX, SECOND_HEX)))


@needs_tmux
def test_a_check_begun_before_a_change_does_not_latch_on_the_old_count(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await join(h)
        before = count_requests(h, "group_info")
        gate = asyncio.Event()
        h.fake.info_gate = gate
        check = asyncio.create_task(h.daemon.check_group())
        await wait_until(lambda: count_requests(h, "group_info") > before)   # it holds the old count (2)
        h.fake.info_gate = None
        assert (await h.daemon.change_membership("add", "b"))[0] == "committed"
        gate.set()
        assert await check is False
        assert not h.daemon.latched() and h.daemon.group_ok
        assert {"kind": "guard", "action": "group-check-deferred"} in [
            {"kind": r["kind"], "action": r.get("action")} for r in audit(h)]
    run(tmp_path, scenario)


@needs_tmux
def test_a_failed_paste_does_not_revive_a_revoked_prompt_after_pruning(tmp_path: Path) -> None:
    # Round-2 finding: with many sender records another message pruned the one being pasted; the paste then
    # failed, the prompt went back to `held`, and a missing sender counted as current.
    async def scenario(h: Harness) -> None:
        await join(h)
        entered, release = threading.Event(), threading.Event()
        pasted: list[str] = []
        original = h.daemon.agent.send

        def send(text: str) -> None:
            if text == "job from b":
                entered.set()
                release.wait(10)
                raise TmuxError("paste failed")
            pasted.append(text)
            original(text)
        h.daemon.agent.send = send                      # type: ignore[method-assign]
        job = "ee" * 32             # pasted by a flush outside work_lock (a hook's idle edge)
        assert h.store.claim_inbound(job)
        hold(h.daemon, job, "job from b", SECOND_HEX)
        flushing = asyncio.create_task(h.daemon.flush())
        await wait_until(entered.is_set)
        h.daemon.senders.update({f"{i:064x}": OPERATOR_HEX for i in range(1000, 1300)})
        await h.say("another from op")                  # prunes the records of messages nobody holds
        await wait_until(lambda: lock_waiters(h.daemon.dispatch_lock) >= 1)
        await h.fake.push_event(member("member_removed"))
        await wait_until(h.daemon.latched)
        h.fake.member_count = 2
        h.daemon.load_operators = lambda: POLICY[:1]
        assert (await h.daemon.rearm())[0] == "rearmed"
        release.set()
        await flushing
        await wait_until(lambda: job in h.store.inbound_with_status("dropped"))
        assert "job from b" not in pasted and "job from b" not in h.log.read_text()
    run(tmp_path, scenario, state(members=3, keys=(OPERATOR_HEX, SECOND_HEX)))
