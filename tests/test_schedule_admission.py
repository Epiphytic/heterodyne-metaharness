"""Only disposable journals/fake schedulers; never installed queue/timer operations."""

import asyncio
import concurrent.futures as cf
import threading
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import msgspec
import pytest
from test_wsd_daemon import settings

from heterodyne.wsd.daemon import JobFailed, Lane, Parts, Wsd
from heterodyne.wsd.schedule_manifest import (
    ManifestInvalid,
    ScheduleManifest,
    load,
    location,
    validate,
)
from heterodyne.wsd.scheduled import ScheduledWakeRequest
from heterodyne.wsd.scheduler import Outcome


def installed(tmp_path: Path) -> tuple[Wsd, ScheduleManifest]:
    s = replace(settings(tmp_path), timer_owner="systemd", backstop_seconds=20, reconcile_seconds=300)
    s.state_dir.mkdir(parents=True)
    s.btq_checkout.mkdir()
    manifest = ScheduleManifest(1, str(uuid4()), str(uuid4()), __import__('os').geteuid(),
                                location(s.config_dir), location(s.state_dir), location(s.btq_checkout),
                                'a' * 64, 'b' * 64, 20, 300, ('pickup', 'reconcile'),
                                tuple(w.name for w in s.workstreams))
    # No producer/queue methods are called: the tests supply only bounded fake jobs.
    daemon = Wsd(s, Parts(None, None, None, {w.name: None for w in s.workstreams}))
    daemon._arm_on = lambda *_: None
    daemon.manifest = manifest
    daemon.admission_ready = True
    return daemon, manifest


def wake(m: ScheduleManifest, job: str = 'pickup') -> ScheduledWakeRequest:
    return ScheduledWakeRequest('scheduled-wake', 1, m.installation_id, m.generation, str(uuid4()), job)


def test_manifest_private_exact_installed_roots(tmp_path):
    d, m = installed(tmp_path)
    validate(m, d.s)
    path = d.s.state_dir / 'schedule-manifest.json'
    path.write_bytes(msgspec.json.encode(m))
    path.chmod(0o600)
    assert load(d.s) == m
    path.chmod(0o644)
    with pytest.raises(ManifestInvalid):
        load(d.s)
    with pytest.raises(ManifestInvalid):
        validate(msgspec.structs.replace(m, backstop_seconds=60), d.s)
    with pytest.raises(ManifestInvalid):
        validate(msgspec.structs.replace(m, workstreams=('unknown',)), d.s)


def test_origins_frozen_on_dequeue_and_bounded():
    stop = threading.Lock()
    lane = Lane('fake', stop)
    begun, finish = threading.Event(), threading.Event()
    with stop:
        first, joined = lane.scheduled(('scheduled-pickup', None),
                                       lambda: (begun.set(), finish.wait(2)), str(uuid4()))
        assert not joined
        for _ in range(70):
            same, joined = lane.scheduled(('scheduled-pickup', None), lambda: None, str(uuid4()))
            assert same is first and joined
        origin = lane.origins[first]
        assert origin.count == 71 and len(origin.ids) == 64 and origin.truncated
    assert begun.wait(2)
    second, joined = lane.scheduled(('scheduled-pickup', None), lambda: None, str(uuid4()))
    assert not joined and second is not first
    assert origin.count == 71
    assert 'ids' not in str(lane.origin_snapshot())
    finish.set()
    first.result(2)
    second.result(2)
    lane.close()


def test_owner_generation_closed_lane_preflight_no_partial(tmp_path):
    async def run():
        d, m = installed(tmp_path)
        req = wake(m)
        d.s = replace(d.s, timer_owner='internal')
        assert (await d.scheduled(req)).data['scheduled']['reason'] == 'owner_mismatch'
        d.s = replace(d.s, timer_owner='systemd')
        assert (await d.scheduled(msgspec.structs.replace(req, generation=str(uuid4())))) \
            .data['scheduled']['reason'] == 'generation_mismatch'
        d.lanes[m.workstreams[0]].close()
        reply = await d.scheduled(req)
        assert reply.result == 'refused'
        assert all(not lane.jobs for lane in d.lanes.values())
        d.drain_now(2)
    asyncio.run(run())


def test_scheduled_uses_backstop_not_operator_and_observes_fatal(tmp_path):
    async def run():
        d, m = installed(tmp_path)
        d.fatal = asyncio.get_running_loop().create_future()
        calls = []
        d._backstop = lambda name: calls.append(name) or Outcome.HELD
        d._operator_pickup = lambda _: pytest.fail('scheduled operator trigger')
        reply = await d.scheduled(wake(m))
        assert reply.result == 'ok' and reply.data['scheduled']['targets'] == '1'
        for _ in range(30):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls == list(m.workstreams)
        # A recorded ordinary job error is not fatal; failed persistence/unexpected errors are.
        ordinary = cf.Future()
        ordinary.set_exception(JobFailed('recorded'))
        d._scheduled_result(ordinary)
        assert not d.fatal.done()
        broken = cf.Future()
        broken.set_exception(RuntimeError('audit failed'))
        d._scheduled_result(broken)
        with pytest.raises(RuntimeError, match='audit failed'):
            d.fatal.result()
        d.drain_now(2)
    asyncio.run(run())


def test_unexpected_partial_submit_trips_fatal(tmp_path, monkeypatch):
    async def run():
        d, m = installed(tmp_path)
        d.fatal = asyncio.get_running_loop().create_future()
        lane = d.lanes[m.workstreams[0]]
        monkeypatch.setattr(lane, 'scheduled', lambda *args: (_ for _ in ()).throw(RuntimeError('partial')))
        with pytest.raises(RuntimeError, match='partial'):
            await d.scheduled(wake(m))
        assert not d.admission_ready
        with pytest.raises(RuntimeError, match='partial'):
            d.fatal.result()
        d.drain_now(2)
    asyncio.run(run())
