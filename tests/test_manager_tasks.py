import copy
import json
from unittest.mock import Mock

import pytest
try:
    from coincurve import PrivateKey
except ImportError:
    PrivateKey = None

from harness import manager_tasks as tasks, manager_resolution as resolution, blocker_receipts as receipts
from harness.babysitter_resolution import observe
from harness.beads import BeadsError
from harness.store import Store
from harness.supervisor import Supervisor
from harness.permission_relay import observe as relay_observe
from tests.test_supervisor import FakeTmux, FakeTransport


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    supervisor = Supervisor(store, FakeTmux(), FakeTransport(), boot='fixture')
    run = supervisor.start('manager-fixture', None, 'codex')
    run['manager']['observed_state'] = 'idle'
    run['beads'] = {'workstream': 'fixture', 'issue_id': 'worker-owned', 'pickup_enabled': True}
    supervisor.persist(run)
    database, comments = {}, {}
    queue = Mock(agent='bel', worker='bel:fixture:' + run['id'])
    queue.matches.return_value = True
    queue.show.side_effect = lambda identity: copy.deepcopy(database[identity])
    def bd(*args):
        if args[0] == 'update':
            database[args[1]].update(status='in_progress', assignee=queue.worker)
        elif args[0] == 'comments':
            if args[1] == 'add':
                comments.setdefault(args[2], []).append({'text': args[3]})
            else:
                return comments.get(args[1], [])
        elif args[0] == 'close':
            database[args[1]]['status'] = 'closed'
        else:
            raise AssertionError(args)
    queue.bd.side_effect = bd
    beads = Mock(enabled=True)
    beads._queue.return_value = queue
    beads._allowed.return_value = True
    def create(manager, title, description, **kw):
        assert manager['agent'] == 'bel' and manager['id'] == run['id']
        assert 'issue_id' not in manager['beads'] and kw['at_top']
        identity = 'btq-' + receipts.digest(kw['key'])[:24]
        database.setdefault(identity, dict(id=identity, status='open', assignee='', title=title,
                                           description=description, metadata=kw['metadata']))
        return copy.deepcopy(database[identity])
    beads.create.side_effect = create
    beads.claim.side_effect = lambda _, identity: bd('update', identity, '--claim')
    beads.ready.side_effect = lambda _: [i for i in database.values() if i['status'] == 'open']
    supervisor.beads = beads
    yield store, supervisor, run, database, queue
    store.db.close()


def report(setup, healthy=False, now=100):
    store, _, run, _, _ = setup
    with store.db:
        observe(store, run, 'blocked-handoff-commit', 'worker',
                'new healthy native turn' if healthy else 'fatal EROFS index.lock', healthy=healthy, now=now)


def first(setup):
    store = setup[0]
    return store.db.execute('SELECT * FROM manager_tasks ORDER BY rowid').fetchone()


def signed(setup, **overrides):
    if PrivateKey is None:
        pytest.skip('install requirements-blockers.txt for signature fixtures')
    store, supervisor, run, _, _ = setup
    expected = resolution.evidence(store, supervisor.beads, run, first(setup)['issue_id'])
    body = {k: v for k, v in expected.items() if k != 'verification'}
    body.update(resolution='Repaired permitted configuration, resumed, observed a new healthy turn.', action_completed_at_ms=101000)
    body.update(overrides)
    key = PrivateKey.from_int(7)
    event = dict(pubkey=key.public_key_xonly.format().hex(), created_at=104, kind=1, tags=[], content=receipts.canonical(body))
    event['id'] = receipts.event_id(event)
    event['sig'] = key.sign_schnorr(bytes.fromhex(event['id'])).hex()
    return event, {'manager_tasks': {'signers': [event['pubkey']], 'event_kind': 1}}


