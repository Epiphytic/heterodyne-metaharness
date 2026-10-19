import json

import pytest

from heterodyne.local_alerts import publish, settle
from heterodyne.wsd.effect_slots import Clock, Sample
from heterodyne.wsd.health import predicates


def test_runtime_oserror_is_not_configuration_exit(tmp_path, monkeypatch):
    from test_schedule_admission import installed

    from heterodyne.wsd import cli
    daemon, _ = installed(tmp_path)
    monkeypatch.setattr(cli, '_settings', lambda: daemon.s)
    monkeypatch.setattr(cli, 'build_runtime', lambda *_: None)
    monkeypatch.setattr(cli, 'build_usage_reader', lambda *_: None)
    monkeypatch.setattr(cli, '_factory', lambda *_: None)
    def fail(*_, **__):
        raise OSError('journal full')
    monkeypatch.setattr(cli, 'run', fail)
    with pytest.raises(OSError, match='journal full'):
        cli.wsd_main(['run'])


def test_config_diagnostic_remains_actionable(monkeypatch, capsys):
    from heterodyne.wsd import cli
    def fail():
        raise cli.ConfigError('invalid timer owner')
    monkeypatch.setattr(cli, '_settings', fail)
    assert cli.wsd_main(['run']) == 78
    assert 'invalid timer owner' in capsys.readouterr().err


def test_rejected_clock_jump_does_not_poison_admitted_water():
    clock = Clock(100)
    assert not clock.observe(Sample('boot', 100, 0, True))
    assert clock.observe(Sample('boot', 130, 30, True))
    assert not clock.observe(Sample('boot', 30000000, 31, True))
    assert clock.high_water == 100  # no calendar work was admitted
    assert not clock.observe(Sample('boot', 131, 31, True))
    assert clock.observe(Sample('boot', 161, 61, True))


def test_unready_and_stale_effect_cache_are_unknown():
    from test_plan8_health import snapshot
    assert predicates(snapshot(), utc=1000, monotonic=100)['effect_hold'] == 'unknown'
    assert predicates(snapshot(effect_state='ready', effect_observed_at=800), utc=1000,
                      monotonic=100)['effect_hold'] == 'unknown'


def test_coalesced_retries_are_idempotent_and_cleared_is_not_retired(tmp_path):
    from test_local_alerts import intent
    for number in range(255):
        (tmp_path / f'foreign{number}.json').write_text('{}')
    value = intent()
    assert publish(tmp_path, value) == 'coalesced'
    first = (tmp_path / 'shared_overflow.json').read_bytes()
    assert publish(tmp_path, value) == 'coalesced'
    assert (tmp_path / 'shared_overflow.json').read_bytes() == first
    state = json.loads((tmp_path / '.local-alert-v1.state').read_text())
    assert state['aggregates']['p8']['coalesced'] == 1
    for number in range(255):
        (tmp_path / f'foreign{number}.json').unlink()
    import hashlib
    assert publish(tmp_path, value) == 'written'
    assert settle(tmp_path, value, hashlib.sha256(value.payload()).hexdigest(), 'cleared')
    state = json.loads((tmp_path / '.local-alert-v1.state').read_text())
    assert state['aggregates']['p8']['retired'] == 0


def test_companion_counter_failure_never_suppresses_native_backup(tmp_path):
    from heterodyne.wsd.backup_pairing import Binding, paired_attempt
    calls = []
    result = paired_attempt(tmp_path, Binding('install', 'gen', 'a' * 64),
                            lambda: calls.append('native') or 'succeeded',
                            lambda *_: calls.append('companion'))
    assert result == (None, 'succeeded')
    assert calls == ['native']


def test_first_observation_is_not_corruption_and_fixed_templates(tmp_path):
    from heterodyne.wsd.observer import Observer
    directory = tmp_path / 'state'
    directory.mkdir()
    alerts = tmp_path / 'alerts'
    Observer(directory, alerts, 'install', 'generation').inspect(1000, 100, 'boot')
    payloads = [json.loads(p.read_text()) for p in alerts.glob('p8_*.json')]
    assert len(payloads) == 1
    assert payloads[0]['text'] == ('Plan8 health: unavailable. Scheduled work status requires attention. '
                                   'Remote delivery unconfirmed.')


