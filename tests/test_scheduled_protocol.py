"""Offline Plan8 wire boundaries; no scheduler or native effects."""

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import msgspec
import pytest

from heterodyne.wsd.ctl import CtlReply, CtlRequest, CtlServer
from heterodyne.wsd.scheduled import (
    InvalidWake,
    ScheduledWakeRequest,
    decode_wake,
    decode_wake_reply,
    encode_wake,
    scheduled_request,
)


def payload() -> dict[str, object]:
    return {"op": "scheduled-wake", "version": 1, "installation_id": str(uuid4()),
            "generation": str(uuid4()), "request_id": str(uuid4()), "job": "pickup"}


def test_roundtrip_keeps_legacy_generation_separate() -> None:
    raw = payload()
    wake = decode_wake(json.dumps(raw).encode())
    assert isinstance(wake, ScheduledWakeRequest)
    assert decode_wake(encode_wake(wake)) == wake
    assert CtlRequest("resolve", generation=3).generation == 3


@pytest.mark.parametrize("key", tuple(payload()))
def test_all_fields_required(key: str) -> None:
    raw = payload()
    del raw[key]
    with pytest.raises(InvalidWake):
        decode_wake(json.dumps(raw).encode())


@pytest.mark.parametrize("key,value", [
    ("op", "tick"), ("op", None), ("version", True), ("version", 1.0),
    ("version", 2), ("version", "1"), ("generation", 3),
    ("job", "backup_bundle"), ("job", "PICKUP"), ("job", []),
    ("request_id", "123"), ("generation", "{9b4ac8ab-4f66-48ee-8ebb-84ed6383d899}"),
    ("installation_id", "A691605E-52FE-4BC0-AC24-ACEE2DAE1E62"), ("installation_id", None),
])
def test_wrong_types_and_variants_refused(key: str, value: object) -> None:
    raw = payload()
    raw[key] = value
    with pytest.raises(InvalidWake):
        decode_wake(json.dumps(raw).encode())


def test_unknown_fields_and_duplicate_keys_refused() -> None:
    raw = payload()
    raw["ws"] = "other-target"
    with pytest.raises(InvalidWake):
        decode_wake(json.dumps(raw).encode())
    del raw["ws"]
    encoded = json.dumps(raw).encode()
    for repeated in (b'"version":1', b'"job":"pickup"', b'"job":"reconcile"'):
        with pytest.raises(InvalidWake):
            decode_wake(encoded[:-1] + b"," + repeated + b"}")


@pytest.mark.parametrize("wire", [b"[]", b"null", b"true", b"{}", b"{", b"\xff",
                                  b"[" * 1000 + b"]" * 1000])
def test_invalid_json_and_shapes_refused(wire: bytes) -> None:
    with pytest.raises(InvalidWake):
        decode_wake(wire)


def test_exact_payload_budget() -> None:
    wire = json.dumps(payload()).encode()
    assert decode_wake(wire + b" " * (1024 - len(wire)))
    with pytest.raises(InvalidWake):
        decode_wake(wire + b" " * (1025 - len(wire)))


def test_typed_construction_cannot_bypass_encode_validation() -> None:
    malformed = ScheduledWakeRequest("scheduled-wake", 1, "bad", "bad", "bad", "pickup")
    with pytest.raises(InvalidWake):
        encode_wake(malformed)


def reply_payload(request_id: str) -> dict[str, object]:
    return {"result": "ok", "message": "queued", "data": {"scheduled": {
        "version": "1", "request_id": request_id, "status": "queued", "reason": "none",
        "targets": "2", "joined": "1"}}}


def test_reply_distinguishes_admission_from_unconfirmed() -> None:
    request_id = str(uuid4())
    raw = reply_payload(request_id)
    admitted = decode_wake_reply(json.dumps(raw).encode() + b"\n", request_id)
    assert admitted is not None
    assert (admitted.status, admitted.targets, admitted.joined) == ("queued", 2, 1)
    for result in ("refused", "failed"):
        assert decode_wake_reply(json.dumps({"result": result, "message": "generic",
                                             "data": {}}).encode() + b"\n", request_id) is None


@pytest.mark.parametrize("key,value", [
    ("version", 1), ("request_id", "efe0e283-4efc-4a75-810d-021cc1fdede1"), ("status", "success"),
    ("reason", "stopping"), ("targets", "129"), ("targets", "02"),
    ("targets", "-1"), ("targets", 2), ("joined", "3"), ("joined", "1.0"),
])
def test_reply_must_match_exact_admission_contract(key: str, value: object) -> None:
    request_id = str(uuid4())
    raw = reply_payload(request_id)
    data = raw["data"]
    assert isinstance(data, dict)
    data["scheduled"][key] = value
    with pytest.raises(InvalidWake):
        decode_wake_reply(json.dumps(raw).encode() + b"\n", request_id)


