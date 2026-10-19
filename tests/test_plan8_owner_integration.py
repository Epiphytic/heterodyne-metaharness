import asyncio
from uuid import uuid4

import msgspec
from test_schedule_admission import installed

from heterodyne.config import paths
from heterodyne.wsd.ctl import CtlServer
from heterodyne.wsd.health import read
from heterodyne.wsd.maintenance_protocol import EffectWake, PairedBackup, request
from heterodyne.wsd.schedule_units import render


async def until(check):
    async with asyncio.timeout(3):
        while not check():
            await asyncio.sleep(0.01)


def test_systemd_has_no_internal_loops_and_heartbeat_precedes_recovery(tmp_path, monkeypatch):
    async def exercise():
        d, manifest = installed(tmp_path)
        d.admission_ready = False
        path = d.s.state_dir / "schedule-manifest.json"
        path.write_bytes(msgspec.json.encode(manifest))
        path.chmod(0o600)
        release = asyncio.Event()
        timers = []

        async def startup():
            await release.wait()

        async def timer(*args):
            timers.append(args)

        monkeypatch.setattr(d, "_startup", startup)
        monkeypatch.setattr(d, "_every", timer)
        stop = asyncio.Event()
        task = asyncio.create_task(d.serve(stop))
        try:
            await until(lambda: (d.s.state_dir / "health.json").exists())
            snapshot = read(d.s.state_dir / "health.json", manifest.installation_id, manifest.generation)
            assert snapshot.recovery == "in_progress" and not snapshot.listener_ready
            release.set()
            await until(lambda: d.admission_ready)
            assert not timers
            assert d.effects is not None
            assert d.effects.cache.state == "error"  # missing effects cannot prevent core listener
        finally:
            stop.set()
            await task

    asyncio.run(exercise())


def test_maintenance_wire_refuses_producers_and_wrong_backup_owner(tmp_path):
    async def exercise():
        d, m = installed(tmp_path)
        server = CtlServer(d.s.socket, d.handle)
        server.maintenance_handler = d.maintenance_request
        await server.start()
        try:
            effect = EffectWake("effect-wake", 1, m.installation_id, m.generation, str(uuid4()), "digest")
            receipt = await request(d.s.socket, effect)
            assert receipt is not None
            assert receipt.status == "refused" and receipt.reason == "producer_disabled"
            backup = PairedBackup(
                "paired-backup",
                1,
                m.installation_id,
                m.generation,
                str(uuid4()),
                "c" * 64,
                (1 << 64) - 1,
                "unknown",
            )
            receipt = await request(d.s.socket, backup)
            assert receipt is not None
            assert receipt.status == "refused" and receipt.reason == "owner_unavailable"
        finally:
            await server.close()
            d.drain_now(3)

    asyncio.run(exercise())


def test_render_retains_arbitrary_approved_cadence_and_pinned_release(tmp_path):
    d, m = installed(tmp_path)
    units = render(
        msgspec.structs.replace(m, jobs=("babysitter", "pickup", "reconcile")),
        "/usr/bin/python3",
        {"PATH": "/usr/bin"},
    )
    assert "OnUnitActiveSec=20" in units["heterodyne-pickup.timer"]
    assert "OnUnitActiveSec=300" in units["heterodyne-babysitter.timer"]
    assert "scheduled-wake pickup" in units["heterodyne-pickup.service"]
    assert m.release_hash in units["heterodyne-pickup.service"]
    assert "Restart=" not in units["heterodyne-pickup.service"]
    assert "WakeSystem=false" in units["heterodyne-reconcile.timer"]

    # Service settings append /wsd; the unit must pin the shared state root.
    for job in ("pickup", "reconcile", "babysitter"):
        text = units[f"heterodyne-{job}.service"]
        environment = dict(line.removeprefix("Environment=").split("=", 1)
                           for line in text.splitlines() if line.startswith("Environment="))
        assert paths.state_dir(environment) / "wsd" == d.s.state_dir
        assert paths.state_dir(environment) / "alerts" == d.s.state_dir.parent / "alerts"
