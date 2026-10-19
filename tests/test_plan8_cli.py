import asyncio
from uuid import uuid4

import pytest

from heterodyne.wsd.cli import wsd_main
from heterodyne.wsd.maintenance_protocol import EffectWake, Receipt, request


def test_missing_daemon_is_unknown_not_failed_effect(tmp_path):
    value = EffectWake("effect-wake", 1, str(uuid4()), str(uuid4()), str(uuid4()), "digest")
    assert asyncio.run(request(tmp_path / "missing.sock", value)) is None


def test_invalid_maintenance_request_never_connects(tmp_path):
    value = EffectWake("effect-wake", 1, str(uuid4()), str(uuid4()), str(uuid4()), "../bad")
    with pytest.raises(ValueError):
        asyncio.run(request(tmp_path / "missing.sock", value))


def test_cli_uses_installed_identity_and_one_attempt(tmp_path, monkeypatch, capsys):
    from test_schedule_admission import installed

    from heterodyne.wsd import cli

    d, manifest = installed(tmp_path)
    calls = []
    monkeypatch.setattr(cli, "_settings", lambda: d.s)
    monkeypatch.setattr(cli, "load_manifest", lambda _: manifest)

    async def fake_request(path, value):
        calls.append((path, value))
        return Receipt(value.request_id, "refused", "producer_disabled")

    monkeypatch.setattr(cli, "maintenance_request", fake_request)
    assert wsd_main(["effect-wake", "digest"]) == 1
    assert capsys.readouterr().out == "refused:producer_disabled\n"
    assert len(calls) == 1
    assert calls[0][1].installation_id == manifest.installation_id
