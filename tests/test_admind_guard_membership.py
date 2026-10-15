"""Focused offline guard state machine tests, including dangerous-order regression cases."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, make_settings, operator

from heterodyne.admind import commands, guard, guard_state, membership
from heterodyne.admind.audit import Audit
from heterodyne.admind.daemon import Admind, render_batch
from heterodyne.admind.settings import Operator
from heterodyne.admind.store import Store
from heterodyne.marmot.control import ControlError, GroupStateChanged

GROUP = "b2" * 32
MID = "a" * 64


class Probe:
    def __init__(self) -> None:
        self.count = 2
        self.failure = False
        self.gate: asyncio.Event | None = None
        self.started: asyncio.Event | None = None
        self.active = 0
        self.maximum = 0
        self.sent: list[str] = []
        self.after_send: Any = None

    async def group_info(self, *_: Any) -> Any:
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        count = self.count
        try:
            if self.started:
                self.started.set()
            if self.gate:
                await self.gate.wait()
            if self.failure:
                raise ControlError("unavailable", "offline", True)
            return SimpleNamespace(member_count=count)
        finally:
            self.active -= 1

    async def send_final(self, _account: str, _group: str, text: str, _reply: Any, _key: str) -> Any:
        self.sent.append(text)
        if self.after_send:
            self.after_send()
        return SimpleNamespace(message_ids_hex=[f"{len(self.sent):064x}"])


def make(tmp_path: Path) -> tuple[Admind, Probe]:
    settings = make_settings(tmp_path, group_check_seconds=60)
    store = Store(settings.state_dir / "db")
    store.set("group_operators", json.dumps([OPERATOR_HEX]))
    store.set("expected_members", "2")
    store.set("operator_seen_at", "seen")
    client = Probe()
    daemon = Admind(settings, client, store, Audit(settings.state_dir / "audit"), None, None,  # type: ignore[arg-type]
                    "account", GROUP)
    daemon.group_ok = daemon.observing = daemon.acked = daemon.reading = True
    daemon.wallclock = lambda: 1000
    return daemon, client


@pytest.mark.parametrize("unreadable", [False, True])
def test_rearm_refreshes_policy_after_waiting_for_count(tmp_path: Path, unreadable: bool) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        original = [OPERATOR_HEX, SECOND_HEX]
        d.store.set("group_operators", json.dumps(original))
        d.store.set("latched", "explicit recovery hold")
        d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        probe.gate, probe.started = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(d.rearm())
        await probe.started.wait()
        if unreadable:
            def unavailable() -> tuple[Operator, ...]:
                raise ValueError("policy unavailable during count")
            d.load_operators = unavailable
        else:
            d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
        probe.gate.set()
        result, _message = await task
        if unreadable:
            assert result == "refused"
            assert d.confirmed_operators() == set(original)
            assert d.store.get("latched") == "explicit recovery hold"
        else:
            assert result == "rearmed"
            assert d.confirmed_operators() == {OPERATOR_HEX}
            assert set(d.operators) == {OPERATOR_HEX}
            assert not d.latched()
        d.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", sorted(guard.MEMBERSHIP_CHANGES))
def test_events_and_count_changes_never_latch_or_grant(tmp_path: Path, kind: str) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        event = GroupStateChanged(account_id_hex="a" * 64, group_id_hex=GROUP, change=kind)
        d.on_event(event)
        assert d.check_task is not None
        await d.check_task
        probe.count = 1
        assert await d.check_group()
        assert not d.latched() and d.may_post()
        assert d.confirmed_operators() == {OPERATOR_HEX}
        assert d.expected_members() == 1
        state = guard_state.notice_state(d.store.get("membership_notice"))
        assert state["unresolved"] is not None
        await d.outbox_pass()
        assert any("operator authority is unchanged" in text for text in probe.sent)
        d.store.close()
    asyncio.run(scenario())


def test_other_group_event_does_not_touch_generation_or_notices(tmp_path: Path) -> None:
    d, _ = make(tmp_path)
    d.on_event(GroupStateChanged(account_id_hex="a" * 64, group_id_hex="c" * 64, change="member_added"))
    assert d.group_events == 0 and d.store.get("membership_notice") is None
    d.store.close()


def test_matching_count_cannot_confirm_a_policy_only_operator(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        probe.count = 3  # equals daemon plus both policy identities, still proves no identity
        assert await d.check_group()
        assert d.confirmed_operators() == {OPERATOR_HEX}
        assert d.refresh_authority() and set(d.operators) == {OPERATOR_HEX}
        d.store.close()
    asyncio.run(scenario())


def test_unknown_startup_latch_is_preserved_before_recovery(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        d, _probe = make(tmp_path)
        d.store.delete("group_operators")
        d.policy_operators = (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        class BeforeRecovery(Exception):
            pass
        def reached_recovery(_chars: int) -> int:
            assert (d.store.get("latched") or "").startswith("admind does not know which operators")
            assert d.store.get("group_operators") is None and d.operators == {}
            raise BeforeRecovery
        monkeypatch.setattr(d.store, "redact_pending_outbox", reached_recovery)
        with pytest.raises(BeforeRecovery):
            await d.run()
        d.store.close()
    asyncio.run(scenario())


def test_stale_response_changes_no_replacement_state_and_checks_serialize(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        probe.count = 0                   # used to latch before generation validation
        probe.gate, probe.started = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(d.check_group())
        await probe.started.wait()
        d.sub_gen += 1
        probe.count = 4
        second = asyncio.create_task(d.check_group())
        probe.gate.set()
        assert not await first
        assert d.expected_members() in {2, 4} and d.group_ok and not d.latched()
        assert await second
        assert probe.maximum == 1 and d.expected_members() == 4
        assert d.confirmed_operators() == {OPERATOR_HEX}
        d.store.close()
    asyncio.run(scenario())


def test_epoch_supersedes_response_and_cancellation_releases_count_lock(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        probe.count = 9
        probe.gate, probe.started = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(d.check_group())
        await probe.started.wait()
        d.membership_epoch += 1
        probe.gate.set()
        assert not await task and d.expected_members() == 2
        probe.gate.clear()
        probe.started.clear()
        task = asyncio.create_task(d.check_group())
        await probe.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not d.count_lock.locked()
        d.store.close()
    asyncio.run(scenario())


def test_transport_failure_and_zero_recover_without_rearm(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        for failure, count in [(True, 2), (False, 0)]:
            probe.failure, probe.count = failure, count
            assert not await d.check_group()
            assert not d.may_post() and not d.latched()
        probe.count = 3
        assert await d.check_group() and d.may_post()
        assert d.confirmed_operators() == {OPERATOR_HEX}
        d.store.close()
    asyncio.run(scenario())


def test_policy_revocation_per_chunk_suppresses_whole_group_then_sends_system(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.store.claim_inbound(MID, sender_key=OPERATOR_HEX)
        d.post("cmd:" + MID + ":0", "first", MID, owner=(OPERATOR_HEX,))
        d.post("cmd:" + MID + ":1", "revoked second", MID, owner=(OPERATOR_HEX,))
        d.post("later-system", "later notice", None, owner=None)
        probe.after_send = lambda: setattr(d, "load_operators", lambda: ())
        await d.outbox_pass()
        assert probe.sent == ["first", "later notice"]
        assert d.store.db.execute("SELECT status FROM outbox WHERE text = 'revoked second'").fetchone() == (
            "suppressed",)
        assert not d.sender_current(MID)
        d.store.close()
    asyncio.run(scenario())


def test_unreadable_policy_stalls_then_recovers_without_stale_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.post("system", "safe notice", None, owner=None)
        def unavailable() -> Any:
            raise OSError("private policy unavailable")
        d.load_operators = unavailable
        await d.outbox_pass()
        assert probe.sent == [] and d.store.next_pending() is not None
        assert d.operators == {} and not d.latched()
        d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
        await d.outbox_pass()
        assert probe.sent == ["safe notice"]
        d.store.close()
    asyncio.run(scenario())


def test_unknown_first_row_and_multi_owner_revocation_do_not_block_cards(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.post("unknown", "unproven", None, owner=())
        d.post("batch:1.0", "multi owner secret", None, owner=(OPERATOR_HEX, SECOND_HEX))
        d.post("ask:aaaa:0", "card", None, owner=None)
        await d.outbox_pass()
        assert probe.sent == ["card"]
        assert d.store.db.execute("SELECT status FROM outbox ORDER BY seq").fetchall() == [
            ("quarantined",), ("suppressed",), ("sent",)]
        d.store.close()
    asyncio.run(scenario())


def test_notice_flood_restart_cadence_and_clock_rollback_do_not_block_replies(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        at = [1000.0]
        d.wallclock = lambda: at[0]
        for _ in range(10000):
            d.observe_membership(kind="admin_added")
        d.flush_membership_notice()
        for _ in range(100):
            d.observe_membership(kind="member_left")
            d.flush_membership_notice()
        assert len(d.store.pending()) == 1
        assert len(d.store.get("membership_notice") or "") < 400
        await d.outbox_pass()
        assert len(probe.sent) == 1
        d.flush_membership_notice()
        assert d.store.pending() == []
        d.post("reply-now", "ordinary reply", None, owner=(OPERATOR_HEX,))
        await d.outbox_pass()
        assert probe.sent[-1] == "ordinary reply"
        at[0] = 1060
        d.flush_membership_notice()
        assert len(d.store.pending()) == 1
        at[0] = 900                       # pending snapshot is now future-due
        d.post("card-now", "ordinary card", None, owner=None)
        await d.outbox_pass()
        assert probe.sent[-1] == "ordinary card"
        assert d.store.db.execute("SELECT COUNT(*) FROM outbox WHERE status = 'delayed'").fetchone() == (1,)
        # Durable state and delayed status survive reopening, never a second snapshot.
        path = d.s.state_dir / "db"
        d.store.close()
        d.store = Store(path)
        d.flush_membership_notice()
        assert not d.store.pending()
        at[0] = 800                       # rollback again while the snapshot is already delayed
        d.flush_membership_notice()
        at[0] = 860
        await d.outbox_pass()
        assert len([text for text in probe.sent if "membership observations" in text]) == 2
        assert guard_state.notice_state(d.store.get("membership_notice"))["unresolved"] is None
        d.store.close()
    asyncio.run(scenario())


def test_local_result_notice_is_not_observation_throttled(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        state = guard_state.notice_state(None)
        state["last_sent"] = 1000
        d.store.set("membership_notice", json.dumps(state))
        d.post("membership:abc", "local operation result", None, owner=None)
        await d.outbox_pass()
        assert probe.sent == ["local operation result"]
        d.store.close()
    asyncio.run(scenario())


def test_terminal_notice_failure_retries_after_cadence_without_blocking_replies(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        at = [1000.0]
        d.wallclock = lambda: at[0]
        d.observe_membership(kind="member_added")
        d.flush_membership_notice()
        row = d.store.next_pending()
        assert row is not None
        d.send_failed(row.seq, row.key)
        state = guard_state.notice_state(d.store.get("membership_notice"))
        assert state["unresolved"] is None and state["pending"] and state["last_sent"] == 1000
        d.observe_membership(kind="member_removed")
        d.post("unrelated", "ordinary reply", None, owner=(OPERATOR_HEX,))
        await d.outbox_pass()
        assert probe.sent == ["ordinary reply"]
        at[0] = 1060
        await d.outbox_pass()
        assert len(probe.sent) == 2 and "member_removed=1" in probe.sent[-1]
        assert guard_state.notice_state(d.store.get("membership_notice"))["unresolved"] is None
        d.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("unproven", [False, True])
@pytest.mark.parametrize("already_batched", [False, True])
def test_untrusted_turn_cannot_poison_current_requesters_backstop(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unproven: bool, already_batched: bool) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        bad_mid = "b" * 64
        d.store.claim_inbound(MID, sender_key=OPERATOR_HEX)
        if not unproven:
            d.store.claim_inbound(bad_mid, sender_key=SECOND_HEX)
        bad = d.store.add_turn("bad", "session", bad_mid, "bad origin", "excluded secret",
                               None, None, None, "summarizing")
        good = d.store.add_turn("good", "session", MID, "good origin", "current reply",
                                None, None, None, "summarizing")
        async def failed_summary(*_args: Any, **_kwargs: Any) -> str:
            raise RuntimeError("offline summarizer failure")
        monkeypatch.setattr("heterodyne.admind.daemon.summarize.summarize", failed_summary)
        if already_batched:
            d.queue_backstop(bad)
            d.queue_backstop(good)
        else:
            for turn_id in (bad, good):
                row = d.store.turn(turn_id)
                assert row is not None
                await d.summarize_turn(row)
        d.wallclock = lambda: 2000
        await d.close_due_batches()
        await d.outbox_pass()
        assert len(probe.sent) == 1 and "current reply" in probe.sent[0]
        assert "excluded secret" not in probe.sent[0] and "bad origin" not in probe.sent[0]
        assert d.store.db.execute("SELECT status FROM outbox WHERE key = ?", (f"held-turn:{bad}",)
                                  ).fetchone() == ("quarantined" if unproven else "suppressed",)
        assert d.store.turn(bad).status == "summarized"  # type: ignore[union-attr]
        assert d.store.db.execute("SELECT batch_id FROM turns WHERE turn_id = ?",
                                  (bad,)).fetchone() == (None,)
        d.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("mode", ["held", "stale-held-link", "revoked-after-delivery"])
def test_batch_details_exclude_held_and_now_revoked_sources(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, full: bool, mode: str) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        if mode == "revoked-after-delivery":
            d.store.set("group_operators", json.dumps([OPERATOR_HEX, SECOND_HEX]))
            d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        turns: list[int] = []
        for mid, sender, text in ((MID, OPERATOR_HEX, "current reply"),
                                  ("b" * 64, SECOND_HEX, "excluded secret")):
            d.store.claim_inbound(mid, sender_key=sender)
            turn = d.store.add_turn(mid, "session", mid, text + " origin", text,
                                    None, None, None, "summarizing")
            turns.append(turn)
            d.queue_backstop(turn)
        batch = d.store.turn(turns[0]).batch_id  # type: ignore[union-attr]
        d.wallclock = lambda: 2000
        await d.close_due_batches()
        await d.outbox_pass()
        assert probe.sent
        if mode == "stale-held-link":  # defense in depth against an old/stale association
            d.store.db.execute("UPDATE turns SET batch_id = ? WHERE turn_id = ?", (batch, turns[1]))
        d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
        probe.sent.clear()
        calls: list[int] = []
        async def tool_calls(row: Any, _seconds: float, _limit: int) -> tuple[str, bool]:
            calls.append(row.turn_id)
            return "tool:" + row.text, False
        monkeypatch.setattr(d, "tool_calls", tool_calls)
        command = "c" * 64
        d.store.claim_inbound(command, sender_key=OPERATOR_HEX)
        await d.details(command, commands.Command("details", arg="full" if full else None), None)
        await d.outbox_pass()
        rendered = "\n".join(probe.sent)
        assert "current reply" in rendered and "excluded secret" not in rendered
        assert "tool:current reply" in rendered if full else "tool:" not in rendered
        assert calls == ([turns[0]] if full else [])
        d.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_direct_summary_details_preserve_valid_and_exclude_revoked_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revoked: bool, full: bool) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.store.set("group_operators", json.dumps([OPERATOR_HEX, SECOND_HEX]))
        d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        source = "b" * 64
        d.store.claim_inbound(source, sender_key=SECOND_HEX)
        turn = d.store.add_turn("source", "session", source, "source origin", "source secret",
                                None, None, None, "summarized")
        d.post("summary", "short summary", source, owner=(SECOND_HEX,))
        d.store.record_post("summary", "summary", turn, None)
        await d.outbox_pass()
        probe.sent.clear()
        if revoked:
            d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
        calls: list[int] = []
        async def tool_calls(row: Any, _seconds: float, _limit: int) -> tuple[str, bool]:
            calls.append(row.turn_id)
            return "source tool secret", False
        monkeypatch.setattr(d, "tool_calls", tool_calls)
        command = "c" * 64
        d.store.claim_inbound(command, sender_key=OPERATOR_HEX)
        await d.details(command, commands.Command("details", arg="full" if full else None), None)
        rows = [row for row in d.store.pending() if row.key.startswith("details:")]
        assert all(set(d.store.row_owners(row.seq)) == {OPERATOR_HEX, SECOND_HEX} for row in rows)
        await d.outbox_pass()
        rendered = "\n".join(probe.sent)
        assert ("source secret" in rendered) is not revoked
        assert ("source tool secret" in rendered) == (full and not revoked)
        assert calls == ([turn] if full and not revoked else [])
        d.store.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["tool-await", "queued", "between-chunks"])
def test_batch_details_retain_source_owners_through_awaits_and_delivery(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.store.set("group_operators", json.dumps([OPERATOR_HEX, SECOND_HEX]))
        d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        for mid, sender in ((MID, OPERATOR_HEX), ("b" * 64, SECOND_HEX)):
            d.store.claim_inbound(mid, sender_key=sender)
            turn = d.store.add_turn(mid, "session", mid, "origin", "ordinary output words " * d.s.chunk_chars,
                                    None, None, None, "summarizing")
            d.queue_backstop(turn)
        d.wallclock = lambda: 2000
        await d.close_due_batches()
        await d.outbox_pass()
        probe.sent.clear()
        def revoke() -> None:
            d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
        entered, release = asyncio.Event(), asyncio.Event()
        async def tool_calls(row: Any, _seconds: float, _limit: int) -> tuple[str, bool]:
            if row.reply_to == "b" * 64 and boundary == "tool-await":
                entered.set()
                await release.wait()
            return "private tool", False
        monkeypatch.setattr(d, "tool_calls", tool_calls)
        command = "c" * 64
        d.store.claim_inbound(command, sender_key=OPERATOR_HEX)
        task = asyncio.create_task(d.details(command, commands.Command("details", arg="full"), None))
        if boundary == "tool-await":
            await entered.wait()
            revoke()
            release.set()
        await task
        rows = [row for row in d.store.pending() if row.key.startswith("details:")]
        assert len(rows) > 1
        assert all(set(d.store.row_owners(row.seq)) == {OPERATOR_HEX, SECOND_HEX} for row in rows)
        if boundary == "between-chunks":
            probe.after_send = revoke
        else:
            revoke()
        await d.outbox_pass()
        assert len(probe.sent) == (1 if boundary == "between-chunks" else 0)
        statuses = dict(d.store.db.execute("SELECT seq,status FROM outbox WHERE key LIKE 'details:%'"))
        assert all(statuses[row.seq] == "suppressed" for row in rows[len(probe.sent):])
        d.store.close()
    asyncio.run(scenario())


def test_delaying_a_quarantined_group_never_releases_it(tmp_path: Path) -> None:
    d, _probe = make(tmp_path)
    d.post("unknown", "held payload", None, owner=())
    d.store.hold_group("unknown", "quarantined", "unproven-owner")
    d.store.hold_group("unknown", "delayed", "notice-cadence", 1000)
    d.store.release_delayed(2000)
    assert d.store.db.execute("SELECT status,hold_reason FROM outbox WHERE key='unknown'").fetchone() == (
        "quarantined", "unproven-owner")
    d.store.close()


def test_revocation_during_batch_render_rebuilds_without_revoked_contribution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        d.store.set("group_operators", json.dumps([OPERATOR_HEX, SECOND_HEX]))
        d.load_operators = lambda: (operator("op", OPERATOR_HEX), operator("other", SECOND_HEX))
        for mid, sender, text in ((MID, OPERATOR_HEX, "current reply"),
                                  ("b" * 64, SECOND_HEX, "revoked reply")):
            d.store.claim_inbound(mid, sender_key=sender)
            turn = d.store.add_turn(mid, "session", mid, "origin", text, None, None, None, "summarizing")
            d.queue_backstop(turn)
        def revoke_while_rendering(rows: Any) -> str:
            d.load_operators = lambda: (operator("op", OPERATOR_HEX),)
            return render_batch(rows)
        monkeypatch.setattr("heterodyne.admind.daemon.render_batch", revoke_while_rendering)
        d.wallclock = lambda: 2000
        await d.close_due_batches()
        await d.outbox_pass()
        assert not probe.sent  # first render was discarded, never queued with stale contributions
        await d.close_due_batches()
        await d.outbox_pass()
        assert len(probe.sent) == 1 and "current reply" in probe.sent[0]
        assert "revoked reply" not in probe.sent[0]
        d.store.close()
    asyncio.run(scenario())


def test_explicit_success_uses_provisional_count_and_next_observation_replaces_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        d, probe = make(tmp_path)
        pending = membership.Pending("add", "b", SECOND_HEX, 2, 3, "now", "id")
        d.store.set("membership_pending", pending.dump())
        assert d.settle_membership(pending, "commit", "ok", None, d.s.operators)[0] == "committed"
        assert d.store.get("expected_members_provisional") == "1" and d.expected_members() == 3
        probe.count = 2
        assert await d.check_group()
        assert d.expected_members() == 2 and d.store.get("expected_members_provisional") is None
        assert d.confirmed_operators() == {OPERATOR_HEX, SECOND_HEX} and not d.latched()
        d.store.close()
    asyncio.run(scenario())


def test_validated_policy_files_are_fresh_at_each_chunk(tmp_path: Path) -> None:
    from heterodyne.admind.settings import operators
    from heterodyne.config import load

    async def scenario() -> None:
        d, probe = make(tmp_path)
        config = tmp_path / "config"
        config.mkdir()
        policy = config / "policy.toml"
        primary = operator("op", OPERATOR_HEX)
        secondary = operator("second", SECOND_HEX)

        def write_policy(npub: str) -> None:
            policy.write_text(f'approvers = ["op"]\noperators = ["op"]\n'
                              f'[identities.op]\nmarmot_npub = "{npub}"\n')

        write_policy(primary.npub)
        d.load_operators = lambda: operators(load(env={"HETERODYNE_CONFIG_DIR": str(config)}))
        d.post("one:0", "first", None, owner=(OPERATOR_HEX,))
        d.post("one:1", "revoked", None, owner=(OPERATOR_HEX,))
        probe.after_send = lambda: write_policy(secondary.npub)
        await d.outbox_pass()
        assert probe.sent == ["first"]
        assert not d.operators
        assert d.store.db.execute("SELECT status FROM outbox WHERE key='one:1'").fetchone() == ("suppressed",)
        policy.write_text("invalid = [")
        assert not d.refresh_authority()
        write_policy(primary.npub)
        assert d.refresh_authority() and OPERATOR_HEX in d.operators
        d.store.close()

    asyncio.run(scenario())


def test_summary_does_not_launch_after_requester_revocation(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        d, _probe = make(tmp_path)
        d.store.claim_inbound(MID, sender_key=OPERATOR_HEX)
        turn_id = d.store.add_turn("turn", "session", MID, "op", "private response",
                                   None, None, None, "summarizing")
        row = d.store.turn(turn_id)
        assert row is not None
        d.load_operators = lambda: (operator("other", SECOND_HEX),)

        async def forbidden(*_args: Any, **_kwargs: Any) -> str:
            pytest.fail("revoked request launched external summarizer")
        monkeypatch.setattr("heterodyne.admind.daemon.summarize.summarize", forbidden)
        await d.summarize_turn(row)
        assert d.store.turn(turn_id).status == "summarized"  # type: ignore[union-attr]
        assert d.store.db.execute("SELECT status FROM outbox WHERE key = ?", (f"held-turn:{turn_id}",)
                                  ).fetchone() == ("suppressed",)
        d.store.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("gate", ["subscription", "policy"])
def test_summary_retries_after_a_temporary_gate(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str) -> None:
    async def scenario() -> None:
        d, _probe = make(tmp_path)
        d.store.claim_inbound(MID, sender_key=OPERATOR_HEX)
        turn_id = d.store.add_turn("turn", "session", MID, "op", "private response",
                                   None, None, None, "summarizing")
        row = d.store.turn(turn_id)
        assert row is not None
        calls: list[str] = []

        async def summary(*_args: Any, **_kwargs: Any) -> str:
            calls.append("launched")
            return "safe summary"
        monkeypatch.setattr("heterodyne.admind.daemon.summarize.summarize", summary)
        load = d.load_operators
        if gate == "subscription":
            d.observing = False
        else:
            def unavailable() -> tuple[Operator, ...]:
                raise ValueError("unreadable temporary policy")
            d.load_operators = unavailable
        await d.summarize_turn(row)
        assert not calls and d.store.turn(turn_id).status == "summarizing"  # type: ignore[union-attr]
        d.observing = True
        d.load_operators = load
        await d.summarize_turn(row)
        assert calls == ["launched"]
        assert d.store.turn(turn_id).status == "summarized"  # type: ignore[union-attr]
        d.store.close()
    asyncio.run(scenario())
