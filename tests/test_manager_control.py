"""Real socket/flock/durable-file tests; no real sandbox or operator authority claim."""
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from test_session_server import ask, req

from heterodyne.session import control, shim
from heterodyne.session.control import ControlCapacity, ControlJournal, ControlOwner
from heterodyne.session.server import SessionServer


@pytest.fixture
def owner(tmp_path):
    owner = ControlOwner(tmp_path / 'owner')
    yield owner
    owner.close()


@pytest.fixture
def journal(tmp_path, owner):
    return ControlJournal(tmp_path / 'journal', owner)


def request(operation='Status', arguments=None, identity=None):
    return json.dumps({'schema': shim.CONTROL_SCHEMA, 'request_id': identity or str(uuid.uuid4()),
                       'operation': operation, 'arguments': arguments or {}}).encode()


def event():
    return dict(id='a'*64, pubkey='b'*64, sig='c'*128, created_at=1, kind=30078, tags=[], content='external')


@pytest.mark.parametrize('operation,arguments', [
    ('Status', {}), ('Boundary', {}), ('ListManagerRequests', {'cursor': None}),
    ('Evidence', {'issue_id': 'btq-x'}),
    ('Ask', {'key': 'question', 'summary': 'Question', 'text': 'Need help'}),
    ('Escalate', {'issue_id': 'btq-x', 'reason': 'Requires operator'}),
    ('ResolveSigned', {'issue_id': 'btq-x', 'event': event()}),
    ('ReadOutcome', {'origin_generation': 1, 'request_id': str(uuid.uuid4())})])
def test_all_eight_closed_operations(operation, arguments):
    assert shim.parse_control(request(operation, arguments))['operation'] == operation


@pytest.mark.parametrize('raw', [b'\xff', b'{}', b'[]', b'{"x":1,"x":2}',
    request('Execute', {'argv': ['anything']}), request('Status', {'ready': True}),
    request('ReadOutcome', {'origin_generation': True, 'request_id': str(uuid.uuid4())}),
    request('Ask', {'key': 'x', 'summary': 'x', 'text': 'x'*4097}),
    request('Ask', {'key': 'x', 'summary': '\ud800', 'text': 'x'}),
    request().replace(b'{}', b'{"unexpected": NaN}'),
    request('Status', {'x': [[[[[[[[[[[[[[[[{}]]]]]]]]]]]]]]]]}),
    request().replace(b'"arguments": {}', b'"arguments": {}, "operation": "Status"')])
def test_codec_refuses_bad_or_open_input(raw):
    with pytest.raises((ValueError, UnicodeError)):
        shim.parse_control(raw)


def test_intake_exact_bytes_conflict_and_terminal_replay(journal):
    raw = request()
    first = journal.intake(raw)
    assert first['state'] == 'accepted'
    assert first['request_sha256'] == hashlib.sha256(raw).hexdigest()
    assert journal.intake(raw) == first
    with pytest.raises(ValueError, match='different bytes'):
        journal.intake(raw.replace(b': ', b':'))
    identity = json.loads(raw)['request_id']
    outcome = journal.reply(identity, 'completed', {'held': True})
    assert journal.intake(raw) == outcome
    assert journal.pending() == []
    with pytest.raises(ValueError, match='immutable'):
        journal.reply(identity, 'completed', {'held': False})


def test_100_unanswered_and_unlimited_terminal_history(journal):
    raws = [request() for _ in range(100)]
    for raw in raws:
        journal.intake(raw)
    with pytest.raises(ControlCapacity):
        journal.intake(request())
    for raw in raws:
        journal.reply(json.loads(raw)['request_id'], 'completed', {})
    for _ in range(105):
        raw = request()
        journal.intake(raw)
        journal.reply(json.loads(raw)['request_id'], 'refused', {})
    assert len(list((journal.root/'requests').iterdir())) == 205
    assert journal.pending() == []
    assert journal.intake(request())['state'] == 'accepted'


def test_request_fsync_before_marker_and_crash_replay(journal, owner):
    raw = request()
    original = control._write
    order = []

    def crash_at_marker(path, data):
        order.append(path.parent.name)
        if path.parent.name == 'pending':
            raise OSError('simulated crash before admission')
        return original(path, data)

    with patch.object(control, '_write', side_effect=crash_at_marker), pytest.raises(OSError):
        journal.intake(raw)
    assert order == ['requests', 'pending']
    restarted = ControlJournal(journal.root, owner)
    assert restarted.pending() == []
    assert restarted.intake(raw)['state'] == 'accepted'
    assert len(restarted.pending()) == 1


