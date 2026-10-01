import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.admind import alerts, chunk, commands, guard
from heterodyne.marmot.control import GroupStateChanged, InboundMessage, Message, Sender

OP = "c3" * 32
GROUP = "b2" * 32


def msg(sender: str = OP, *, is_self: bool = False, group: str = GROUP, text: str = "hi") -> InboundMessage:
    return InboundMessage(account_id_hex="a1" * 32, group_id_hex=group,
                          message=Message(message_id_hex="d4" * 32, text=text, recorded_at=1,
                                          sender=Sender(account_id_hex=sender, is_self=is_self)))


def test_guard_accepts_only_the_operator_in_the_group() -> None:
    assert guard.judge_message(msg(), group_id=GROUP, operator_hex=OP, latched=False).action == "process"
    upper = msg(OP.upper(), group=GROUP.upper())
    assert guard.judge_message(upper, group_id=GROUP, operator_hex=OP, latched=False).action == "process"
    stranger = guard.judge_message(msg("e5" * 32), group_id=GROUP, operator_hex=OP, latched=False)
    assert stranger.action == "drop"
    assert guard.judge_message(msg(group="f6" * 32), group_id=GROUP, operator_hex=OP,
                               latched=False).action == "drop"
    assert guard.judge_message(msg(is_self=True), group_id=GROUP, operator_hex=OP,
                               latched=False).action == "ignore"
    latched = guard.judge_message(msg(), group_id=GROUP, operator_hex=OP, latched=True)
    assert latched.action == "drop" and "latched" in latched.reason


@pytest.mark.parametrize("change", sorted(guard.MEMBERSHIP_CHANGES))
def test_membership_changes_latch(change: str) -> None:
    ev = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex=GROUP, change=change)
    assert guard.judge_group_change(ev, group_id=GROUP).action == "latch"


def test_other_group_changes_are_ignored() -> None:
    ev = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex=GROUP, change="group_renamed", detail="x")
    assert guard.judge_group_change(ev, group_id=GROUP).action == "ignore"
    elsewhere = GroupStateChanged(account_id_hex="a1" * 32, group_id_hex="f6" * 32, change="member_added")
    assert guard.judge_group_change(elsewhere, group_id=GROUP).action == "ignore"


@pytest.mark.parametrize(("count", "action"), [(1, "latch"), (2, "process"), (3, "latch")])
def test_member_count(count: int, action: str) -> None:
    assert guard.judge_member_count(count).action == action


@given(st.text(), st.integers(min_value=1, max_value=50))
def test_chunks_reassemble_exactly(text: str, limit: int) -> None:
    parts = chunk.split(text, limit)
    assert "".join(parts) == text
    assert all(0 < len(p) <= limit for p in parts)


def test_chunks_prefer_line_breaks() -> None:
    text = "a" * 30 + "\n" + "b" * 30
    assert chunk.split(text, 40) == ["a" * 30 + "\n", "b" * 30]
    assert chunk.split("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert chunk.split("", 10) == []


@pytest.mark.parametrize(("text", "expected"), [
    ("hello", None),
    ("!new", commands.Command("new")),
    ("!interrupt", commands.Command("interrupt")),
    ("!ps", commands.Command("ps")),
    ("!tail", commands.Command("tail", lines=40)),
    ("!tail 7", commands.Command("tail", lines=7)),
    ("!restart wsd.service", commands.Command("restart", arg="wsd.service")),
])
def test_parse_commands(text: str, expected: commands.Command | None) -> None:
    assert commands.parse(text) == expected


@pytest.mark.parametrize("text", ["!", "!restrat wsd", "!new now", "!tail x", "!tail 0", "!tail 99999",
                                  "!restart", "!restart a b", "! ps"])
def test_parse_errors(text: str) -> None:
    with pytest.raises(commands.CommandError):
        commands.parse(text)


def test_control_characters() -> None:
    assert not commands.has_control_chars("tab\tand\nnewline and émoji 👍")
    for bad in ("\x1b[201~", "\x00", "\r", "\x7f", "\x9b"):
        assert commands.has_control_chars(f"a{bad}b")


def test_alert_scan_and_render(tmp_path: Path) -> None:
    (tmp_path / "b-2.json").write_text(json.dumps({"id": "b-2", "created_at": "2026-09-30T00:00:00Z",
                                                   "text": "card undelivered"}))
    (tmp_path / "a1.json").write_text("{not json")
    (tmp_path / ".c3.json.tmp").write_text("{}")
    (tmp_path / "bad name.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    found = alerts.scan(tmp_path)
    assert [name for name, _ in found] == ["a1", "b-2", "bad name"]
    assert found[0][1] is None and found[2][1] is None
    alert = found[1][1]
    assert alert is not None and alert.text == "card undelivered"
    rendered = alerts.render("b-2", alert, 4000)
    assert rendered.startswith("🚨 wsd alert (2026-09-30T00:00:00Z): card undelivered")
    long = alerts.Alert(id="x", created_at="t", text="y" * 5000)
    assert len(alerts.render("x", long, 1000)) <= 1000
    assert "malformed" in alerts.render("a1", None, 4000)
    assert alerts.scan(tmp_path / "missing") == []


def test_alert_id_must_match_file_name(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"id": "b", "created_at": "t", "text": "x"}))
    assert alerts.scan(tmp_path) == [("a", None)]
