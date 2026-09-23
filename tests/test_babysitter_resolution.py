import tempfile
from pathlib import Path

import pytest

from harness.babysitter_resolution import ACTIONS, observe, poll
from harness.babysitter_observers import worker_errors
from harness.store import Store
from harness.supervisor import Supervisor
from tests.test_supervisor import FakeTmux, FakeTransport
from install_babysitter_resolution import plan


@pytest.fixture
def setup():
    with tempfile.TemporaryDirectory() as directory:
        store = Store(directory)
        supervisor = Supervisor(store, FakeTmux(), FakeTransport(), boot='fixture')
        run = supervisor.start('repair-fixture', None, 'codex')
        run['manager']['observed_state'] = 'idle'
        run.update(native_turn_state='idle', native_turn_key=['native', 'one'])
        supervisor.persist(run)
        yield store, supervisor, run
        store.db.close()


def finding(store):
    return dict(store.db.execute('SELECT * FROM babysitter_findings').fetchone())


def report(setup, *, healthy=False, now=1000, category='invalid-pause'):
    store, _, run = setup
    with store.lock(), store.db:
        return observe(store, run, category, 'test-condition',
                       'Fixture condition. Verify the repaired condition on a subsequent poll.',
                       healthy=healthy, now=now)


@pytest.mark.parametrize('category', ACTIONS)
def test_dispatched_agent_repairs_then_later_probe_verifies(setup, category):
    store, supervisor, run = setup
    report(setup, category=category)
    poll(store, now=1000)
    assert finding(store)['phase'] == 'open'
    repaired = Path(store.root) / 'repair-result'
    original_send = supervisor.tmux.send
    def fixture_agent(destination, text, submit=False):
        original_send(destination, text, submit)
        if text.startswith('Babysitter repair task:'):
            repaired.write_text('healthy')
    supervisor.tmux.send = fixture_agent
    supervisor.drain_inbox(run)
    assert len(supervisor.tmux.sent) == 1
    assert supervisor.tmux.sent[0][0].endswith('-manager')
    assert ACTIONS[category] in supervisor.tmux.sent[0][1]
    # The fixture agent performs the requested repair. Detector independently
    # observes that change on its next poll; dispatch alone never clears it.
    poll(store, now=1001)
    assert finding(store)['phase'] == 'open'
    report(setup, category=category, healthy=repaired.read_text() == 'healthy', now=1301)
    poll(store, now=1301)
    assert finding(store)['phase'] == 'resolved'
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='babysitter_resolution_verified'").fetchone()[0] == 1


def test_three_failures_escalate_once_with_restart_dedup(setup):
    store, supervisor, run = setup
    report(setup)
    for attempt, at in enumerate((1000, 1400, 1800), 1):
        poll(store, now=at)
        supervisor.drain_inbox(run)
        poll(store, now=at + 1)
        report(setup, now=at + 302)
        poll(store, now=at + 302)
        assert finding(store)['failures'] == attempt
    for at in (2200, 2600):
        other = Store(store.root.parent)
        try:
            poll(other, now=at)
        finally:
            other.db.close()
    assert finding(store)['phase'] == 'escalated'
    assert finding(store)['attempts'] == 3
    assert store.db.execute('SELECT count(*) FROM inbox').fetchone()[0] == 3
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='babysitter_repair_escalation'").fetchone()[0] == 1


@pytest.mark.parametrize('state', ['pending', 'uncertain', 'sending', 'held'])
def test_undelivered_attempt_never_replayed(setup, state):
    store, _, _ = setup
    report(setup)
    poll(store, now=1000)
    with store.db:
        store.db.execute('UPDATE inbox SET state=?', (state,))
    report(setup, now=1301)
    poll(store, now=1301)
    assert finding(store)['failures'] == 0
    poll(store, now=1901)
    assert finding(store)['phase'] == 'escalated'
    assert finding(store)['attempts'] == 1


def test_stale_verification_does_not_count_failure(setup):
    store, supervisor, run = setup
    report(setup)
    poll(store, now=1000)
    supervisor.drain_inbox(run)
    poll(store, now=1001)
    poll(store, now=1302)
    assert finding(store)['failures'] == 0
    poll(store, now=1902)
    assert finding(store)['phase'] == 'escalated'


def test_clear_cancels_pending_and_recurrence_has_new_identity(setup):
    store, _, _ = setup
    report(setup)
    poll(store, now=1000)
    first = finding(store)['inbox_id']
    report(setup, healthy=True, now=1010)
    assert store.db.execute('SELECT state FROM inbox WHERE id=?', (first,)).fetchone()[0] == 'superseded'
    report(setup, now=1020)
    poll(store, now=1020)
    assert finding(store)['episode'] == 2
    assert finding(store)['inbox_id'] != first


