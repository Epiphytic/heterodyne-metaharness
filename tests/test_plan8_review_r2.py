"""Offline regressions for the second HIGH review."""
import asyncio
import errno
import hashlib
import json
import threading
import time

import msgspec
import pytest

from heterodyne.wsd.backup_cycles import Identity, copy_bundle, new_attempt, retain
from heterodyne.wsd.effects import EffectStore


def test_settled_notice_retained_until_capacity_needed(tmp_path):
    from test_local_alerts import intent

    from heterodyne.local_alerts import publish, settle
    first = intent('recovery', 'recovery')
    assert publish(tmp_path, first) == 'written'
    assert settle(tmp_path, first, hashlib.sha256(first.payload()).hexdigest(), 'published')
    assert publish(tmp_path, intent('second')) == 'written'
    assert (tmp_path / first.filename()).read_bytes() == first.payload()
    for i in range(253):
        (tmp_path / f'foreign{i}.json').write_text('{}')
    assert publish(tmp_path, intent('third')) == 'written'
    assert not (tmp_path / first.filename()).exists()
    assert len(list(tmp_path.glob('*.json'))) == 255


@pytest.mark.parametrize('failure', ['read', 'marker'])
def test_transient_io_preserves_observer_epoch(tmp_path, monkeypatch, failure):
    from heterodyne.wsd import observer as module
    directory = tmp_path / 'state'
    directory.mkdir()
    observer = module.Observer(directory, tmp_path / 'alerts', 'install', 'generation')
    observer.inspect(1000, 100, 'boot')
    original = observer.path.read_bytes()
    if failure == 'read':
        real_open = module.os.open
        def fail(path, *args, **kwargs):
            if path == observer.path:
                raise OSError(errno.EMFILE, 'transient descriptors')
            return real_open(path, *args, **kwargs)
        monkeypatch.setattr(module.os, 'open', fail)
    else:
        (directory / '.observer-installed').unlink()
        real_write = module.write_private
        def fail(path, data):
            if path.name == '.observer-installed':
                raise OSError(errno.ENOSPC, 'transient marker')
            return real_write(path, data)
        monkeypatch.setattr(module, 'write_private', fail)
    with pytest.raises(OSError):
        observer._load(1001)
    assert observer.path.read_bytes() == original
    assert not list(directory.glob('observer-state.corrupt-*'))


def test_failed_copy_cleans_own_partial_attempt(tmp_path, monkeypatch):
    from heterodyne.wsd import backup_cycles as module
    directory = tmp_path / 'backups'
    def fail(source, target, cancel):
        target.write_bytes(b'partial')
        target.chmod(0o600)
        raise OSError('copy floor')
    monkeypatch.setattr(module, 'snapshot', fail)
    for cycle in range(1, 5):
        with pytest.raises(OSError, match='copy floor'):
            copy_bundle(directory, (tmp_path / 'a', tmp_path / 'b'), cycle, 'unknown',
                        threading.Event(), Identity('install', 'generation', 'a' * 64), new_attempt())
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize('native', ['unknown', 'failed'])
def test_verified_local_history_independent_of_native(tmp_path, native):
    from test_plan8_backup_recovery import sources

    from heterodyne.wsd.backup_copy import seal_manifest
    identity = Identity('install', 'generation', 'a' * 64)
    directory = tmp_path / 'backups'
    inputs = sources(tmp_path)
    now = time.time()
    attempts = []
    for cycle in range(3):
        attempt = new_attempt()
        copy_bundle(directory, inputs, cycle + 1, native, threading.Event(), identity, attempt)
        path = directory / attempt / 'manifest.json'
        record = json.loads(path.read_text())
        record['completed_utc'] = now - cycle * 86400
        seal_manifest(path, json.dumps(record))
        attempts.append(attempt)
    retain(directory, identity, now, None)
    assert all((directory / attempt / 'manifest.json').exists() for attempt in attempts)
    assert all(json.loads((directory / attempt / 'manifest.json').read_text())['status'] == 'partial'
               for attempt in attempts)