def test_typed_refusal_has_zero_targets() -> None:
    request_id = str(uuid4())
    raw = {"result": "refused", "message": "held", "data": {"scheduled": {
        "version": "1", "request_id": request_id, "status": "refused", "reason": "maintenance",
        "targets": "0", "joined": "0"}}}
    receipt = decode_wake_reply(json.dumps(raw).encode() + b"\n", request_id)
    assert receipt is not None and receipt.reason == "maintenance"
    raw["data"]["scheduled"]["targets"] = "1"
    with pytest.raises(InvalidWake):
        decode_wake_reply(json.dumps(raw).encode() + b"\n", request_id)


def test_reply_frame_and_duplicate_limits() -> None:
    request_id = str(uuid4())
    wire = json.dumps(reply_payload(request_id)).encode()
    assert decode_wake_reply(wire + b" " * (4095 - len(wire)) + b"\n", request_id)
    for bad in (wire, wire + b"\n\n", wire + b" " * (4096 - len(wire)) + b"\n",
                wire[:-1] + b',"result":"ok"}\n'):
        with pytest.raises(InvalidWake):
            decode_wake_reply(bad, request_id)


@pytest.mark.parametrize("response", ["queued", "mismatch", "oversized", "generic", "hang"])
def test_client_one_send_and_bounded_transport(response: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import heterodyne.wsd.scheduled as scheduled

    assert scheduled.WAKE_SECONDS == 5.0
    monkeypatch.setattr(scheduled, "WAKE_SECONDS", 0.1)

    async def scenario(path: Path) -> None:
        request = decode_wake(json.dumps(payload()).encode())
        received: list[bytes] = []
        release = asyncio.Event()
        finished = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                received.append(await reader.readline())
                wire = json.dumps(reply_payload(request.request_id)).encode() + b"\n"
                if response == "hang":
                    await release.wait()
                    return
                if response == "mismatch":
                    wire = json.dumps(reply_payload(str(uuid4()))).encode() + b"\n"
                elif response == "oversized":
                    wire = b"x" * 4096 + b"\n"
                elif response == "generic":
                    wire = b'{"result":"refused","message":"closing","data":{}}\n'
                writer.write(wire)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_unix_server(handler, path=str(path))
        async with server:
            result = await asyncio.wait_for(scheduled_request(path, request), 1)
            release.set()
            await asyncio.wait_for(finished.wait(), 1)
            assert len(received) == 1
            assert decode_wake(received[0][:-1]) == request
            assert (result is not None) == (response == "queued")

    with tempfile.TemporaryDirectory(prefix="p8-", dir="/tmp") as directory:
        asyncio.run(scenario(Path(directory) / "ctl"))


def test_client_missing_listener_is_unconfirmed(tmp_path: Path) -> None:
    request = decode_wake(json.dumps(payload()).encode())
    assert asyncio.run(scheduled_request(tmp_path / "absent", request)) is None


@pytest.mark.parametrize("mode", ["valid", "unknown", "duplicate", "oversize", "no-newline",
                                  "closing", "unavailable", "legacy"])
def test_server_dispatches_only_strict_scheduled_variant(mode: str) -> None:
    async def scenario(path: Path) -> None:
        legacy_calls: list[CtlRequest] = []
        scheduled_calls: list[ScheduledWakeRequest] = []

        async def legacy(req: CtlRequest) -> CtlReply:
            legacy_calls.append(req)
            return CtlReply("ok", "legacy")

        async def scheduled(req: ScheduledWakeRequest) -> CtlReply:
            scheduled_calls.append(req)
            return msgspec.convert(reply_payload(req.request_id), type=CtlReply)

        server = CtlServer(path, legacy,
                           scheduled_handler=None if mode == "unavailable" else scheduled)
        await server.start()
        try:
            request = payload()
            if mode == "unknown":
                request["ws"] = "other"
            wire = json.dumps(request).encode()
            if mode == "duplicate":
                wire = wire[:-1] + b',"version":1}'
            elif mode == "oversize":
                wire += b" " * (1025 - len(wire))
            elif mode == "legacy":
                wire = b'{"op":"resolve","ws":"alpha","session":"hz0123456789ab",' \
                       b'"generation":3,"action":"discard","old-unknown-field":true}'
            if mode == "closing":
                server.closing = True
            reader, writer = await asyncio.open_unix_connection(str(path))
            try:
                writer.write(wire if mode == "no-newline" else wire + b"\n")
                if mode == "no-newline":
                    writer.write_eof()
                await writer.drain()
                response = msgspec.json.decode(await asyncio.wait_for(reader.readline(), 1),
                                               type=CtlReply)
                if mode == "valid":
                    assert response.result == "ok" and len(scheduled_calls) == 1
                elif mode == "legacy":
                    assert response.message == "legacy" and len(legacy_calls) == 1
                    assert legacy_calls[0].generation == 3
                else:
                    assert response.result == "refused" and not response.data
                assert len(scheduled_calls) == (1 if mode == "valid" else 0)
                assert len(legacy_calls) == (1 if mode == "legacy" else 0)
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.close()

    with tempfile.TemporaryDirectory(prefix="p8-", dir="/tmp") as directory:
        asyncio.run(scenario(Path(directory) / "ctl"))