@pytest.mark.parametrize('guard', ['approval', 'recovery', 'stopped', 'missing'])
def test_delivery_rechecks_guards(setup, guard):
    store, supervisor, run = setup
    report(setup)
    poll(store, now=1000)
    if guard == 'approval':
        run['manager']['observed_state'] = 'awaiting_approval'
    elif guard == 'recovery':
        run.setdefault('beads', {})['recovery_required'] = True
    elif guard == 'stopped':
        run['state'] = 'stopped'
    else:
        run['manager_missing'] = True
    supervisor.drain_inbox(run)
    assert supervisor.tmux.sent == []


def test_erofs_observer_ignores_manager_prose_and_unknown_capture(setup):
    store, _, run = setup
    info = {'run_id': run['id'], 'pane_id': run['pane_id']}
    error = "fatal: Unable to create '/repo/.git/index.lock': Read-only file system"
    with store.db:
        worker_errors(store, [dict(info, is_manager=True)], lambda _: error, now=1000)
        worker_errors(store, [info], lambda _: 'Discussing EROFS index.lock read-only repairs', now=1000)
        worker_errors(store, [info], lambda _: error, now=1000)
        worker_errors(store, [info], lambda _: error, now=1301)
        worker_errors(store, [info], lambda _: '', now=1302)
        worker_errors(store, [], lambda _: 'idle', now=1303)
    assert finding(store)['sequence'] == 1
    assert finding(store)['phase'] == 'open'
    with store.db:
        worker_errors(store, [info], lambda _: 'git operation succeeded\nidle', now=1400)
    assert finding(store)['phase'] == 'open'
    run['native_turn_key'] = ['native', 'two']
    with store.db:
        store.save(run)
        worker_errors(store, [info], lambda _: 'git operation succeeded\nidle', now=1401)
    assert finding(store)['phase'] == 'resolved'


def test_installer_is_bounded_and_idempotent(tmp_path):
    path = tmp_path / 'babysitter.py'
    path.write_text('def handle_erofs_stall(state):\n    pass\n\ndef poll_once(state):\n    handle_erofs_stall(state)\n\ndef unrelated():\n    return 42\n')
    result = plan(path)
    path.write_text(result['files'][0]['replacement'])
    assert 'return 42' in path.read_text()
    assert plan(path)['files'] == []
    path.write_text('def handle_erofs_stall(state, surprise):\n    pass\n')
    with pytest.raises(ValueError):
        plan(path)


def test_direct_submit_also_preserves_approval(setup):
    store, supervisor, run = setup
    report(setup)
    poll(store, now=1000)
    row = store.db.execute('SELECT * FROM inbox').fetchone()
    run['manager']['observed_state'] = 'awaiting_approval'
    supervisor.submit(run, row['text'], target='manager', message_id=row['id'])
    assert supervisor.tmux.sent == []


def test_no_manager_escalates_even_with_fresh_observations(setup):
    store, supervisor, run = setup
    run['manager_missing'] = True
    supervisor.persist(run)
    report(setup)
    poll(store, now=1000)
    report(setup, now=1800)
    poll(store, now=1901)
    assert finding(store)['attempts'] == 0
    assert finding(store)['phase'] == 'escalated'


def test_existing_direct_steering_precedes_repair(setup):
    store, supervisor, run = setup
    report(setup)
    poll(store, now=1000)
    with store.db:
        store.db.execute('INSERT INTO inbox VALUES (?,?,?,?,?,?)',
                         ('direct', run['id'], 'Operator steering', 1100, 'pending', 'manager'))
    supervisor.drain_inbox(run)
    assert supervisor.tmux.sent[0][1] == 'Operator steering'


def test_installed_adapter_observes_and_dispatches(setup, tmp_path, monkeypatch):
    store, _, run = setup
    import harness.store
    adapter_store = Store(store.root.parent)
    monkeypatch.setattr(harness.store, 'Store', lambda: adapter_store)
    path = tmp_path / 'babysitter.py'
    path.write_text('def handle_erofs_stall(state):\n    pass\n\ndef poll_once(state):\n    handle_erofs_stall(state)\n')
    replacement = plan(path)['files'][0]['replacement']
    namespace = {
        'HARNESS_REPO': str(Path.cwd()),
        'list_worker_panes': lambda: [{'run_id': run['id'], 'pane_id': run['pane_id']}],
        'capture': lambda _: "fatal: Unable to create '/repo/.git/index.lock': Read-only file system",
    }
    exec(replacement, namespace)
    namespace['poll_once']({})
    assert finding(store)['attempts'] == 1
    assert store.db.execute('SELECT target FROM inbox').fetchone()[0] == 'manager'
