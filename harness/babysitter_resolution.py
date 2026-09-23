"""Durable agent repair episodes; contract: spec/babysitter-resolution.md."""
import hashlib
import json
import time

ACTIONS = {
    'supervisor-dead': 'Inspect the supervisor unit and retained failure. Wait for affected native turns to end before an authorized restart; verify a fresh successful tick.',
    'outbox-head-of-line': 'Inspect the oldest undelivered outbox item and delivery failure; repair delivery within standing authority and verify the item delivered. Do not delete retained messages.',
    'worker-stall': 'Inspect the owned worker and native turn. Wait for ended-turn evidence before disruptive repair; preserve native approvals and intentional pauses.',
    'invalid-pause': 'Inspect pause provenance and reconcile the invalid pause through the task facade. Never override an intentional operator pause.',
    'missing-operator-notification': 'Inspect the required notification and durable outbox, repair its delivery path, and verify delivery evidence.',
    'unverified-agent-job': 'Inspect the agent job and its retained result; obtain independent verification or arrange the missing work.',
    'blocked-handoff-commit': 'Inspect the blocked handoff or commit and its exact error. For EROFS index.lock: compare every bead checkout gitdir (git rev-parse --git-dir) with the affected run config.extra_args writable_roots. Under existing operator authority only, wait for a matched ended turn, update the affected runs.data config to include missing gitdirs, restart hermes-workstreams.service, explicitly reconcile/resume affected runs, then steer a retry. Never change your own sandbox permissions from inside a blocked worker. Verify a later changed owned worker capture at a new completed native turn, not merely a successful restart.',
}
INTERVAL = 300
DELIVERY_DEADLINE = 900


def initialize(store):
    store.db.execute('''CREATE TABLE IF NOT EXISTS babysitter_findings (
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL, category TEXT NOT NULL,
      subject TEXT NOT NULL, episode INTEGER NOT NULL, sequence INTEGER NOT NULL,
      evidence TEXT NOT NULL, phase TEXT NOT NULL, attempts INTEGER NOT NULL,
      failures INTEGER NOT NULL, attempted_sequence INTEGER NOT NULL,
      inbox_id TEXT, deadline REAL NOT NULL, submitted_at REAL,
      observed_at REAL NOT NULL)''')


def observe(store, run, category, subject, evidence, *, healthy, now=None):
    """Caller holds Store.lock and transaction. Probe errors must not call healthy."""
    if category not in ACTIONS or not subject or not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('Finding requires a known category, subject and nonempty evidence')
    if type(healthy) is not bool:
        raise ValueError('Health must be an explicit boolean observation')
    initialize(store)
    now = time.time() if now is None else now
    identity = 'babysitter-repair:' + hashlib.sha256(json.dumps(
        [run['id'], category, subject]).encode()).hexdigest()
    previous = store.db.execute('SELECT * FROM babysitter_findings WHERE id=?', (identity,)).fetchone()
    if previous is None:
        if healthy:
            return identity
        store.db.execute('INSERT INTO babysitter_findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identity, run['id'], category, subject, 1, 1, evidence, 'open',
                          0, 0, 0, None, now + DELIVERY_DEADLINE, None, now))
        return identity
    sequence = previous['sequence'] + 1
    store.db.execute('UPDATE babysitter_findings SET sequence=?, evidence=?, observed_at=? WHERE id=?',
                     (sequence, evidence, now, identity))
    if healthy:
        if previous['phase'] != 'resolved':
            store.db.execute("UPDATE babysitter_findings SET phase='resolved' WHERE id=?", (identity,))
            store.db.execute("UPDATE inbox SET state='superseded' WHERE id=? AND state='pending'",
                             (previous['inbox_id'],))
            store.event(run, 'babysitter_resolution_verified', evidence,
                        f"{identity}:{previous['episode']}:verified")
    elif previous['phase'] == 'resolved':
        store.db.execute('''UPDATE babysitter_findings SET phase='open', episode=episode+1,
          attempts=0, failures=0, attempted_sequence=0, inbox_id=NULL, deadline=?,
          submitted_at=NULL WHERE id=?''', (now + DELIVERY_DEADLINE, identity,))
    return identity


def eligible(run):
    from .manager_tasks import available
    return (available(run) and not run.get('resume_required')
            and not run.get('beads', {}).get('recovery_required'))


