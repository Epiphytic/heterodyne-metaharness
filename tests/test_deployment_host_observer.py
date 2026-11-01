"""Actual fd/tree/DB reads; manager text tests are not actual systemd qualification."""
# pyright: reportPrivateUsage=false

import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

from heterodyne.actions.codec import InvalidAction
from heterodyne.actions.deployment_host_observer import _endpoint_state, _parse_unit, _tree_digest
from heterodyne.harness.resources import store_core


def _unit(**changes: str) -> bytes:
    fields = {
        'Id': 'heterodyne-admind.service', 'LoadState': 'loaded', 'ActiveState': 'active',
        'SubState': 'running', 'UnitFileState': 'enabled', 'NeedDaemonReload': 'no',
        'Environment': 'HETERODYNE_RELEASE=' + 'a' * 40, 'MainPID': '123', 'InvocationID': 'b' * 32,
    }
    fields.update(changes)
    return ''.join(f'{key}={value}\n' for key, value in fields.items()).encode()


def test_unit_exact_active_and_inactive() -> None:
    assert _parse_unit(_unit())['ActiveState'] == 'active'
    assert _parse_unit(_unit(ActiveState='inactive', SubState='dead', MainPID='0',
                             UnitFileState='disabled'))['ActiveState'] == 'inactive'


