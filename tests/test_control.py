import asyncio
import json
from pathlib import Path

import pytest
from fakes.fake_wn_agent import ACCOUNT, FakeWnAgent

from heterodyne.marmot.control import (
    MAX_FRAME,
    ControlClient,
    ControlError,
    GroupStateChanged,
    InboundMessage,
    OtherEvent,
    PeerError,
    ProtocolError,
    decode_event,
)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_requests_carry_protocol_id_and_token(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        accounts = await client.account_list()
        assert accounts.accounts[0].account_id_hex == ACCOUNT
        assert accounts.accounts[0].local_signing is True
        info = await client.group_info(ACCOUNT, fake.group_id)
        assert info.member_count == 2
        req = fake.requests[-1]
        assert req["marmot_agent_control"] == "marmot.agent-control.v2"
        assert req["auth_token"] == "test-token" and len(req["id"]) == 32  # noqa: S105 (test value)
        await fake.stop()
    run(body())


def test_error_frames_raise_control_error_with_code(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        with pytest.raises(ControlError) as exc:
            await ControlClient(tmp_path / "s.sock", "wrong").account_list()
        assert exc.value.code == "unauthorized" and exc.value.retryable is False
        assert exc.value.detail == "bad token" and "bad token" not in str(exc.value)
        await fake.stop()
    run(body())


def test_send_final_is_idempotent_by_key(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        a = await client.send_final(ACCOUNT, fake.group_id, "hi", None, "k1")
        b = await client.send_final(ACCOUNT, fake.group_id, "hi", None, "k1")
        assert a.message_ids_hex == b.message_ids_hex and len(fake.sent) == 1
        assert fake.sent[0]["reply_to_message_id_hex"] is None
        assert fake.sent[0]["idempotency_key"] == "k1"
        await fake.stop()
    run(body())


def test_missing_socket_is_a_retryable_socket_error(tmp_path: Path) -> None:
    with pytest.raises(ControlError) as exc:
        run(ControlClient(tmp_path / "absent.sock", None, timeout=1).account_list())
    assert exc.value.code == "socket_io" and exc.value.retryable


def test_subscribe_yields_typed_events(tmp_path: Path) -> None:
    async def body() -> list[object]:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        got: list[object] = []

        async def consume() -> None:
            async for event in client.subscribe(ACCOUNT, fake.group_id):
                got.append(event)
                if len(got) == 3:
                    return

        task = asyncio.create_task(consume())
        await fake.wait_subscribed()
        await fake.push_event(fake.message_event("hello", "c3" * 32, "d4" * 32, reply_to="e5" * 32))
        await fake.push_event({"type": "group_state_changed", "account_id_hex": ACCOUNT,
                               "group_id_hex": fake.group_id, "event_id_hex": "f6" * 32,
                               "change": "member_added"})
        await fake.push_event({"type": "message_edited", "account_id_hex": ACCOUNT})
        await asyncio.wait_for(task, 5)
        await fake.stop()
        return got
    got = run(body())
    msg = got[0]
    assert isinstance(msg, InboundMessage)
    assert msg.message.text == "hello" and msg.message.sender.account_id_hex == "c3" * 32
    assert msg.reply_to is not None and msg.reply_to.message_id_hex == "e5" * 32
    assert isinstance(got[1], GroupStateChanged) and got[1].change == "member_added"
    assert isinstance(got[2], OtherEvent) and got[2].type == "message_edited"


def _frame(**body: object) -> bytes:
    return json.dumps({"marmot_agent_control": "marmot.agent-control.v2", "id": "r1", **body}).encode()


def test_decode_event_is_strict_for_known_types() -> None:
    # sender.is_self missing: a known type with a missing field is a protocol error, not a partial dict.
    bad = _frame(type="inbound_message", account_id_hex="aa", group_id_hex="bb",
                 message={"message_id_hex": "cc", "text": "x", "recorded_at": 1,
                          "sender": {"account_id_hex": "dd"}})
    with pytest.raises(ProtocolError):
        decode_event(bad, "r1")


def test_peer_error_codes_are_allowlisted() -> None:
    frame = _frame(type="error", code="npub1-leaked value", message="m", retryable=False)
    with pytest.raises(ControlError) as exc:
        decode_event(frame, "r1")
    assert exc.value.code == "unrecognised" and "leaked" not in str(exc.value)


def test_decode_event_checks_protocol_and_id() -> None:
    with pytest.raises(ProtocolError, match="id"):
        decode_event(_frame(type="ack"), "other")
    wrong = json.dumps({"marmot_agent_control": "v1", "id": "r1", "type": "ack"}).encode()
    with pytest.raises(ProtocolError, match="protocol"):
        decode_event(wrong, "r1")


def test_oversized_frames_are_rejected_before_sending(tmp_path: Path) -> None:
    client = ControlClient(tmp_path / "s.sock", None)
    with pytest.raises(ControlError, match="too large"):
        run(client.send_final(ACCOUNT, "b2" * 32, "x" * MAX_FRAME, None, "k"))


def test_call_rejects_a_response_of_the_wrong_type(tmp_path: Path) -> None:
    async def body() -> None:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            req = json.loads(await reader.readline())
            writer.write(json.dumps({"marmot_agent_control": "marmot.agent-control.v2", "id": req["id"],
                                     "type": "ack", "message_ids_hex": ["aa"]}).encode() + b"\n")
            await writer.drain()
            writer.close()
        server = await asyncio.start_unix_server(handle, path=str(tmp_path / "s.sock"))
        try:
            with pytest.raises(ProtocolError, match="unexpected response type"):
                await ControlClient(tmp_path / "s.sock", None).send_final(ACCOUNT, "bb", "hi", None, "k")
        finally:
            server.close()
    run(body())


def test_malformed_error_frames_are_protocol_errors() -> None:
    with pytest.raises(ProtocolError) as exc:
        decode_event(_frame(type="error", code=7, message="secret-ish", retryable=False), "r1")
    assert str(exc.value) == "malformed error frame"


def test_error_codes_with_digits_are_not_echoed() -> None:
    with pytest.raises(ControlError) as exc:
        decode_event(_frame(type="error", code="abc123secret", message="m", retryable=False), "r1")
    assert "unrecognised" in str(exc.value) and exc.value.code == "unrecognised"
    assert "abc123secret" not in str(exc.value)
    with pytest.raises(ControlError) as exc2:
        decode_event(_frame(type="error", code="not_group_admin", message="m", retryable=False), "r1")
    assert exc2.value.code == "not_group_admin" and "not_group_admin" in str(exc2.value)


def test_all_letter_unknown_error_code_is_not_echoed() -> None:
    with pytest.raises(ControlError) as exc:
        decode_event(_frame(type="error", code="secrettoken", message="m", retryable=False), "r1")
    assert exc.value.code == "unrecognised"
    assert "secrettoken" not in str(exc.value)


@pytest.mark.parametrize("code", ["backend", "unknown_member"])
def test_membership_error_codes_are_peer_errors_and_echoed(code: str) -> None:
    # S4b: adding a present member is `backend`; removing a non-member is `unknown_member`.
    with pytest.raises(PeerError) as exc:
        decode_event(_frame(type="error", code=code, message="connector failed", retryable=False), "r1")
    assert exc.value.code == code and not exc.value.retryable


def test_only_an_error_frame_is_a_peer_error() -> None:
    with pytest.raises(ProtocolError) as exc:
        decode_event(b"not json", "r1")
    assert not isinstance(exc.value, PeerError)


def test_member_add_and_remove_requests(tmp_path: Path) -> None:
    async def body() -> None:
        fake = FakeWnAgent(tmp_path / "s.sock")
        await fake.start()
        client = ControlClient(tmp_path / "s.sock", "test-token")
        await client.group_member_add(ACCOUNT, fake.group_id, ["d4" * 32])
        assert fake.member_count == 3
        await client.group_member_remove(ACCOUNT, fake.group_id, ["d4" * 32])
        assert fake.member_count == 2
        add, remove = fake.requests
        assert (add["type"], add["members"], add["initial_admins"]) == ("group_member_add", ["d4" * 32], [])
        assert remove["type"] == "group_member_remove" and remove["members"] == ["d4" * 32]
        fake.membership_mode = "fail"
        with pytest.raises(PeerError):
            await client.group_member_add(ACCOUNT, fake.group_id, ["d4" * 32])
        await fake.stop()
    run(body())