def ready(store, run, row):
    """Supervisor delivery-time recheck; direct input remains ahead of repairs."""
    initialize(store)
    finding = store.db.execute('SELECT * FROM babysitter_findings WHERE inbox_id=?', (row['id'],)).fetchone()
    if not finding or finding['phase'] != 'open' or finding['run_id'] != run['id']:
        store.db.execute("UPDATE inbox SET state='superseded' WHERE id=? AND state='pending'", (row['id'],))
        return False
    return eligible(run)


def escalate(store, run, finding, reason):
    if not run.get('group_id'):
        raise ValueError('Repair escalation requires the registered operator group')
    text = (f"Babysitter repair needs operator attention: {finding['category']}.\n"
            f"{reason}\n{finding['evidence']}\n"
            'Please inspect the existing agent and retained evidence before authorizing further repair.\n\n'
            f"Reference: {finding['id']} episode {finding['episode']}")
    store.event(run, 'babysitter_repair_escalation', text,
                f"{finding['id']}:{finding['episode']}:escalated", operator_ask=True)
    store.db.execute("UPDATE babysitter_findings SET phase='escalated' WHERE id=?", (finding['id'],))


def dispatch(store, run, finding, now):
    if not eligible(run):
        if now >= finding['deadline']:
            escalate(store, run, finding, 'No safe existing manager destination is available.')
        return
    attempt = finding['attempts'] + 1
    identity = f"{finding['id']}:{finding['episode']}:{attempt}"
    text = (f"Babysitter repair task: {finding['category']}\n{ACTIONS[finding['category']]}\n"
            f"Observed evidence and verification requirement:\n{finding['evidence']}\n"
            'Investigate this focused condition; retain repair and verification evidence. '
            'Preserve task ownership, native approvals, recovery holds and operator deployment gates. '
            'This message grants no additional permissions. A later detector poll must verify resolution.\n\n'
            f"Reference: {identity}")
    store.db.execute('INSERT INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                     (identity, run['id'], text, now, 'pending', 'manager'))
    store.db.execute('''UPDATE babysitter_findings SET attempts=?, attempted_sequence=sequence,
      inbox_id=?, deadline=?, submitted_at=NULL WHERE id=?''',
                     (attempt, identity, now + DELIVERY_DEADLINE, finding['id']))


def advance(store, run, finding, now):
    if not finding['inbox_id']:
        dispatch(store, run, finding, now)
        return
    inbox = store.db.execute('SELECT state FROM inbox WHERE id=?', (finding['inbox_id'],)).fetchone()
    if not inbox or inbox['state'] != 'submitted':
        if now >= finding['deadline']:
            escalate(store, run, finding, 'Repair delivery is missing, delayed or uncertain; inspect before retrying.')
        return
    if finding['submitted_at'] is None:
        store.db.execute('UPDATE babysitter_findings SET submitted_at=?, deadline=? WHERE id=?',
                         (now, now + INTERVAL, finding['id']))
        return
    if now < finding['deadline']:
        return
    if not eligible(run):
        if now >= finding['submitted_at'] + DELIVERY_DEADLINE:
            escalate(store, run, finding, 'Agent has not reached a safe verification boundary.')
        return
    if (finding['sequence'] <= finding['attempted_sequence']
            or finding['observed_at'] <= finding['submitted_at']):
        if now >= finding['submitted_at'] + DELIVERY_DEADLINE:
            escalate(store, run, finding, 'No fresh verification observation after agent dispatch.')
        return
    failures = finding['failures'] + 1
    store.db.execute('UPDATE babysitter_findings SET failures=?, inbox_id=NULL WHERE id=?',
                     (failures, finding['id']))
    if failures >= 3:
        escalate(store, run, finding, 'Three repair attempts failed subsequent verification; circuit breaker open.')
    else:
        dispatch(store, run, finding, now)


def poll(store, *, now=None):
    """Caller holds Store.lock. State, dispatch and escalation are transactional."""
    from .cli import load_config
    from .manager_tasks import collect
    if load_config(store.root.parent).get('beads', {}).get('enabled'):
        with store.db:
            collect(store)
        return  # The independent minute timer owns manager queue pickup.
    now = time.time() if now is None else now
    with store.db:
        initialize(store)
        findings = store.db.execute("SELECT * FROM babysitter_findings WHERE phase='open'").fetchall()
        for finding in findings:
            advance(store, store.get(finding['run_id']), finding, now)
