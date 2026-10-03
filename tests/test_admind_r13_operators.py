import json
from pathlib import Path

import pytest
from fakes.settings import OPERATOR_HEX, SECOND_HEX, operator
from test_admind_daemon import Harness, needs_tmux, run_with  # noqa: E402  (shared harness)
from test_admind_settings import BASE_CONFIG  # noqa: E402

from heterodyne.admind import guard
from heterodyne.admind.redact import redact
from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.marmot.control import InboundMessage, Message, Sender
from heterodyne.marmot.nip19 import hex_to_npub

GROUP = "b2" * 32
STRANGER = "e5" * 32


def policy(d: Path, body: str) -> dict[str, str]:
    (d / "config.toml").write_text(BASE_CONFIG)
    (d / "policy.toml").write_text(body)
    return {"HETERODYNE_CONFIG_DIR": str(d), "HETERODYNE_STATE_DIR": str(d / "state"), "HOME": str(d)}


def two_ops(extra: str = "") -> str:
    return (f'approvers = ["a", "b", "c"]\noperators = ["a", "b", "c"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(SECOND_HEX)}"\n{extra}')


def test_every_operator_with_an_npub(tmp_path: Path) -> None:
    env = policy(tmp_path, two_ops())          # "c" has no identity: skipped
    s = resolve(load(None, env), env)
    assert [(o.name, o.hex) for o in s.operators] == [("a", OPERATOR_HEX), ("b", SECOND_HEX)]


def test_no_operator_with_an_npub_is_an_error(tmp_path: Path) -> None:
    env = policy(tmp_path, 'approvers = ["a"]\noperators = ["a"]\n')
    with pytest.raises(ConfigError, match="at least one"):
        resolve(load(None, env), env)