def test_episode_survives_restart_one_claim_one_dispatch_and_signed_close(setup):
    store, supervisor, run, database, queue = setup
    report(setup)
    assert len(tasks.pickup(store, supervisor, now=100)['submitted']) == 1
    issue_id = first(setup)['issue_id']
    report(setup, now=101)
    other = Store(store.root.parent)
    tasks.pickup(other, supervisor, now=101)
    other.db.close()
    assert len(database) == len(supervisor.tmux.sent) == 1
    assert store.get(run['id'])['beads']['issue_id'] == 'worker-owned'
    with pytest.raises(BeadsError, match='verification'):
        resolution.evidence(store, supervisor.beads, run, issue_id)
    report(setup, healthy=True, now=103)
    event, config = signed(setup)
    result = resolution.resolve(store, supervisor.beads, run, issue_id, event, config, now=104)
    assert result['status'] == 'closed'
    resolution.resolve(store, supervisor.beads, run, issue_id, event, config, now=105)
    assert len(queue.bd('comments', issue_id)) == 1
    report(setup, now=106)
    tasks.pickup(store, supervisor, now=107)
    assert len(database) == 2


@pytest.mark.parametrize('state', ['awaiting_approval', 'awaiting_question', 'working'])
def test_manager_native_wait_never_claims_or_types(setup, state):
    store, supervisor, run, database, queue = setup
    run['manager']['observed_state'] = state
    supervisor.persist(run)
    report(setup)
    tasks.pickup(store, supervisor, now=100)
    assert len(database) == 1 and not supervisor.tmux.sent
    queue.bd.assert_not_called()


def test_uncertain_delivery_never_replayed(setup):
    store, supervisor, _, _, _ = setup
    report(setup)
    supervisor.tmux.send = Mock(side_effect=RuntimeError('uncertain'))
    assert tasks.pickup(store, supervisor, now=100)['errors']
    tasks.pickup(store, supervisor, now=160)
    assert supervisor.tmux.send.call_count == 1


@pytest.mark.parametrize('mutation', ['signature', 'scope', 'signer', 'stale', 'action'])
def test_resolution_rejects_forged_or_wrong_evidence(setup, mutation):
    store, supervisor, run, database, _ = setup
    report(setup)
    tasks.pickup(store, supervisor, now=100)
    report(setup, healthy=True, now=103)
    kwargs = {'issue_id': 'another'} if mutation == 'scope' else {}
    if mutation == 'action':
        kwargs['action_completed_at_ms'] = 103000
    event, config = signed(setup, **kwargs)
    if mutation == 'signature':
        event['sig'] = '00' * 64
    if mutation == 'signer':
        config['manager_tasks']['signers'] = []
    if mutation == 'stale':
        report(setup, now=104)
    with pytest.raises(BeadsError):
        resolution.resolve(store, supervisor.beads, run, first(setup)['issue_id'], event, config, now=104)
    assert all(i['status'] != 'closed' for i in database.values())


def test_shared_pickup_approval_waits_for_gate_resolution(setup):
    store, supervisor, run, database, _ = setup
    database['gate'] = dict(id='gate', status='open', metadata={'harness_gate': {
        'revision': 'v1', 'reason': 'Scoped operator decision', 'blocker': {'run_id': run['id']}}})
    tasks.approval(store, supervisor.beads, run, 'gate')
    tasks.pickup(store, supervisor, now=100)
    issue_id = first(setup)['issue_id']
    assert len(supervisor.tmux.sent) == 1
    with pytest.raises(BeadsError, match='signed gate policy'):
        resolution.evidence(store, supervisor.beads, run, issue_id)
    database['gate'].update(status='closed')
    database['gate']['metadata']['harness_gate_resolution'] = {'receipt': 'existing signed gate result'}
    with pytest.raises(BeadsError, match='without verified evidence'):
        resolution.evidence(store, supervisor.beads, run, issue_id)


def native_prompt(setup, subject='worker'):
    store, supervisor, run, _, _ = setup
    target = run['manager'] if subject == 'manager' else run
    target['native_session_id'] = subject + '-native'
    region = ('Dangerous Command\necho fixture\nAllow once\nDeny\n'
              '↑/↓ to select, Enter to confirm')
    supervisor.tmux.panes[target['id']]['text'] = region
    relay_observe(store, dict(run, native_session_id=target['native_session_id'],
                             pane_id=target['pane_id']), supervisor.tmux.inspect(target),
                  beads=supervisor.beads)
    return region


