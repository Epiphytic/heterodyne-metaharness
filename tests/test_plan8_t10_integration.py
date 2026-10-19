"""Disposable compatibility checks for the Plan8 / managed-install composition."""

import asyncio

from test_schedule_admission import installed

from heterodyne.wsd import ctl
from heterodyne.wsd.daemon import HARNESS_LANE
from heterodyne.wsd.health import read
from heterodyne.wsd.scheduler import Outcome


def test_t10_recovery_precedes_plan8_pickups(tmp_path, monkeypatch):
    daemon, _ = installed(tmp_path)
    seen = []
    monkeypatch.setattr(daemon, 'harness_recover', lambda: seen.append('harness'))
    monkeypatch.setattr(daemon, 'recover_one', lambda name: seen.append(('recover', name)))

    def pickup(name, trigger):
        assert seen[0] == 'harness'
        assert all(('recover', ws) in seen for ws in daemon.parts.schedulers)
        seen.append(('pickup', name))
        return Outcome.NOTHING

    monkeypatch.setattr(daemon, 'pickup_one', pickup)

    async def exercise():
        try:
            await daemon._startup()
        finally:
            for lane in daemon.lanes.values():
                lane.close()

    asyncio.run(exercise())
    assert len(seen) == 1 + 2 * len(daemon.parts.schedulers)


def test_harness_and_scheduled_hold_share_control_without_mutual_rejection(tmp_path):
    daemon, _ = installed(tmp_path)

    async def exercise():
        try:
            held = await daemon.handle(ctl.CtlRequest('scheduled-hold'))
            assert held.result == 'ok' and daemon.maintenance
            request = ctl.CtlRequest('harness', harness=ctl.HarnessRequest('list'))
            assert ctl.refusal(request) == ''
            reply = await daemon.handle(request)
            assert reply.result == 'refused'  # no real install ports in this disposable fixture
            assert 'not set up' in reply.message
        finally:
            for lane in daemon.lanes.values():
                lane.close()

    asyncio.run(exercise())


def test_health_scheduled_lane_budget_excludes_t10_internal_lane(tmp_path):
    daemon, manifest = installed(tmp_path)
    assert HARNESS_LANE in daemon.lanes
    asyncio.run(daemon._publish_health('boot'))
    snapshot = read(daemon.s.state_dir / 'health.json', manifest.installation_id, manifest.generation)
    assert set(snapshot.lanes) == set(daemon.parts.schedulers)


def test_harness_advance_obeys_maintenance_without_disabling_normal_ticks(tmp_path, monkeypatch):
    daemon, _ = installed(tmp_path)
    calls = []
    monkeypatch.setattr(daemon.parts.harness, "advance", lambda now: calls.append(now))
    try:
        daemon.harness_advance()
        assert len(calls) == 1
        asyncio.run(daemon.handle(ctl.CtlRequest("scheduled-hold")))
        assert daemon.maintenance
        daemon.harness_advance()
        assert len(calls) == 1
        daemon.maintenance = False
        daemon.harness_advance()
        assert len(calls) == 2
    finally:
        for lane in daemon.lanes.values():
            lane.close()