def test_duplicate_keys_are_an_error(tmp_path: Path) -> None:
    body = (f'approvers = ["a", "b"]\noperators = ["a", "b"]\n'
            f'[identities.a]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n'
            f'[identities.b]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    env = policy(tmp_path, body)
    with pytest.raises(ConfigError, match="same marmot_npub"):
        resolve(load(None, env), env)


@pytest.mark.parametrize("name", ["x" * 129, "bad\x1bname", "tab\tname"])
def test_operator_names_follow_the_control_contract(tmp_path: Path, name: str) -> None:
    # Codex r2 finding 10: a name `admind operators remove NAME` can't carry is refused in policy too.
    body = (f'approvers = ["{name}"]\noperators = ["{name}"]\n'
            f'[identities."{name}"]\nmarmot_npub = "{hex_to_npub(OPERATOR_HEX)}"\n')
    env = policy(tmp_path, body.replace("\x1b", "\\u001b").replace("\t", "\\t"))
    with pytest.raises(ConfigError, match="operator name"):
        resolve(load(None, env), env)


def msg(sender: str) -> InboundMessage:
    return InboundMessage("a1" * 32, GROUP, Message("0" * 64, Sender(sender, False), "hi", 1))


def test_guard_names_the_operator() -> None:
    ops = {OPERATOR_HEX: "a", SECOND_HEX: "b"}
    v = guard.judge_message(msg(SECOND_HEX.upper()), group_id=GROUP, operators=ops, latched=False)
    assert (v.action, v.operator) == ("process", "b")
    assert guard.judge_message(msg(STRANGER), group_id=GROUP, operators=ops, latched=False).action == "drop"
    v = guard.judge_message(msg(SECOND_HEX), group_id=GROUP, operators=ops, latched=True)
    assert (v.action, v.operator) == ("drop", "b")          # latched, but still named for the audit
    assert guard.judge_message(msg(STRANGER), group_id=GROUP, operators=ops, latched=True).operator is None


def test_member_count_against_expected() -> None:
    assert guard.judge_member_count(3, 3).action == "process"
    v = guard.judge_member_count(2, 3)
    assert v.action == "latch" and v.reason == "group has 2 members, expected 3"


# --- the daemon -----------------------------------------------------------------------------------
TWO = {"operators": (operator("a", OPERATOR_HEX), operator("b", SECOND_HEX))}
BOTH = json.dumps(sorted([OPERATOR_HEX, SECOND_HEX]))


def records(h: Harness) -> list[dict[str, object]]:
    return [json.loads(line) for line in (h.settings.state_dir / "audit.jsonl").read_text().splitlines()]


def two_in_group(h: Harness) -> None:
    h.fake.member_count = 3
    h.store.set("expected_members", "3")
    h.store.set("group_operators", BOTH)


@needs_tmux
def test_a_second_operator_is_processed_and_named(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("from b", SECOND_HEX)
        await h.until(lambda: "echo: from b" in h.texts())
        await h.say("from a")
        await h.until(lambda: "echo: from a" in h.texts())
    h = run_with(tmp_path, scenario, two_in_group, TWO)
    inbound = [r for r in records(h) if r["kind"] == "inbound"]
    assert [r["operator"] for r in inbound] == ["b", "a"]


@needs_tmux
def test_a_stranger_is_dropped(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("let me in", STRANGER)
        await h.say("hello", SECOND_HEX)
        await h.until(lambda: "echo: hello" in h.texts())
        assert not any("let me in" in t for t in h.texts())
    h = run_with(tmp_path, scenario, two_in_group, TWO)
    assert any(r.get("reason") == "sender is not an operator" for r in records(h))


@needs_tmux
def test_the_expected_count_is_migrated(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.until(lambda: h.store.get("expected_members") is not None)
    def before(h: Harness) -> None:
        h.fake.member_count = 2
        h.store.set("group_operators", json.dumps([OPERATOR_HEX]))
    h = run_with(tmp_path, scenario, before)
    assert h.store.get("expected_members") == "2"
    assert any(r["kind"] == "guard" and r["action"] == "migrated" for r in records(h))


@needs_tmux
def test_configured_is_not_confirmed(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("sneaky", SECOND_HEX)
        await h.say("hello", OPERATOR_HEX)
        await h.until(lambda: "echo: hello" in h.texts())
        assert not any("sneaky" in t for t in h.texts())
        assert h.daemon.operators == {OPERATOR_HEX: "a"}
    def before(h: Harness) -> None:
        h.fake.member_count = 2
        h.store.set("expected_members", "2")
        h.store.set("group_operators", json.dumps([OPERATOR_HEX]))
    h = run_with(tmp_path, scenario, before, TWO)
    assert any(r.get("reason") == "sender is not an operator" for r in records(h))


@needs_tmux
def test_plan2_upgrade_with_one_policy_operator(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.say("hello")
        await h.until(lambda: "echo: hello" in h.texts())
    h = run_with(tmp_path, scenario)
    assert json.loads(h.store.get("group_operators") or "null") == [OPERATOR_HEX]
    assert any(r["kind"] == "guard" and r["action"] == "migrated-operators" and r["operators"] == 1
               for r in records(h))


@needs_tmux
def test_plan2_upgrade_with_several_policy_operators_latches(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        await h.until(lambda: h.store.get("latched") is not None)
    def before(h: Harness) -> None:
        h.fake.member_count = 2
    h = run_with(tmp_path, scenario, before, TWO)
    assert (h.store.get("latched") or "").startswith("admind does not know which operators are in the group")
    assert h.store.get("group_operators") is None
    assert h.daemon.operators == {}


@needs_tmux
def test_a_latched_operators_message_is_audited_whole(tmp_path: Path) -> None:
    text = "line " * 1000 + "ghp_" + "A" * 36

    async def scenario(h: Harness) -> None:
        h.daemon.latch("test")
        await h.say(text, SECOND_HEX)
        await h.until(lambda: any(r["kind"] == "drop" for r in records(h)))
    h = run_with(tmp_path, scenario, two_in_group, TWO)
    drops = [r for r in records(h) if r["kind"] == "drop"]
    assert len(drops) == 1
    drop = drops[0]
    assert drop["operator"] == "b" and str(drop["reason"]).startswith("admind is latched")
    assert drop["text"] == redact(text) and len(str(drop["text"])) > 5000 and "ghp_" not in str(drop["text"])
    assert h.texts() == []


@needs_tmux
def test_a_replayed_operator_message_is_audited_whole(tmp_path: Path) -> None:
    async def scenario(h: Harness) -> None:
        mid = await h.say("once", SECOND_HEX)
        await h.until(lambda: "echo: once" in h.texts())
        await h.fake.push_event(h.fake.message_event("once", SECOND_HEX, mid))
        await h.until(lambda: any(r["kind"] == "drop" for r in records(h)))
    h = run_with(tmp_path, scenario, two_in_group, TWO)
    drop = next(r for r in records(h) if r["kind"] == "drop")
    assert drop["operator"] == "b" and drop["reason"] == "replayed message id" and drop["text"] == "once"