def test_native_prompt_claims_manager_bead_without_operator_ping(setup):
    store, supervisor, run, database, _ = setup
    before = store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
    region = native_prompt(setup)
    assert len(database) == 1  # Detection creates the Bead before the cron tick.
    assert tasks.pickup(store, supervisor, now=100)['submitted']
    row = first(setup)
    issue = database[row['issue_id']]
    assert issue['status'] == 'in_progress' and issue['assignee'].startswith('bel:')
    assert region in issue['description'] and run['pane_id'] in issue['description']
    assert store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == before
    assert len(supervisor.tmux.sent) == 1
    assert tasks.pickup(store, supervisor, now=101)['submitted'] == []


def test_native_resolution_requires_full_text_and_appends_audit(setup):
    store, supervisor, run, database, _ = setup
    region = native_prompt(setup)
    tasks.pickup(store, supervisor, now=100)
    run['observed_state'] = 'idle'
    run['observation'] = {'state': 'idle', 'at': 103, 'pane_alive': True}
    event, config = signed(setup, resolution='Approved once after live inspection.\n' + region)
    result = resolution.resolve(store, supervisor.beads, run, first(setup)['issue_id'], event, config, now=104)
    assert result['status'] == 'closed'
    audit = store.root / 'runs' / run['id'] / 'approval-log.jsonl'
    assert json.loads(audit.read_text().splitlines()[0])['captured_approval'] == region
    resolution.resolve(store, supervisor.beads, run, first(setup)['issue_id'], event, config, now=105)
    assert len(audit.read_text().splitlines()) == 1


def test_native_resolution_rejects_stale_observation_and_incomplete_text(setup):
    store, supervisor, run, database, _ = setup
    native_prompt(setup)
    tasks.pickup(store, supervisor, now=100)
    issue_id = first(setup)['issue_id']
    with pytest.raises(BeadsError, match='Fresh post-action'):
        resolution.evidence(store, supervisor.beads, run, issue_id)
    run['observed_state'] = 'idle'
    run['observation'] = {'state': 'idle', 'at': 103, 'pane_alive': True}
    event, config = signed(setup, resolution='Approved once')
    with pytest.raises(BeadsError, match='full captured approval text'):
        resolution.resolve(store, supervisor.beads, run, issue_id, event, config, now=104)
    assert database[issue_id]['status'] == 'in_progress'
    assert not (store.root / 'runs' / run['id'] / 'approval-log.jsonl').exists()


def test_native_resolution_rejects_unrecognized_capture(setup):
    store, supervisor, run, database, _ = setup
    target = run
    target['native_session_id'] = 'worker-native'
    region = 'Partial command text without a complete native modal'
    relay_observe(store, run, dict(alive=True,pane_id=run['pane_id'],text=region),
                  detected=True, beads=supervisor.beads)
    tasks.pickup(store, supervisor, now=100)
    run['observed_state'] = 'idle'
    run['observation'] = {'state': 'idle', 'at': 103, 'pane_alive': True}
    event, config = signed(setup, resolution='Approved after inspection.\n' + region)
    with pytest.raises(BeadsError, match='Captured approval is incomplete'):
        resolution.resolve(store, supervisor.beads, run, first(setup)['issue_id'], event, config, now=104)
    assert database[first(setup)['issue_id']]['status'] == 'in_progress'


