import asyncio
import stat
from pathlib import Path

import pytest
from test_admind_daemon import Harness, needs_tmux, run_with

from heterodyne.admind import ctl
from heterodyne.admind.audit import Audit
from heterodyne.marmot.nip19 import hex_to_npub


def test_round_trip_and_mode(tmp_path: Path) -> None:
    seen: list[ctl.CtlRequest] = []

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        seen.append(req)
        return ctl.CtlReply("committed", "ok")

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            reply = await ctl.request(path, ctl.CtlRequest("add", "b"))
            assert reply == ctl.CtlReply("committed", "ok")
            # Any policy name is allowed (finding 13); the daemon looks it up exactly.
            odd = await ctl.request(path, ctl.CtlRequest("add", "../x"))
            assert odd == ctl.CtlReply("committed", "ok")
            for bad in (ctl.CtlRequest("add", "a\x1b[2Jb"), ctl.CtlRequest("add", "x" * 129),
                        ctl.CtlRequest("add", ""), ctl.CtlRequest("add"), ctl.CtlRequest("rearm", "b")):
                assert (await ctl.request(path, bad)).result == "refused"
        finally:
            await server.close()

    asyncio.run(body())
    assert seen == [ctl.CtlRequest("add", "b"), ctl.CtlRequest("add", "../x")]
    text = (tmp_path / "s" / "audit.jsonl").read_text()
    assert "\\u001b" not in text and "x" * 129 not in text      # refused names are never logged


def test_malformed_request_is_refused(tmp_path: Path) -> None:
    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        raise AssertionError("must not be called")

    async def body() -> bytes:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(b'{"op": "format-disk"}\n')
            await writer.drain()
            line = await reader.readline()
            writer.close()
            return line
        finally:
            await server.close()

    assert b'"refused"' in asyncio.run(body())


def test_handler_error_is_fixed_wording(tmp_path: Path) -> None:
    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        raise RuntimeError("detail that must not reach the host")

    async def body() -> ctl.CtlReply:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            return await ctl.request(path, ctl.CtlRequest("rearm"))
        finally:
            await server.close()

    reply = asyncio.run(body())
    assert reply == ctl.CtlReply("failed", "admind hit an internal error; see the audit log.")
    assert '"error": "RuntimeError"' in (tmp_path / "s" / "audit.jsonl").read_text()
    assert "must not reach" not in (tmp_path / "s" / "audit.jsonl").read_text()


def test_names_are_redacted_in_the_audit(tmp_path: Path) -> None:
    npub = hex_to_npub("c3" * 32)

    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        return ctl.CtlReply("refused", "no")

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, handler, Audit(tmp_path / "s" / "audit.jsonl"))
        await server.start()
        try:
            await ctl.request(path, ctl.CtlRequest("add", npub))
        finally:
            await server.close()

    asyncio.run(body())
    assert npub not in (tmp_path / "s" / "audit.jsonl").read_text()


def _handler_ok() -> ctl.Handler:
    async def handler(req: ctl.CtlRequest) -> ctl.CtlReply:
        return ctl.CtlReply("rearmed", "ok")
    return handler


def test_a_stale_socket_is_replaced(tmp_path: Path) -> None:
    async def serve_twice(path: Path) -> None:
        first = ctl.CtlServer(path, _handler_ok(), Audit(tmp_path / "audit.jsonl"))
        await first.start()
        assert first.server is not None
        first.server.close()                       # leaves the socket file behind, like a crash
        second = ctl.CtlServer(path, _handler_ok(), Audit(tmp_path / "audit.jsonl"))
        await second.start()
        try:
            assert (await ctl.request(path, ctl.CtlRequest("rearm"))).result == "rearmed"
        finally:
            await second.close()
        await first.close()                        # the old instance no longer owns the path
        assert not path.exists()

    asyncio.run(serve_twice(tmp_path / "s" / ctl.CTL_SOCKET))


def test_a_regular_file_is_refused_and_survives_close(tmp_path: Path) -> None:
    regular = tmp_path / "t" / ctl.CTL_SOCKET
    regular.parent.mkdir()
    regular.write_text("keep")

    async def body() -> None:
        server = ctl.CtlServer(regular, _handler_ok(), Audit(tmp_path / "audit.jsonl"))
        with pytest.raises(OSError):
            await server.start()
        await server.close()

    asyncio.run(body())
    assert regular.read_text() == "keep"