def test_crash_after_reply_durability_never_requeues(journal):
    raw = request()
    identity = json.loads(raw)['request_id']
    journal.intake(raw)
    original = Path.unlink

    def crash_at_remove(path, *args, **kwargs):
        if path.parent.name == 'pending':
            raise OSError('simulated crash after outcome fsync')
        return original(path, *args, **kwargs)

    with patch.object(Path, 'unlink', new=crash_at_remove), pytest.raises(OSError):
        journal.reply(identity, 'completed', {'actual': 'retained'})
    assert journal.intake(raw)['data'] == {'actual': 'retained'}
    assert journal.pending() == []
    journal.intake(request())  # owner reaps the stale marker, preserving terminal history
    assert len(journal.pending()) == 1


def test_readonly_journal_never_admits_answers_or_changes_files(journal):
    raw = request()
    journal.intake(raw)
    before = {p: p.stat().st_mtime_ns for p in journal.root.rglob('*')}
    reader = ControlJournal(journal.root)
    assert len(reader.pending()) == 1
    assert reader.outcome(json.loads(raw)['request_id']) is None
    for action in (lambda: reader.intake(request()),
                   lambda: reader.reply(json.loads(raw)['request_id'], 'completed', {})):
        with pytest.raises(RuntimeError, match='read-only'):
            action()
    assert {p: p.stat().st_mtime_ns for p in journal.root.rglob('*')} == before


def test_actual_cross_process_owner_lock_and_release(tmp_path, owner):
    script = '''from pathlib import Path
from heterodyne.session.control import ControlOwner
import sys
try:
 o = ControlOwner(Path(sys.argv[1]))
except BlockingIOError:
 print('held')
else:
 print('acquired'); o.close()
'''
    args = [sys.executable, '-c', script, str(tmp_path/'owner')]
    assert subprocess.check_output(args, text=True).strip() == 'held'
    owner.close()
    assert subprocess.check_output(args, text=True).strip() == 'acquired'
    with pytest.raises(RuntimeError):
        owner.check()


def test_real_socket_structured_standalone_client_and_worker_refusal(tmp_path, journal):
    sock = tmp_path/'s.sock'
    token = 'test-only-generation-token'  # noqa: S105 - disposable local fixture
    (tmp_path/'token').write_text(token)
    (tmp_path/'shim.json').write_text('{"wait_seconds": 1}')
    server = SessionServer(sock, token, tmp_path/'events', control=journal)
    server.start()
    try:
        raw = request('Ask', {'key': 'q', 'summary': 'Question', 'text': 'Need operator'})
        result = subprocess.run([sys.executable, '-I', str(Path(shim.__file__)), 'control'],
            input=raw, capture_output=True, env=dict(os.environ, HZ_SESSION_SOCKET=str(sock)), timeout=5)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['state'] == 'accepted'
        retained = journal.pending()
        assert len(retained) == 1
        assert retained[0]['request'] == json.loads(raw)
        assert not (tmp_path/'events').exists()  # control bypasses lossy event spool
    finally:
        server.close()
    worker = SessionServer(sock, token, tmp_path/'worker-events')
    worker.start()
    try:
        assert json.loads(ask(sock, req(token, 'ws_request', json.loads(request()))))['error'] == 'forbidden'
    finally:
        worker.close()


def test_cross_process_observer_does_not_rebind_or_unlink_owner_socket(tmp_path, owner):
    from heterodyne.sandbox.runtime import Phase, SessionRecord, write_record
    from heterodyne.sandbox.spec import SessionLayout
    sessions = tmp_path/'sessions'
    layout = SessionLayout.at(sessions, 'manager-key')
    layout.run(1).mkdir(parents=True)
    record = SessionRecord(key='manager-key', ws='build', bead='btq-manager', role='manager',
        profile='codex', adapter='codex', generation=1, phase=Phase.RUNNING,
        sandbox='fixture-sandbox', tmux_session='fixture-tmux')
    write_record(layout, record)
    journal = ControlJournal(layout.control(1), owner)
    server = SessionServer(layout.socket(1), 'token', layout.events(1), control=journal)
    server.start()
    before = {p: p.stat().st_mtime_ns for p in layout.root.rglob('*')}
    script = '''from pathlib import Path
from types import SimpleNamespace
from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.session.server import SessionServer
import sys
r=SandboxRuntime.__new__(SandboxRuntime)
r.c=SimpleNamespace(sessions=Path(sys.argv[1]),
 tmux=SimpleNamespace(has_session=lambda _:True,pane_dead=lambda _:False))
r.backend=SimpleNamespace(names=lambda:{'fixture-sandbox'})
def forbidden(*a,**kw): raise AssertionError('observer attempted ownership/mutation')
r._rebind=r._end=r._restore_creates=forbidden
SessionServer.start=forbidden
assert r.observe_session('manager-key').key=='manager-key'
'''
    try:
        subprocess.run([sys.executable, '-c', script, str(sessions)], check=True, timeout=5)
        assert {p: p.stat().st_mtime_ns for p in layout.root.rglob('*')} == before
        result = json.loads(ask(layout.socket(1), req('token', 'ws_request', json.loads(request()))))
        assert result['state'] == 'accepted'
    finally:
        server.close()