def test_native_bead_creation_failure_retries_at_pickup(setup):
    store, supervisor, run, database, _ = setup
    create = supervisor.beads.create.side_effect
    attempts = 0
    def retryable_create(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BeadsError('temporary Beads failure')
        return create(*args, **kwargs)
    supervisor.beads.create.side_effect = retryable_create
    native_prompt(setup)
    assert first(setup)['issue_id'] is None
    assert not database
    assert tasks.pickup(store, supervisor, now=100)['submitted']
    assert attempts == 2 and first(setup)['issue_id'] in database


def test_native_escalation_is_first_and_only_operator_ask(setup):
    store, supervisor, run, _, _ = setup
    before = store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
    region = native_prompt(setup)
    tasks.pickup(store, supervisor, now=100)
    assert store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] == before
    issue_id = first(setup)['issue_id']
    resolution.escalate(store, run, issue_id, 'Needs credential access')
    resolution.escalate(store, run, issue_id, 'Needs credential access')
    asks = store.db.execute('SELECT text FROM outbox WHERE id LIKE "operator-ask:%"').fetchall()
    assert len(asks) == 1 and region in asks[0][0]


def test_manager_own_modal_claims_then_escalates_without_self_steer(setup):
    store, supervisor, run, database, _ = setup
    native_prompt(setup, 'manager')
    result = tasks.pickup(store, supervisor, now=100)
    assert len(result['submitted']) == 1
    row = first(setup)
    assert row['operator_hold'] == 1 and database[row['issue_id']]['status'] == 'in_progress'
    assert not supervisor.tmux.sent
    assert store.db.execute('SELECT COUNT(*) FROM outbox WHERE id LIKE "operator-ask:%"').fetchone()[0] == 1


def test_disruptive_boundary_requires_completed_matching_turn():
    run = dict(id='run', native_turn_state='idle', native_turn_key=['native', 'turn'], state='active',
               beads={'pickup_enabled': True}, observation={'pane_alive': True})
    assert not tasks.boundary(run)['ready']
    run['native_completion'] = dict(native='native', turn='turn', applied=True)
    assert tasks.boundary(run)['ready']
    run['continuation'] = {'question': True}
    assert not tasks.boundary(run)['ready']


def test_dangerous_remediation_escalates_once_without_closing(setup):
    store, supervisor, run, database, _ = setup
    report(setup)
    tasks.pickup(store, supervisor, now=100)
    issue_id = first(setup)['issue_id']
    resolution.escalate(store, run, issue_id, 'Credential replacement requires Liam')
    resolution.escalate(store, run, issue_id, 'Credential replacement requires Liam')
    assert first(setup)['operator_hold'] == 1
    assert database[issue_id]['status'] == 'in_progress'
    assert store.db.execute('SELECT count(*) FROM operator_asks').fetchone()[0] == 1


def test_notice_classes_dedup_and_low_severity_audit(setup):
    from harness.manager_notices import record
    store, _, run, _, _ = setup
    for text in ('EROFS index.lock strike 81', 'EROFS index.lock strike 82'):
        assert record(store, run, text, operator_ask=False)['target'] == 'manager-bead'
    assert store.db.execute('SELECT count(*) FROM manager_tasks').fetchone()[0] == 1
    for text in ('supervisor dead', 'outbox head-of-line blocked', 'invalid pause', 'worker stalled'):
        record(store, run, text, operator_ask=True)
    assert store.db.execute('SELECT count(*) FROM manager_tasks').fetchone()[0] == 5
    assert record(store, run, 'normal progress', operator_ask=False) is None
    record(store, run, 'verified repair', operator_ask=False, category='blocked-handoff-commit', healthy=True)
    assert store.db.execute("SELECT phase FROM babysitter_findings WHERE category='blocked-handoff-commit'").fetchone()[0] == 'resolved'


def test_timer_is_shared_and_independent_of_supervisor(tmp_path):
    from install_manager_pickup import units
    rendered = units(tmp_path, tmp_path, '/usr/bin/python3')
    assert len(rendered) == 2
    timer = rendered['hermes-manager-pickup.timer']
    service = rendered['hermes-manager-pickup.service']
    assert 'OnUnitActiveSec=60s' in timer and 'AccuracySec=1s' in timer
    assert '"manager-task" "pickup"' in service
    assert 'Requires=hermes-workstreams' not in service


