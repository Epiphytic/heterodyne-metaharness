"""Disposable offline regressions for actual third review; no installed services."""
import asyncio
import errno
import hashlib
import threading
import time
from dataclasses import replace

import msgspec
import pytest

from heterodyne.wsd.backup_cycles import NativeStatus


def test_partial_does_not_displace_last_native_success(tmp_path):
    from test_plan8_backup_recovery import sources

    from heterodyne.wsd.backup_cycles import Identity, copy_bundle, new_attempt, retain
    identity = Identity('install', 'generation', 'a' * 64)
    directory = tmp_path / 'bundles'
    inputs = sources(tmp_path)
    success, partial = new_attempt(), new_attempt()
    copy_bundle(directory, inputs, 1, 'succeeded', threading.Event(), identity, success)
    copy_bundle(directory, inputs, 2, 'unknown', threading.Event(), identity, partial)
    retain(directory, identity, time.time(), None)
    assert (directory / success / 'manifest.json').exists()
    assert (directory / partial / 'manifest.json').exists()


def test_settled_eviction_uses_payload_age_not_hash_order(tmp_path):
    from test_local_alerts import intent

    from heterodyne.local_alerts import publish, settle
    items = sorted((intent(str(i), 'recovery') for i in range(2)), key=lambda x: x.filename())
    newer = replace(items[0], created_at='2026-10-18T01:00:00Z')
    older = replace(items[1], created_at='2026-10-18T00:00:00Z')
    for item in (older, newer):
        assert publish(tmp_path, item) == 'written'
        assert settle(tmp_path, item, hashlib.sha256(item.payload()).hexdigest(), 'published')
    for i in range(253):
        (tmp_path / f'foreign{i}.json').write_text('{}')
    assert publish(tmp_path, intent('next')) == 'written'
    assert (tmp_path / newer.filename()).exists()
    assert not (tmp_path / older.filename()).exists()


def test_foreign_saturation_does_not_destroy_settled_notice(tmp_path):
    from test_local_alerts import intent

    from heterodyne.local_alerts import publish, settle
    old = intent('old', 'recovery')
    assert publish(tmp_path, old) == 'written'
    assert settle(tmp_path, old, hashlib.sha256(old.payload()).hexdigest(), 'published')
    for i in range(255):
        (tmp_path / f'foreign{i}.json').write_text('{}')
    assert publish(tmp_path, intent('next')) in ('held', 'coalesced')
    assert (tmp_path / old.filename()).read_bytes() == old.payload()


def test_observer_quarantines_symlink_without_touching_target(tmp_path):
    from heterodyne.wsd.observer import Observer
    directory = tmp_path / 'state'
    directory.mkdir()
    target = tmp_path / 'target'
    target.write_text('untouched')
    observer = Observer(directory, tmp_path / 'alerts', 'install', 'generation')
    observer.path.symlink_to(target)
    state = observer._load(1000)
    assert state.reset is not None and not observer.path.is_symlink()
    assert target.read_text() == 'untouched'
    assert next(directory.glob('observer-state.corrupt-*')).is_symlink()


@pytest.mark.parametrize('code', [errno.EMFILE, errno.EIO, errno.ENOENT, errno.EACCES])
def test_manifest_io_remains_restartable(tmp_path, monkeypatch, code):
    from test_schedule_admission import installed

    from heterodyne.wsd import schedule_manifest as module
    daemon, _ = installed(tmp_path)
    monkeypatch.setattr(module.os, 'open', lambda *_: (_ for _ in ()).throw(OSError(code, 'temporary')))
    with pytest.raises(OSError) as failure:
        module.load(daemon.s)
    assert failure.value.errno == code


def test_native_exception_is_failed_but_shutdown_does_not_call_companion(tmp_path):
    from heterodyne.wsd.backup_pairing import Binding, initialize, paired_attempt
    binding = Binding('install', 'generation', 'a' * 64)
    initialize(tmp_path, binding)
    observed = []
    def failing():
        raise RuntimeError('native failed')
    with pytest.raises(RuntimeError, match='native failed'):
        paired_attempt(tmp_path, binding, failing, lambda *args: observed.append(args))
    assert observed == [(1, 'failed')]
    def shutdown():
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        paired_attempt(tmp_path, binding, shutdown, lambda *args: observed.append(args))
    assert observed == [(1, 'failed')]


def test_cancelled_startup_never_claims_recovery_complete(tmp_path, monkeypatch):
    from test_plan8_owner_integration import until
    from test_schedule_admission import installed
    async def exercise():
        daemon, manifest = installed(tmp_path)
        path = daemon.s.state_dir / 'schedule-manifest.json'
        path.write_bytes(msgspec.json.encode(manifest))
        path.chmod(0o600)
        started = asyncio.Event()
        async def startup():
            started.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(daemon, '_startup', startup)
        stop = asyncio.Event()
        serving = asyncio.create_task(daemon.serve(stop))
        await until(started.is_set)
        stop.set()
        await serving
        assert daemon.recovery_status == 'in_progress'
    asyncio.run(exercise())