@pytest.mark.parametrize('role,enabled', [('worker', False), ('secondary', False), ('manager', True)])
def test_actual_runtime_server_only_enables_manager_journal(tmp_path, owner, role, enabled):
    from types import SimpleNamespace

    from heterodyne.sandbox.runtime import Phase, SandboxRuntime, SessionRecord
    runtime = SandboxRuntime.__new__(SandboxRuntime)
    runtime.c = SimpleNamespace(sessions=tmp_path/'sessions')
    runtime.manager_owner = owner
    rec = SessionRecord(key=role, ws='build', bead='btq-'+role, role=role, profile='codex',
        adapter='codex', generation=1, phase=Phase.RUNNING, sandbox='fixture', tmux_session='fixture')
    layout = runtime.layout(rec.key)
    layout.root.mkdir(parents=True)
    server = runtime._server(rec, SimpleNamespace(turn_end_events=('Stop',)), 'token', None)
    assert (server.control is not None) == enabled
    for bound in (layout.home, layout.bridge, layout.git, layout.run(1)):
        assert not layout.control(1).is_relative_to(bound)


def test_codec_exact_byte_depth_and_complete_outer_wire_bounds(tmp_path):
    raw = request()
    assert shim.parse_control(raw + b' '*(shim.CONTROL_BYTES-len(raw)))
    with pytest.raises(ValueError, match='ordinary bound'):
        shim.parse_control(raw + b' '*(shim.CONTROL_BYTES-len(raw)+1))
    deep = b'['*17 + b'0' + b']'*17
    with patch.object(shim, 'strict_json', side_effect=AssertionError('must bound before parsing')):
        with pytest.raises(ValueError, match='bounds'):
            shim.parse_control(deep)
    assert shim.json_depth(b'['*16+b'0'+b']'*16) == 16
    sock = tmp_path/'socket'
    (tmp_path/'token').write_text('token')
    (tmp_path/'shim.json').write_text('{}')
    env = {'HZ_SESSION_SOCKET': str(sock)}
    value = json.loads(request('ResolveSigned', {'issue_id': 'btq-x', 'event': event()}))
    outer = {'token': 'token', 'type': 'ws_request', 'payload': value}
    room = shim.MAX_LINE - len(shim._encode(outer)) - 1
    value['arguments']['event']['content'] += 'x'*room
    with patch.object(shim, '_ask', return_value={'ok': True}) as exchange:
        assert shim.run_control(json.dumps(value).encode(), env)[0] == 0
        exchange.assert_called_once()
    value['arguments']['event']['content'] += 'x'
    with patch.object(shim, '_ask') as exchange:
        assert 'operator-route-required' in shim.run_control(json.dumps(value).encode(), env)[1]
        exchange.assert_not_called()


def test_client_refuses_oversized_reply_and_endpoint_arguments(tmp_path):
    (tmp_path/'token').write_text('token')
    (tmp_path/'shim.json').write_text('{}')
    with patch.object(shim, '_ask', return_value={'ok': True, 'data': 'x'*shim.CONTROL_REPLY_BYTES}):
        result = shim.run_control(request(), {'HZ_SESSION_SOCKET': str(tmp_path/'socket')})
        assert 'operator-route-required' in result[1]
    with patch.object(shim, 'control_main') as client:
        assert shim.main(['control', '--socket', '/anything']) == 2
        client.assert_not_called()


def test_journal_rejects_nonregular_and_corrupt_pending_or_outcome(journal):
    raw = request()
    identity = json.loads(raw)['request_id']
    journal.intake(raw)
    marker = journal._path('pending', identity)
    marker.write_text('[]')
    with pytest.raises(ValueError, match='pending marker'):
        journal.pending()
    marker.unlink()
    os.mkfifo(marker)
    with pytest.raises(ValueError, match='regular file'):
        journal.pending()
    marker.unlink()
    marker.write_text('{}')
    journal.reply(identity, 'completed', {})
    outcome = journal._path('outcomes', identity)
    value = json.loads(outcome.read_bytes())
    value['request_sha256'] = '0'*64
    outcome.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='outcome'):
        journal.intake(raw)


def test_runtime_owner_release_waits_for_all_handlers(tmp_path):
    from types import SimpleNamespace

    from heterodyne.sandbox.runtime import SandboxRuntime
    runtime = SandboxRuntime.__new__(SandboxRuntime)
    runtime.c = SimpleNamespace(sessions=tmp_path/'sessions')
    runtime.manager_owner = None
    runtime.servers = {}
    runtime.own_manager_control()
    retained = runtime.manager_owner
    from unittest.mock import Mock
    busy = Mock(active=1)
    runtime.servers = {'manager': busy}
    with pytest.raises(Exception, match='handlers'):
        runtime.close_manager_control()
    assert runtime.manager_owner is retained
    retained.check()
    busy.active = 0
    runtime.close_manager_control()
    assert runtime.manager_owner is None
    with pytest.raises(RuntimeError):
        retained.check()