def test_changed_owner_and_dependencies_never_claim(setup):
    store, supervisor, _, database, queue = setup
    report(setup)
    original = supervisor.beads.ready.side_effect
    supervisor.beads.ready.side_effect = lambda _: []
    tasks.pickup(store, supervisor, now=100)
    queue.bd.assert_not_called()
    supervisor.beads.ready.side_effect = original
    database[first(setup)['issue_id']]['assignee'] = 'someone-else'
    assert tasks.pickup(store, supervisor, now=160)['errors']
    queue.bd.assert_not_called()


def test_native_idle_with_unknown_screen_can_receive_but_modal_blocks(setup):
    store, supervisor, run, _, _ = setup
    run['manager'].update(observed_state='unknown', native_turn_state='idle')
    supervisor.persist(run)
    report(setup)
    assert tasks.pickup(store, supervisor, now=100)['submitted']


def test_changed_screen_approval_blocks_stale_idle(setup):
    store, supervisor, run, _, queue = setup
    supervisor.tmux.panes[run['manager']['id']]['text'] = 'Dangerous Command\nrm example\nAllow once\nDeny\n↑/↓ to select, Enter to confirm'
    report(setup)
    # Exact modal recognition is tested by the existing permission relay suite.
    from harness.agents import Adapter
    observed = Adapter('hermes').observe(run['manager'], supervisor.tmux.panes[run['manager']['id']]['text'])
    assert observed['state'] == 'awaiting_approval'
    tasks.pickup(store, supervisor, now=100)
    queue.bd.assert_not_called()


def test_lost_close_ack_reconciles_exact_comment_after_fresh_health(setup):
    store, supervisor, run, database, queue = setup
    report(setup)
    tasks.pickup(store, supervisor, now=100)
    report(setup, healthy=True, now=103)
    event, config = signed(setup)
    original = queue.bd.side_effect
    def uncertain(*args):
        result = original(*args)
        if args[0] == 'close':
            raise BeadsError('lost close acknowledgement')
        return result
    queue.bd.side_effect = uncertain
    issue_id = first(setup)['issue_id']
    with pytest.raises(BeadsError, match='lost close'):
        resolution.resolve(store, supervisor.beads, run, issue_id, event, config, now=104)
    report(setup, healthy=True, now=105)
    queue.bd.side_effect = original
    resolution.resolve(store, supervisor.beads, run, issue_id, event, config, now=106)
    assert database[issue_id]['status'] == 'closed'
    assert len(queue.bd('comments', issue_id)) == 1


def test_blocked_first_request_does_not_starve_second_and_query_once(setup):
    store, supervisor, run, database, _ = setup
    report(setup)
    with store.db:
        observe(store, run, 'outbox-head-of-line', 'second', 'delivery blocked', healthy=False, now=100)
    supervisor.beads.ready.side_effect = lambda _: [i for i in database.values() if i['title'] == 'Manager: outbox-head-of-line']
    result = tasks.pickup(store, supervisor, now=100)
    assert len(result['submitted']) == 1
    assert database[result['submitted'][0]]['title'] == 'Manager: outbox-head-of-line'
    supervisor.beads.ready.assert_called_once()


def test_notice_and_worker_probe_share_erofs_identity(setup):
    from harness.manager_notices import record
    store, _, run, _, _ = setup
    with store.db:
        observe(store, run, 'blocked-handoff-commit', 'worker-read-only-error', 'fatal EROFS', healthy=False, now=100)
    record(store, run, 'EROFS strike 81', operator_ask=True)
    assert store.db.execute('SELECT count(*) FROM babysitter_findings').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM manager_tasks').fetchone()[0] == 1


def test_pending_dispatch_rechecks_remote_owner(setup):
    store, supervisor, run, database, _ = setup
    report(setup)
    drain = supervisor.drain_inbox
    supervisor.drain_inbox = Mock()
    tasks.pickup(store, supervisor, now=100)
    database[first(setup)['issue_id']]['assignee'] = 'changed-owner'
    supervisor.drain_inbox = drain
    drain(run)
    assert not supervisor.tmux.sent
