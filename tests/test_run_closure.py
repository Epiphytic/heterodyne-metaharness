"""Closure authority and lifecycle regressions; no live queue or transport."""
import json
from unittest.mock import Mock

import pytest

from harness.cli import dispatch, parser
from harness.store import Store
from harness.supervisor import Supervisor
from harness import operator_asks
from tests.test_supervisor import FakeTmux, FakeTransport


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path)
    supervisor = Supervisor(store, FakeTmux(), FakeTransport(), boot='fixture')
    run = supervisor.start('fixture', None, 'codex')
    yield store, supervisor, run
    store.db.close()


def consent(run, action='close', **extra):
    return dict(run_id=run['id'], action=action, approved_by='operator',
                response='Please close this workstream.', evidence_ref='retained/operator-message.json',
                force=False, open_beads=[], **extra)


def queue(supervisor, run, tasks):
    run['beads'] = {'workstream': 'fixture'}
    native = Mock(ws='fixture')
    native.bd.return_value = tasks
    native.show.side_effect = lambda key: next(i for i in tasks if i['id'] == key)
    supervisor.beads._queue = Mock(return_value=native)
    return native


def task(identity='a', status='open', metadata=None):
    return dict(id=identity, status=status, metadata=metadata or {})


def test_persistent_default_and_explicit_opt_out(context):
    store, supervisor, run = context
    assert run['persistent'] is True
    args = parser().parse_args(['start', 'other', '-', 'codex', '--no-persistent'])
    assert dispatch(args, store, supervisor, {})['persistent'] is False


@pytest.mark.parametrize('persistent', [False, None, True])
def test_completion_event_never_terminalizes(context, persistent):
    store, supervisor, run = context
    if persistent is not None:
        run['persistent'] = persistent
    else:
        run.pop('persistent')
    supervisor.persist(run)
    args = parser().parse_args(['event', run['id'], '--state', 'completed', '--text', 'deployed'])
    dispatch(args, store, supervisor, {})
    saved = store.get(run['id'])
    assert saved['state'] == 'idle'
    assert saved['task_state'] == 'completed'


def test_status_cannot_reopen_closed_run(context):
    store, supervisor, run = context
    supervisor.close(run, consent(run))
    dispatch(parser().parse_args(['event', run['id'], '--state', 'working', '--text', 'late']),
             store, supervisor, {})
    assert store.get(run['id'])['state'] == 'completed'


def test_successful_command_idle_and_reported_once(context):
    store, supervisor, _ = context
    run = supervisor.start('command', None, 'command', agent_config={'argv': ['/bin/true']})
    supervisor.tmux.panes[run['id']].update(alive=False, dead=True, exit_code=0)
    supervisor.observe(run)
    supervisor.observe(run)
    assert run['state'] == 'idle'
    assert run['task_state'] == 'completed'
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='completed'").fetchone()[0] == 1


@pytest.mark.parametrize('action', ['close', 'stop'])
def test_no_consent_has_no_terminal_effect(context, action):
    _, supervisor, run = context
    with pytest.raises(ValueError, match='consent'):
        getattr(supervisor, action)(run)
    assert run['state'] == 'active'
    assert not supervisor.tmux.stops


def test_open_beads_force_requires_exact_consent(context):
    _, supervisor, run = context
    native = queue(supervisor, run, [task(), task('done', 'closed')])
    evidence = consent(run)
    with pytest.raises(ValueError, match='Nonclosed'):
        supervisor.close(run, evidence)
    evidence['force'] = True
    with pytest.raises(ValueError, match='open_beads'):
        supervisor.close(run, evidence, force=True)
    evidence['open_beads'] = ['a']
    supervisor.close(run, evidence, force=True)
    assert run['state'] == 'completed'
    assert '--all' in native.bd.call_args.args
    assert 'agent:codex' not in native.bd.call_args.args