def test_a_symlink_to_a_live_socket_is_refused_and_the_target_untouched(tmp_path: Path) -> None:
    async def body() -> None:
        target = ctl.CtlServer(tmp_path / "other" / "x.sock", _handler_ok(), Audit(tmp_path / "audit.jsonl"))
        await target.start()
        try:
            link = tmp_path / "s" / ctl.CTL_SOCKET
            link.parent.mkdir()
            link.symlink_to(target.path)
            server = ctl.CtlServer(link, _handler_ok(), Audit(tmp_path / "audit.jsonl"))
            with pytest.raises(OSError):
                await server.start()
            await server.close()
            assert link.is_symlink() and target.path.exists()
            assert (await ctl.request(target.path, ctl.CtlRequest("rearm"))).result == "rearmed"
        finally:
            await target.close()

    asyncio.run(body())


def test_request_size_limit_is_exact(tmp_path: Path) -> None:
    async def send(path: Path, body_len: int) -> bytes:
        base = b'{"op":"rearm"}'
        raw = base + b" " * (body_len - len(base)) + b"\n"      # whitespace-padded, still valid JSON
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(raw)
        await writer.drain()
        line = await reader.readline()
        writer.close()
        return line

    async def body() -> None:
        path = tmp_path / "s" / ctl.CTL_SOCKET
        server = ctl.CtlServer(path, _handler_ok(), Audit(tmp_path / "audit.jsonl"))
        await server.start()
        try:
            assert b'"rearmed"' in await send(path, ctl.MAX_REQUEST)        # the newline is not counted
            assert b"malformed" in await send(path, ctl.MAX_REQUEST + 1)
        finally:
            await server.close()

    asyncio.run(body())


def test_an_oversized_reply_is_unavailable_not_a_traceback(tmp_path: Path) -> None:
    async def body() -> None:
        path = tmp_path / ctl.CTL_SOCKET

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readline()
            writer.write(b"x" * 200_000 + b"\n")
            await writer.drain()
            writer.close()
        server = await asyncio.start_unix_server(serve, path=str(path))
        try:
            with pytest.raises(ctl.CtlUnavailable):
                await ctl.request(path, ctl.CtlRequest("rearm"), timeout=5)
        finally:
            server.close()

    asyncio.run(body())


def test_no_daemon(tmp_path: Path) -> None:
    async def body() -> None:
        try:
            await ctl.request(tmp_path / ctl.CTL_SOCKET, ctl.CtlRequest("rearm"), timeout=1)
        except ctl.CtlUnavailable:
            return
        raise AssertionError("expected CtlUnavailable")

    asyncio.run(body())


# --- the daemon -----------------------------------------------------------------------------------
NPUB = hex_to_npub("c3" * 32)


def test_on_ctl_redacts_the_message(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    async def refuse(op: str, name: str) -> tuple[str, str]:
        return "refused", f"{NPUB} is not an operator"
    h.daemon.change_membership = refuse  # type: ignore[method-assign]
    reply = asyncio.run(h.daemon.on_ctl(ctl.CtlRequest("add", NPUB)))
    assert reply.result == "refused"
    assert NPUB not in reply.message


def test_on_ctl_survives_a_failed_flush(tmp_path: Path) -> None:
    h = Harness(tmp_path)

    async def commit(op: str, name: str) -> tuple[str, str]:
        return "committed", "done"

    async def broken() -> None:
        raise OSError("disk")
    h.daemon.change_membership = commit  # type: ignore[method-assign]
    h.daemon.flush = broken  # type: ignore[method-assign]
    reply = asyncio.run(h.daemon.on_ctl(ctl.CtlRequest("add", "b")))
    assert reply == ctl.CtlReply("committed", "done")
    audit = (h.settings.state_dir / "audit.jsonl").read_text()
    assert '"action": "flush-failed"' in audit and '"kind": "ctl"' in audit


def test_on_ctl_rearm_goes_to_rearm(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    flushed: list[bool] = []

    async def rearm() -> tuple[str, str]:
        return "rearmed", "ok"

    async def flush() -> None:
        flushed.append(True)
    h.daemon.rearm = rearm  # type: ignore[method-assign]
    h.daemon.flush = flush  # type: ignore[method-assign]
    assert asyncio.run(h.daemon.on_ctl(ctl.CtlRequest("rearm"))) == ctl.CtlReply("rearmed", "ok")
    assert flushed == [True]


@needs_tmux
def test_run_serves_and_removes_the_control_socket(tmp_path: Path) -> None:
    seen: list[Path] = []

    async def scenario(h: Harness) -> None:
        path = h.settings.state_dir / ctl.CTL_SOCKET
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        reply = await ctl.request(path, ctl.CtlRequest("add", "nobody"), timeout=30)
        assert reply.result == "refused" and "nobody" in reply.message
        seen.append(path)

    run_with(tmp_path, scenario)
    assert not seen[0].exists()