def test_prune_failure_preserves_sealed_result(tmp_path, monkeypatch):
    from test_plan8_backup_recovery import sources

    from heterodyne.wsd import effect_owner as module
    path = tmp_path / 'store.db'
    EffectStore.initialize(path, 'install', 'generation')
    monkeypatch.setattr(module, 'retain', lambda *_: (_ for _ in ()).throw(PermissionError('prune')))
    owner = module.EffectOwner(path, 'install', 'generation', 'a' * 64)
    try:
        owner.opened.result(3)
        result = owner.backup(1, 'succeeded', tmp_path / 'paired-backups', sources(tmp_path))
        assert result.result(3) == 'queued'
        owner.copy.result(3)
        deadline = time.monotonic() + 3
        while owner.call(lambda s: s.pending_backup()).result(3) is not None:
            assert time.monotonic() < deadline
            time.sleep(.01)
        row = owner.call(lambda s: s.db.execute(
            'SELECT state,attempt,manifest_hash FROM backup_cycle').fetchone()).result(3)
        assert row[0] == 'succeeded' and len(row[1]) == 32 and len(row[2]) == 64
    finally:
        owner.close()


@pytest.mark.parametrize('status', ['failed', 'partial'])
def test_repeat_returns_actual_local_result(tmp_path, status):
    path = tmp_path / 'store.db'
    EffectStore.initialize(path, 'install', 'generation')
    store = EffectStore(path, 'install', 'generation')
    try:
        assert store.begin_backup(7, 'unknown') == 'queued'
        store.finish_backup(7, status)
        assert store.begin_backup(7, 'succeeded') == status
        assert store.begin_backup(6, 'succeeded') == 'result_unavailable'
        assert store.begin_backup(8, 'succeeded') == 'queued'
        assert store.pending_backup() == (8, 'succeeded', '')
    finally:
        store.close()


def test_old_boot_is_unknown_not_unavailable(tmp_path):
    from test_plan8_health import snapshot

    from heterodyne.wsd.health import publish
    from heterodyne.wsd.observer import Observer
    directory = tmp_path / 'state'
    directory.mkdir()
    publish(directory / 'health.json', snapshot(boot_id='old-boot'))
    values = Observer(directory, tmp_path / 'alerts', 'install', 'generation').inspect(1000, 100, 'new-boot')
    assert values['unavailable'] == 'unknown' and values['unknown'] == 'true'


def test_fresh_effect_error_reports_hold():
    from test_plan8_health import snapshot

    from heterodyne.wsd.health import predicates
    assert predicates(snapshot(effect_state='error', effect_observed_at=1000), utc=1000,
                      monotonic=100)['effect_hold'] == 'true'
    assert predicates(snapshot(effect_state='error', effect_observed_at=800), utc=1000,
                      monotonic=100)['effect_hold'] == 'unknown'


def test_native_exception_is_not_suppressed(tmp_path):
    from heterodyne.wsd.backup_pairing import Binding, initialize, paired_attempt
    binding = Binding('install', 'generation', 'a' * 64)
    initialize(tmp_path, binding)
    seen = []
    failure = OSError('native failure')
    def native():
        raise failure
    def companion(cycle, status):
        seen.append((cycle, status))
        raise RuntimeError('companion failed too')
    with pytest.raises(OSError) as caught:
        paired_attempt(tmp_path, binding, native, companion)
    assert caught.value is failure and seen == [(1, 'failed')]


def test_startup_failure_has_final_failed_heartbeat(tmp_path, monkeypatch):
    from test_plan8_owner_integration import until
    from test_schedule_admission import installed

    from heterodyne.wsd.health import read
    async def exercise():
        daemon, manifest = installed(tmp_path)
        path = daemon.s.state_dir / 'schedule-manifest.json'
        path.write_bytes(msgspec.json.encode(manifest))
        path.chmod(0o600)
        async def startup():
            await until(lambda: (daemon.s.state_dir / 'health.json').exists())
            raise RuntimeError('recovery failed')
        monkeypatch.setattr(daemon, '_startup', startup)
        with pytest.raises(RuntimeError, match='recovery failed'):
            await daemon.serve(asyncio.Event())
        result = read(daemon.s.state_dir / 'health.json', manifest.installation_id, manifest.generation)
        assert result.recovery == 'failed'
    asyncio.run(exercise())