def test_unavailable_queue_fails_closed(context):
    _, supervisor, run = context
    native = queue(supervisor, run, [])
    native.bd.side_effect = RuntimeError('offline')
    with pytest.raises(RuntimeError, match='offline'):
        supervisor.close(run, consent(run))
    assert run['state'] == 'active'
    assert not supervisor.tmux.stops


def test_unresolved_ask_blocks_even_force(context):
    store, supervisor, run = context
    operator_asks.enqueue(store, run, 'Decision?', 'key', 'Decision?')
    evidence = consent(run); evidence['force'] = True
    with pytest.raises(ValueError, match='Unresolved'):
        supervisor.close(run, evidence, force=True)
    assert run['state'] == 'active'


@pytest.mark.parametrize('history', [[{'stage': 'merged'}], json.dumps([{'stage': 'final-tested'}])])
def test_pending_deploy_blocks_even_closed_bead_and_force(context, history):
    _, supervisor, run = context
    queue(supervisor, run, [task(status='closed', metadata={'harness_lifecycle': history})])
    evidence = consent(run); evidence['force'] = True
    with pytest.raises(ValueError, match='Pending deployments'):
        supervisor.close(run, evidence, force=True)


def test_consumed_consent_retries_but_cannot_close_resumed_run(context):
    store, supervisor, run = context
    evidence = consent(run)
    supervisor.close(run, evidence)
    saved = store.get(run['id'])
    assert saved['closure']['consent'] == evidence
    supervisor.close(saved, evidence)  # safe retry after uncertain process stop
    saved['state'] = 'active'
    with pytest.raises(ValueError, match='consumed'):
        supervisor.close(saved, evidence)


def test_cli_close_requires_file_and_retains_attestation(context, tmp_path):
    store, supervisor, run = context
    path = tmp_path / 'consent.json'; path.write_text(json.dumps(consent(run)))
    args = parser().parse_args(['close', run['id'], '--consent-file', str(path)])
    dispatch(args, store, supervisor, {})
    assert store.get(run['id'])['closure']['consent']['evidence_ref'] == 'retained/operator-message.json'
    assert supervisor.tmux.stops == [run['id'], run['manager']['id']]


@pytest.mark.parametrize('field,value', [('run_id', 'foreign'), ('action', 'stop'),
    ('force', True), ('approved_by', ''), ('response', ''), ('evidence_ref', '')])
def test_mismatched_or_incomplete_consent(context, field, value):
    _, supervisor, run = context
    evidence = consent(run); evidence[field] = value
    with pytest.raises(ValueError):
        supervisor.close(run, evidence)
    assert run['state'] == 'active'
    assert not supervisor.tmux.stops


def test_linked_deploy_on_other_workstream_is_a_hard_block(context):
    _, supervisor, run = context
    members = {'parent': 'p', 'implementation': 'i', 'review': 'r', 'deployment': 'd'}
    tasks = [task(key, 'closed' if role != 'deployment' else 'open',
                  {'harness_delivery': {'version': 1, 'role': role, 'members': members}})
             for role, key in members.items()]
    native = queue(supervisor, run, tasks)
    native.bd.return_value = [tasks[0]]  # Only parent routed here; others followed live.
    evidence = consent(run); evidence.update(force=True, open_beads=['d'])
    with pytest.raises(ValueError, match='Pending deployments'):
        supervisor.close(run, evidence, force=True)
    assert native.show.call_count == 4


def test_queue_bound_and_malformed_metadata_fail_closed(context):
    _, supervisor, run = context
    native = queue(supervisor, run, [task(str(i)) for i in range(201)])
    with pytest.raises(RuntimeError, match='bounded'):
        supervisor.close(run, consent(run))
    native.bd.return_value = [task()]
    native.show.side_effect = lambda _: task(metadata={'harness_delivery': 'invalid-json'})
    with pytest.raises(ValueError):
        supervisor.close(run, consent(run))
    assert not supervisor.tmux.stops
