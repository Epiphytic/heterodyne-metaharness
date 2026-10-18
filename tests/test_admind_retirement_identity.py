"""Adapter identity limits must disqualify retirement proof, never ordinary approval execution."""

import asyncio
from pathlib import Path

import pytest
from test_admind_retirement import APPROVE, HOLD

from heterodyne.admind.approvals import ApproveBead, MaintenanceHold


@pytest.mark.parametrize("identity_case", ["uncached", "empty", "oversized", "unreadable", "stamp-drift"])
@pytest.mark.parametrize("action", ["approve", "deny"])
def test_hash_unavailability_does_not_prevent_ordinary_decision(
    tmp_path: Path, monkeypatch, identity_case: str, action: str
) -> None:
    async def scenario() -> None:
        from dataclasses import replace

        binary = tmp_path / "adapter"
        binary.write_bytes(b"offline source; never executed")
        port = ApproveBead(binary)
        if identity_case == "empty":
            binary.write_bytes(b"")
            assert not port.identity()
        elif identity_case == "oversized":
            with binary.open("wb") as stream:
                stream.truncate((8 << 20) + 1)
            assert not port.identity()
        elif identity_case == "unreadable":
            from heterodyne.admind import approvals

            def denied(*args, **kwargs):
                raise PermissionError("synthetic execute-only adapter")

            monkeypatch.setattr(approvals.os, "open", denied)
            assert not port.identity()
        elif identity_case == "stamp-drift":
            assert port.identity()
            binary.write_bytes(b"different bounded source")
        assert not port.cached_identity()
        launched = []

        async def run(argv, timeout, cap, on_launch):
            assert on_launch is not None
            on_launch()
            return 0, b"", b""

        monkeypatch.setattr(port, "_run", run)
        assert await port.decide(
            replace(APPROVE, action=action), "offline", lambda: launched.append(True)
        ) == (0, "")
        assert launched == [True]

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["missing", "drift"])
def test_missing_or_drifted_identity_cannot_become_retirement_identity(
    tmp_path: Path, monkeypatch, case: str
) -> None:
    async def scenario() -> None:
        binary = tmp_path / "adapter"
        binary.write_bytes(b"original bounded source")
        port = ApproveBead(binary)
        if case == "drift":
            assert port.identity()

        async def run(argv, timeout, cap, on_launch):
            assert on_launch is not None
            on_launch()
            if case == "drift":
                binary.write_bytes(b"changed bounded source")
            return 75, HOLD, b""

        monkeypatch.setattr(port, "_run", run)
        launched = []
        result = await port.decide(APPROVE, "offline", lambda: launched.append(True))
        assert launched == [True]
        assert isinstance(result, MaintenanceHold)
        assert result.adapter_identity == ""

    asyncio.run(scenario())