def test_valid_observer_does_not_rewrite_install_marker(tmp_path, monkeypatch):
    from heterodyne.wsd import observer as module
    directory = tmp_path / 'state'
    directory.mkdir()
    observer = module.Observer(directory, tmp_path / 'alerts', 'install', 'generation')
    original = observer._load(1000)
    def fail(*_):
        raise AssertionError('valid load must not rewrite marker or state')
    monkeypatch.setattr(module, 'write_private', fail)
    assert observer._load(1001) == original


def test_restart_cleans_only_exact_unsealed_private_scratch(tmp_path):
    from heterodyne.wsd.backup_cycles import discard_incomplete
    from heterodyne.wsd.effect_owner import EffectOwner
    path = tmp_path / 'store.db'
    EffectStore.initialize(path, 'install', 'generation')
    store = EffectStore(path, 'install', 'generation')
    attempt = new_attempt()
    assert store.begin_backup(1, 'unknown') == 'queued'
    store.backup_attempt(1, attempt)
    store.close()
    directory = tmp_path / 'paired-backups'
    partial = directory / attempt
    partial.mkdir(parents=True, mode=0o700)
    data = partial / 'local-0.db'
    data.write_bytes(b'partial')
    data.chmod(0o600)
    owner = EffectOwner(path, 'install', 'generation', 'a' * 64)
    try:
        owner.opened.result(3)
        # Serialize after completion, without treating absence as native success.
        deadline = time.monotonic() + 3
        while owner.call(lambda s: s.pending_backup()).result(3) is not None:
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert not partial.exists()
        assert owner.call(lambda s: s.begin_backup(1, 'unknown')).result(3) == 'failed'
    finally:
        owner.close()
    foreign = directory / new_attempt()
    foreign.mkdir(mode=0o700)
    (foreign / 'manifest.json').write_text('incomplete seal: retain for investigation')
    discard_incomplete(directory, foreign.name)
    assert foreign.exists()


def test_unknown_inventory_refuses_new_copy_without_growth(tmp_path):
    directory = tmp_path / 'backups'
    directory.mkdir()
    for i in range(256):
        (directory / str(i)).write_text('unowned')
    with pytest.raises(ValueError, match='inventory held'):
        copy_bundle(directory, (tmp_path / 'a', tmp_path / 'b'), 1, 'unknown', threading.Event(),
                    Identity('install', 'generation', 'a' * 64), new_attempt())
    assert len(list(directory.iterdir())) == 256


@pytest.mark.parametrize('result,reason', [('failed', 'local_failed'), ('partial', 'local_partial'),
                                          ('result_unavailable', 'result_unavailable')])
def test_repeated_bad_result_survives_real_control_wire(tmp_path, result, reason):
    from concurrent.futures import Future
    from uuid import uuid4

    from test_schedule_admission import installed

    from heterodyne.wsd.ctl import CtlServer
    from heterodyne.wsd.effect_owner import EffectOwner
    from heterodyne.wsd.maintenance_protocol import PairedBackup, request
    async def exercise():
        daemon, manifest = installed(tmp_path)
        daemon.manifest = msgspec.structs.replace(manifest, jobs=('backup_bundle', 'pickup', 'reconcile'),
                                                 backup_owner_hash='a' * 64)
        path = tmp_path / 'store.db'
        EffectStore.initialize(path, manifest.installation_id, manifest.generation)
        daemon.effects = EffectOwner(path, manifest.installation_id, manifest.generation, 'a' * 64)
        daemon.effects.opened.result(3)
        # Typed owner result: no native or SQLite effect is synthesized by the daemon.
        def backup(*_):
            future = Future()
            future.set_result(result)
            return future
        daemon.effects.backup = backup
        server = CtlServer(daemon.s.socket, daemon.handle)
        server.maintenance_handler = daemon.maintenance_request
        await server.start()
        try:
            receipt = await request(daemon.s.socket, PairedBackup('paired-backup', 1,
                manifest.installation_id, manifest.generation, str(uuid4()), 'a' * 64, 7, 'unknown'))
            assert receipt.status == 'refused' and receipt.reason == reason
        finally:
            await server.close()
            await asyncio.to_thread(daemon.effects.close)
            daemon.drain_now(3)
    asyncio.run(exercise())