@pytest.mark.parametrize('changes', [
    {'Id': 'heterodyne-wsd.service'}, {'LoadState': 'not-found'}, {'ActiveState': 'failed'},
    {'SubState': 'exited'}, {'UnitFileState': 'masked'}, {'NeedDaemonReload': 'yes'},
    {'MainPID': '0'}, {'MainPID': '-2'}, {'MainPID': '0004'},
    {'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '123'},
])
def test_unsettled_unit_is_not_an_observation(changes: dict[str, str]) -> None:
    with pytest.raises(InvalidAction):
        _parse_unit(_unit(**changes))


@pytest.mark.parametrize('raw', [
    _unit() + b'Id=heterodyne-admind.service\n', _unit() + b'Other=not-pinned\n',
    _unit().replace(b'LoadState=loaded\n', b''), _unit() + b'not-a-property\n', b'x' * 16385,
])
def test_unit_strict_shape(raw: bytes) -> None:
    with pytest.raises(InvalidAction):
        _parse_unit(raw)


def _digest(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return _tree_digest(fd)
    finally:
        os.close(fd)


def test_real_tree_hash_is_predictable_not_inode_or_time(tmp_path: Path) -> None:
    a = tmp_path / 'a'
    b = tmp_path / 'b'
    a.mkdir()
    b.mkdir()
    (a / 'file').write_bytes(b'payload')
    (b / 'file').write_bytes(b'payload')
    assert (a / 'file').stat().st_ino != (b / 'file').stat().st_ino
    assert _digest(a) == _digest(b)
    os.utime(b / 'file', (1, 1))
    assert _digest(a) == _digest(b)
    (b / 'file').write_bytes(b'changed')
    assert _digest(a) != _digest(b)


def test_actual_tree_mode_and_internal_link_are_bound(tmp_path: Path) -> None:
    file = tmp_path / 'file'
    file.write_bytes(b'payload')
    original = _digest(tmp_path)
    file.chmod(0o700)
    assert _digest(tmp_path) != original
    changed = _digest(tmp_path)
    (tmp_path / 'link').symlink_to('file')
    assert _digest(tmp_path) != changed
    (tmp_path / 'link').unlink()
    (tmp_path / 'link').symlink_to('/etc/passwd')
    # External link literal is measured, never followed or claimed as closure.
    assert _digest(tmp_path) != changed


@pytest.mark.parametrize('kind', ['hardlink', 'fifo'])
def test_special_or_aliased_tree_refuses(tmp_path: Path, kind: str) -> None:
    (tmp_path / 'file').write_bytes(b'payload')
    if kind == 'hardlink':
        os.link(tmp_path / 'file', tmp_path / 'other')
    else:
        os.mkfifo(tmp_path / 'fifo')
    with pytest.raises(store_core.WalkRefused):
        _digest(tmp_path)


def test_changed_file_during_hash_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / 'file'
    file.write_bytes(b'payload')
    original = os.read
    changed = False

    def mutate(fd: int, size: int) -> bytes:
        nonlocal changed
        raw = original(fd, size)
        if raw and not changed:
            changed = True
            file.write_bytes(b'replaced')
        return raw

    monkeypatch.setattr(os, 'read', mutate)
    with pytest.raises(store_core.WalkRefused):
        _digest(tmp_path)
    assert changed


def test_tree_golden(tmp_path: Path) -> None:
    # Byte contract is independent of machine inode/time; explicit actual modes.
    tmp_path.chmod(0o700)
    file = tmp_path / 'a'
    file.write_bytes(b'abc')
    file.chmod(0o600)
    manifest = {'root_mode': 0o700, 'entries': [{
        'path': 'a', 'kind': 'file', 'mode': 0o600, 'size': 3,
        'sha256': hashlib.sha256(b'abc').hexdigest(), 'target': None,
    }]}
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    assert _digest(tmp_path) == hashlib.sha256(
        b'heterodyne-deployment-installed-tree.v1\x00' + raw,
    ).hexdigest()


def test_mapped_root_retained_profile_and_real_database_tree(tmp_path: Path) -> None:
    import subprocess
    import sys

    # Private user+mount namespace; a fake manager read is explicit. The DB,
    # descriptor ownership, profile mode, content hashing and refusals are real.
    source = Path(__file__).resolve().parents[1] / 'src'
    script = r'''
import contextlib, hashlib, json, os, pwd, sqlite3, tempfile
from pathlib import Path
from heterodyne.actions import deployment_host_observer as owner
from heterodyne.actions.codec import InvalidAction
os.system('mount --make-rprivate /')
import subprocess
subprocess.run(['mount','-t','tmpfs','-o','mode=755','tmpfs','/tmp'],check=True)
with tempfile.TemporaryDirectory(dir='/tmp') as home:
 base=Path(home);base.chmod(0o700)
 uid=pwd.getpwnam('nobody').pw_uid
 release='a'*40
 releases=base/'releases';releases.mkdir()
 tree=releases/release;tree.mkdir();(tree/'file').write_bytes(b'content')
 (tree/'external-interpreter').symlink_to('/usr/bin/python3')
 record=base/'record.json';record.write_text(json.dumps({'version':1,'release':release,'units':{}}))
 os.chown(record,uid,-1);record.chmod(0o600)
 database=base/'admind.db'
 db=sqlite3.connect(database);db.execute('CREATE TABLE outbox(status TEXT)')
 db.executemany('INSERT INTO outbox VALUES (?)',[('quarantined',),('pending',)])
 db.commit();db.close();database.chmod(0o600)
 p={'schema':'heterodyne-deployment-observer-profile.v1','target_id':'test-admind',
 'service_user':'nobody','service_uid':uid,'runuser_sha256':'b'*64,'env_sha256':'c'*64,
 'systemctl_sha256':'d'*64,'releases':str(releases),'install_record':str(record),
 'database':str(database),'scratch':str(base)}
 profile=base/'profile.json';profile.write_text(json.dumps(p));profile.chmod(0o400)
 unit={'Environment':'HETERODYNE_RELEASE='+release,'UnitFileState':'enabled','ActiveState':'active'}
 owner._show=lambda _:dict(unit) # manager trust is qualified separately, not by this mock
 fd=os.open(profile,os.O_RDONLY)
 try:
  before=hashlib.sha256(database.read_bytes()).hexdigest()
  first=owner._observe(fd)
  assert first.observation.guard_verdict=='unchecked' and first.observation.quarantine_count==1
  assert hashlib.sha256(database.read_bytes()).hexdigest()==before
  assert owner._observe(fd)==first
  # Actual DB change affects both count and diagnostic copied-input digest.
  db=sqlite3.connect(database);db.execute("UPDATE outbox SET status='quarantined'")
  db.commit();db.close()
  second=owner._observe(fd)
  assert second.observation.quarantine_count==2
  assert second.database_copy_sha256!=first.database_copy_sha256
  profile.chmod(0o600)
  try:owner._observe(fd)
  except InvalidAction:pass
  else:raise AssertionError('mutable profile accepted')
  profile.chmod(0o400)
  unit['Environment']='HETERODYNE_RELEASE='+'e'*40
  try:owner._observe(fd)
  except InvalidAction as exc:assert str(exc)=='deployment-observer-release-mismatch'
  else:raise AssertionError('record/runtime disagreement accepted')
  print('actual-profile-tree-db-read-and-refusals-pass')
 finally:os.close(fd)
'''
    result = subprocess.run(
        ['unshare', '--user', '--map-auto', '--map-root-user', '--mount', sys.executable, '-c', script],
        capture_output=True, timeout=30, check=False, env={**os.environ, 'PYTHONPATH': str(source)},
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b'actual-profile-tree-db-read-and-refusals-pass\n'


def test_actual_manager_bus_and_stale_private_socket(tmp_path: Path) -> None:
    private = tmp_path / 'private'
    bus = tmp_path / 'bus'
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(private))
    stale.close()
    assert _endpoint_state(private, os.getuid())[3] == 'refused'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as live:
        live.bind(str(bus))
        live.listen(2)
        assert _endpoint_state(bus, os.getuid())[3] == 'connected'
        with pytest.raises(InvalidAction):
            _endpoint_state(bus, os.getuid() + 1)
    with pytest.raises(ConnectionRefusedError):
        _endpoint_state(bus, os.getuid())


def test_private_endpoint_must_be_an_actual_owned_socket(tmp_path: Path) -> None:
    private = tmp_path / 'private'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(private))
    with pytest.raises(InvalidAction):
        _endpoint_state(private, os.getuid() + 1)
    private.unlink()
    private.write_bytes(b'not a socket')
    with pytest.raises((InvalidAction, OSError)):
        _endpoint_state(private, os.getuid())