def test_restart_without_attempt_consumes_failed_local_cycle(tmp_path):
    from heterodyne.wsd.effect_owner import EffectOwner
    from heterodyne.wsd.effects import EffectStore
    path = tmp_path / 'effects.db'
    EffectStore.initialize(path, 'install', 'generation')
    store = EffectStore(path, 'install', 'generation')
    assert store.begin_backup(1, 'unknown') == 'queued'
    store.close()  # crash before naming/submitting a local copy
    owner = EffectOwner(path, 'install', 'generation')
    try:
        owner.opened.result(3)
        assert owner.call(lambda s: s.begin_backup(2, 'succeeded')).result(3) == 'queued'
    finally:
        owner.close()


@pytest.mark.parametrize('named', [False, True])
def test_failed_local_copy_or_missing_manifest_does_not_stall_later_cycles(tmp_path, monkeypatch, named):
    import time

    from heterodyne.wsd import effect_owner
    from heterodyne.wsd.effects import EffectStore
    path = tmp_path / 'effects.db'
    EffectStore.initialize(path, 'install', 'generation')
    if named:
        store = EffectStore(path, 'install', 'generation')
        assert store.begin_backup(1, 'unknown') == 'queued'
        store.backup_attempt(1, 'attempt-without-manifest')
        store.close()
    monkeypatch.setattr(effect_owner, 'copy_bundle',
                        lambda *_: (_ for _ in ()).throw(OSError('copy failed')))
    owner = effect_owner.EffectOwner(path, 'install', 'generation', 'a' * 64)
    try:
        owner.opened.result(3)
        if not named:
            assert owner.backup(1, 'unknown', tmp_path / 'paired-backups',
                                (tmp_path / 'a', tmp_path / 'b')).result(3) == 'queued'
        deadline = time.monotonic() + 3
        while owner.call(lambda s: s.pending_backup()).result(3) is not None:
            assert time.monotonic() < deadline, 'failed local copy still blocks later cycle'
            time.sleep(.01)
        assert owner.call(lambda s: s.begin_backup(2, 'succeeded')).result(3) == 'queued'
    finally:
        owner.close()


def test_private_control_hold_closes_external_ingress_but_is_not_workstream_pause(tmp_path):
    import asyncio
    from uuid import uuid4

    from test_schedule_admission import installed, wake

    from heterodyne.wsd.ctl import CtlRequest, CtlServer, request
    from heterodyne.wsd.maintenance_protocol import EffectWake
    async def exercise():
        daemon, manifest = installed(tmp_path)
        server = CtlServer(daemon.s.socket, daemon.handle)
        await server.start()
        try:
            reply = await request(daemon.s.socket, CtlRequest('scheduled-hold'))
            assert reply.result == 'ok'
            assert not daemon.wanted_pause
            for _ in range(2):
                reply = await daemon.scheduled(wake(manifest))
                assert reply.data['scheduled']['reason'] == 'maintenance'
                effect = EffectWake('effect-wake', 1, manifest.installation_id,
                                    manifest.generation, str(uuid4()), 'digest')
                assert (await daemon.maintenance_request(effect)).result == 'refused'
            assert all(not lane.jobs for lane in daemon.lanes.values())
            assert (await daemon.handle(CtlRequest('scheduled-hold', ws=manifest.workstreams[0]))) \
                .result == 'refused'
        finally:
            await server.close()
            daemon.drain_now(2)
    asyncio.run(exercise())


def test_internal_owner_ignores_stale_external_manifest(tmp_path, monkeypatch):
    import asyncio
    from dataclasses import replace

    from test_schedule_admission import installed
    async def exercise():
        daemon, _ = installed(tmp_path)
        daemon.s = replace(daemon.s, timer_owner='internal')
        (daemon.s.state_dir / 'schedule-manifest.json').write_bytes(b'invalid stale bytes')
        stop = asyncio.Event()
        async def startup():
            stop.set()
        monkeypatch.setattr(daemon, '_startup', startup)
        await daemon.serve(stop)
        assert daemon.manifest is None
        assert daemon.effects is None
    asyncio.run(exercise())