def test_missing_manifest_is_restartable_io_failure(tmp_path):
    from test_schedule_admission import installed

    from heterodyne.wsd.schedule_manifest import load
    daemon, _ = installed(tmp_path)
    try:
        with pytest.raises(FileNotFoundError):
            load(daemon.s)
    finally:
        daemon.drain_now(3)


def test_actual_missing_store_error_has_fresh_timestamp(tmp_path):
    from test_plan8_health import snapshot

    from heterodyne.wsd.health import predicates
    store = EffectStore(tmp_path / 'absent.db', 'install', 'generation')
    assert store.cache.state == 'error'
    now = time.time()
    assert 0 <= now - store.cache.observed_at < 5
    assert predicates(snapshot(utc=now, effect_state=store.cache.state,
                      effect_observed_at=store.cache.observed_at), utc=now,
                      monotonic=100)['effect_hold'] == 'true'


def test_final_failed_snapshot_fences_inflight_periodic_writer(tmp_path, monkeypatch):
    from test_plan8_owner_integration import until
    from test_schedule_admission import installed

    from heterodyne.wsd import daemon as module
    from heterodyne.wsd.health import read
    async def exercise():
        daemon, manifest = installed(tmp_path)
        path = daemon.s.state_dir / 'schedule-manifest.json'
        path.write_bytes(msgspec.json.encode(manifest))
        path.chmod(0o600)
        entered = threading.Event()
        release = threading.Event()
        actual_publish = module.publish_health
        def blocked(path, snapshot):
            if snapshot.recovery == 'in_progress':
                entered.set()
                assert release.wait(3)
            actual_publish(path, snapshot)
        monkeypatch.setattr(module, 'publish_health', blocked)
        async def startup():
            await until(entered.is_set)
            asyncio.get_running_loop().call_later(.05, release.set)
            raise RuntimeError('recovery failed')
        monkeypatch.setattr(daemon, '_startup', startup)
        try:
            with pytest.raises(RuntimeError, match='recovery failed'):
                await daemon.serve(asyncio.Event())
            assert read(daemon.s.state_dir / 'health.json', manifest.installation_id,
                        manifest.generation).recovery == 'failed'
        finally:
            release.set()
    asyncio.run(exercise())


@pytest.mark.parametrize('reason', ['local_failed', 'local_partial', 'result_unavailable'])
def test_cli_does_not_report_repeated_bad_result_as_success(tmp_path, monkeypatch, capsys, reason):
    from test_schedule_admission import installed

    from heterodyne.wsd import cli
    from heterodyne.wsd.maintenance_protocol import Receipt
    daemon, manifest = installed(tmp_path)
    monkeypatch.setattr(cli, '_settings', lambda: daemon.s)
    monkeypatch.setattr(cli, 'load_manifest', lambda _: msgspec.structs.replace(
        manifest, backup_owner_hash='a' * 64))
    async def refused(path, value):
        return Receipt(value.request_id, 'refused', reason)
    monkeypatch.setattr(cli, 'maintenance_request', refused)
    try:
        assert cli.wsd_main(['paired-backup', '7', 'unknown']) == 1
        assert capsys.readouterr().out == f'refused:{reason}\n'
    finally:
        daemon.drain_now(3)