def test_verified_partial_repeat_is_completed_local_result(tmp_path):
    from test_plan8_backup_recovery import sources

    from heterodyne.wsd.backup_cycles import Identity, copy_bundle, new_attempt
    from heterodyne.wsd.effects import EffectStore
    path = tmp_path / 'store.db'
    EffectStore.initialize(path, 'install', 'generation')
    store = EffectStore(path, 'install', 'generation')
    try:
        assert store.begin_backup(7, 'unknown') == 'queued'
        attempt = new_attempt()
        store.backup_attempt(7, attempt)
        result = copy_bundle(tmp_path / 'bundles', sources(tmp_path), 7, 'unknown',
                             threading.Event(), Identity('install', 'generation', 'a' * 64), attempt)
        store.finish_backup(7, result['status'], attempt, result['manifest_sha256'])
        assert store.begin_backup(7, 'unknown') == 'completed'
        assert store.db is not None
        assert store.db.execute('SELECT native,state FROM backup_cycle').fetchone() == ('unknown', 'partial')
    finally:
        store.close()


def test_manifest_read_io_is_not_configuration_error(tmp_path, monkeypatch):
    from test_schedule_admission import installed

    from heterodyne.wsd import schedule_manifest as module
    daemon, manifest = installed(tmp_path)
    path = daemon.s.state_dir / module.MANIFEST_NAME
    path.write_bytes(msgspec.json.encode(manifest))
    path.chmod(0o600)
    real = module.os.fstat
    def fail(fd):
        raise OSError(errno.EIO, 'temporary stat')
    monkeypatch.setattr(module.os, 'fstat', fail)
    with pytest.raises(OSError, match='temporary stat'):
        module.load(daemon.s)
    monkeypatch.setattr(module.os, 'fstat', real)
    assert module.load(daemon.s) == manifest
    path.write_text('{}')
    with pytest.raises(module.ManifestInvalid):
        module.load(daemon.s)


@pytest.mark.parametrize('code', [errno.EMFILE, errno.EIO, errno.EACCES, errno.ENOENT])
def test_observer_io_on_valid_entry_preserves_epoch(tmp_path, monkeypatch, code):
    from heterodyne.wsd import observer as module
    directory = tmp_path / 'state'
    directory.mkdir()
    observer = module.Observer(directory, tmp_path / 'alerts', 'install', 'generation')
    observer._load(1000)
    original = observer.path.read_bytes()
    real = module.os.open
    def fail(path, *args, **kwargs):
        if path == observer.path:
            raise OSError(code, 'transient')
        return real(path, *args, **kwargs)
    monkeypatch.setattr(module.os, 'open', fail)
    with pytest.raises(OSError):
        observer._load(1001)
    assert observer.path.read_bytes() == original
    assert not list(directory.glob('observer-state.corrupt-*'))


def test_settled_corruption_does_not_block_new_notice_with_free_capacity(tmp_path):
    from test_local_alerts import intent

    from heterodyne.local_alerts import publish, settle
    old = intent('old', 'recovery')
    assert publish(tmp_path, old) == 'written'
    assert settle(tmp_path, old, hashlib.sha256(old.payload()).hexdigest(), 'published')
    (tmp_path / old.filename()).write_text('tampered')
    assert publish(tmp_path, intent('new')) == 'written'
    assert (tmp_path / old.filename()).read_text() == 'tampered'


def test_native_success_calendar_slots_survive_newer_local_partials(tmp_path):
    import json

    from test_plan8_backup_recovery import sources

    from heterodyne.wsd.backup_copy import seal_manifest
    from heterodyne.wsd.backup_cycles import Identity, copy_bundle, new_attempt, retain
    identity = Identity('install', 'generation', 'a' * 64)
    directory = tmp_path / 'bundles'
    inputs = sources(tmp_path)
    now = time.time()
    attempts = []
    samples: tuple[tuple[int, NativeStatus], ...] = (
        (90000, 'succeeded'), (89999, 'unknown'), (100, 'succeeded'), (99, 'unknown'))
    for cycle, (age, native) in enumerate(samples, start=1):
        attempt = new_attempt()
        copy_bundle(directory, inputs, cycle, native, threading.Event(), identity, attempt)
        path = directory / attempt / 'manifest.json'
        record = json.loads(path.read_text())
        record['completed_utc'] = now - age
        seal_manifest(path, json.dumps(record))
        attempts.append(attempt)
    retain(directory, identity, now, None)
    assert all((directory / attempt / 'manifest.json').exists() for attempt in attempts)