def test_only_admitted_effect_updates_durable_utc_water(tmp_path):
    import sqlite3

    from test_effect_store import ready
    store, path = ready(tmp_path)
    try:
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT utc_water FROM identity').fetchone() == (0,)
        assert store.prepare('job', 1, 130)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT utc_water FROM identity').fetchone() == (130,)
        assert store.clock.high_water == 130
        assert not store.observe(Sample('boot', 129, 31, True))
    finally:
        store.close()


def test_cli_hold_uses_existing_private_control_boundary(tmp_path, monkeypatch):
    from test_schedule_admission import installed

    from heterodyne.wsd import cli
    from heterodyne.wsd.ctl import CtlReply
    daemon, _ = installed(tmp_path)
    calls = []
    monkeypatch.setattr(cli, '_settings', lambda: daemon.s)
    monkeypatch.setattr(cli, '_ask', lambda s, req: calls.append(req) or CtlReply('ok', 'held'))
    assert cli.wsctl_main(['scheduled-hold']) == 0
    assert len(calls) == 1 and calls[0].op == 'scheduled-hold' and calls[0].ws is None


def test_effect_projection_is_fresh_and_reports_uncertainty_without_ready_clock(tmp_path):
    from heterodyne.wsd.effects import EffectStore
    path = tmp_path / 'effects.db'
    EffectStore.initialize(path, 'install', 'generation')
    store = EffectStore(path, 'install', 'generation')
    try:
        assert not store.clock.ready
        assert store.cache.state == 'ready' and store.cache.uncertain == 0
        assert store.begin_backup(1, 'unknown') == 'queued'
        store.finish_backup(1, 'uncertain')
        store.refresh()
        assert store.cache.state == 'ready' and store.cache.uncertain == 1
        assert store.cache.observed_at > 0
    finally:
        store.close()


def test_state_loss_after_first_install_requires_exact_reset_notice(tmp_path, monkeypatch):
    from heterodyne.wsd import observer as module
    from heterodyne.wsd.observer import Observer
    captured = []
    original = module.publish
    def capture(directory, intent):
        captured.append(intent.text)
        return original(directory, intent)
    monkeypatch.setattr(module, 'publish', capture)
    state = tmp_path / 'state'
    state.mkdir()
    alerts = tmp_path / 'alerts'
    observer = Observer(state, alerts, 'install', 'generation')
    observer.inspect(1000, 100, 'boot')
    (state / 'observer-state.json').unlink()
    observer.inspect(1001, 101, 'boot')
    texts = [json.loads(path.read_text())['text'] for path in alerts.glob('p8_*.json')]
    # Reset was locally published and settled; overflow/retired counters are not remote receipts.
    assert ('Plan8 observer state reset; prior diagnostic continuity unknown. '
            'Remote delivery unconfirmed.') in captured
    assert (state / '.observer-installed').exists()
    loaded = json.loads((state / 'observer-state.json').read_text())
    assert loaded['reset'] is None
    assert all(not text.startswith('Workstream daemon') for text in texts)


def test_coalesced_observer_transitions_keep_only_latest_pending_per_code(tmp_path):
    from test_plan8_health import snapshot

    from heterodyne.wsd.health import publish as health_publish
    from heterodyne.wsd.observer import Observer
    state = tmp_path / 'state'
    state.mkdir()
    alerts = tmp_path / 'alerts'
    alerts.mkdir()
    for number in range(255):
        (alerts / f'foreign{number}.json').write_text('{}')
    observer = Observer(state, alerts, 'install', 'generation')
    for cycle in range(300):
        now = 1000 + cycle
        observer.inspect(now, 100 + cycle, 'boot')
        health_publish(state / 'health.json', snapshot(utc=now, sequence=cycle + 1, monotonic=100+cycle,
                       listener_ready_at=100+cycle, effect_state='ready', effect_observed_at=now))
        observer.inspect(now, 100 + cycle, 'boot')
        (state / 'health.json').unlink()
    metadata = json.loads((alerts / '.local-alert-v1.state').read_text())
    assert len(metadata['pending']) <= 7
    assert metadata['aggregates']['p8']['coalesced'] == 600
    assert metadata['aggregates']['p8']['retired'] == 0
    latest = json.loads((state / 'observer-state.json').read_text())
    assert latest['episodes']['unavailable']['sequence'] == 300
