"""Durable delivery projection of native facts. Contract: spec/transitions.md."""
import json
import time

from .task_gates import digest

WINDOW = 120
PART_SIZE = 6000


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS transition_heads(
      run_id TEXT, issue_id TEXT, data TEXT NOT NULL, PRIMARY KEY(run_id,issue_id));
    CREATE TABLE IF NOT EXISTS transition_batches(
      id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, group_id TEXT,
      opened_at REAL NOT NULL, due_at REAL NOT NULL, queued_at REAL);
    CREATE TABLE IF NOT EXISTS transitions(
      seq INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, batch_id INTEGER NOT NULL,
      run_id TEXT NOT NULL, issue_id TEXT NOT NULL, kind TEXT NOT NULL,
      title TEXT NOT NULL, status TEXT NOT NULL, payload TEXT, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS transition_signals(
      run_id TEXT,issue_id TEXT,kind TEXT,signature TEXT,sequence INTEGER NOT NULL,
      PRIMARY KEY(run_id,issue_id,kind));
    CREATE TABLE IF NOT EXISTS transition_retirements(
      event_id TEXT PRIMARY KEY, retired_at REAL NOT NULL, reason TEXT NOT NULL);
    ''')


def append(store, run, issue, kind, status, key, payload=None, now=None):
    """Caller holds write transaction. Same identity with altered facts fails."""
    now = time.time() if now is None else now
    identity = digest([run['id'], issue['id'], kind, key])
    encoded = json.dumps(payload, sort_keys=True) if payload is not None else None
    expected = (run['id'], issue['id'], kind, issue['title'], status, encoded)
    prior = store.db.execute('SELECT run_id,issue_id,kind,title,status,payload FROM transitions WHERE identity=?', (identity,)).fetchone()
    if prior:
        if tuple(prior) != expected:
            raise ValueError('Transition identity reused with different facts')
        return identity
    group = run.get('group_id')
    batch = store.db.execute('''SELECT id FROM transition_batches
      WHERE run_id=? AND group_id IS ? AND queued_at IS NULL AND due_at>?
      ORDER BY id DESC LIMIT 1''', (run['id'], group, now)).fetchone()
    batch_id = batch[0] if batch else store.db.execute('''INSERT INTO transition_batches
      (run_id,group_id,opened_at,due_at) VALUES (?,?,?,?)''',
      (run['id'], group, now, now + WINDOW)).lastrowid
    store.db.execute('''INSERT INTO transitions
      (identity,batch_id,run_id,issue_id,kind,title,status,payload,created_at)
      VALUES (?,?,?,?,?,?,?,?,?)''', (identity, batch_id, *expected, now))
    return identity


def facts(issue):
    from .tasks import scope_fingerprint
    history = issue.get('metadata', {}).get('harness_lifecycle', [])
    return {'status': issue.get('status'), 'scope': scope_fingerprint(issue),
            'stages': [event.get('digest') for event in history]}


def observe(store, run, issue, now=None):
    """Observe native revisions, not pane pixels; first read seeds a baseline."""
    current = facts(issue)
    row = store.db.execute('SELECT data FROM transition_heads WHERE run_id=? AND issue_id=?',
                           (run['id'], issue['id'])).fetchone()
    prior = json.loads(row[0]) if row else None
    if prior and prior != current:
        sequence = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM transitions').fetchone()[0] + 1
        if prior['status'] != current['status']:
            append(store, run, issue, 'status', current['status'], sequence, now=now)
        if prior['scope'] != current['scope']:
            append(store, run, issue, 'scope', 'scope-changed', sequence, now=now)
        for event in issue.get('metadata', {}).get('harness_lifecycle', []):
            if event.get('digest') not in prior['stages']:
                stage = event['stage']
                status = {'pr-open': 'review-requested', 'deployed': 'live-verification-passed'}.get(stage, stage)
                append(store, run, issue, 'stage', status, event['digest'], now=now)
    store.db.execute('INSERT OR REPLACE INTO transition_heads VALUES (?,?,?)',
                     (run['id'], issue['id'], json.dumps(current, sort_keys=True)))


def handoff(store, run, issue, now=None):
    """Only a verified claim calls this; each stable worker gets one whole handoff."""
    with store.db:
        if not store.db.in_transaction:
            store.db.execute('BEGIN IMMEDIATE')
        identity = digest([run['id'], issue['id'], 'handoff', 'first'])
        if not store.db.execute('SELECT 1 FROM transitions WHERE identity=?', (identity,)).fetchone():
            append(store, run, issue, 'handoff', issue['status'], 'first', issue, now)


def parts(text):
    if len(text) <= PART_SIZE:
        return [text]
    chunks = [text[i:i + PART_SIZE] for i in range(0, len(text), PART_SIZE)]
    return [f'part {i + 1}/{len(chunks)}\n' + chunk for i, chunk in enumerate(chunks)]


def render(row):
    line = ', '.join((' '.join(row['title'].split()), row['status'], row['issue_id']))
    if row['payload']:
        return line + '\n' + row['payload']
    return line


def flush(store, now=None):
    """Close fixed windows atomically into the existing retry outbox; no models."""
    now = time.time() if now is None else now
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        batches = store.db.execute('''SELECT * FROM transition_batches
          WHERE queued_at IS NULL AND group_id IS NOT NULL AND due_at<=? ORDER BY id LIMIT 100''', (now,)).fetchall()
        for batch in batches:
            if not batch['group_id']:
                continue  # Routing absent: retain inspectable facts, never guess a chat.
            rows = store.db.execute('SELECT * FROM transitions WHERE batch_id=? ORDER BY seq', (batch['id'],)).fetchall()
            for index, text in enumerate(parts('\n'.join(map(render, rows)))):
                identity = f"transition:{batch['id']}:{index}"
                store.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                                 (identity, batch['run_id'], 'transition_batch', text, now))
                store.db.execute('''INSERT OR IGNORE INTO outbox
                  (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
                  (identity, batch['run_id'], batch['group_id'], text, now))
            store.db.execute('UPDATE transition_batches SET queued_at=? WHERE id=?', (now, batch['id']))


def retire_polls(store, now=None):
    """Retire only untouched, positively identified harness heartbeat rows."""
    now = time.time() if now is None else now
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        from .operator_asks import initialize as initialize_asks
        initialize_asks(store.db)
        rows = store.db.execute('''SELECT o.id FROM outbox o JOIN events e ON e.id=o.id
          JOIN runs r ON r.id=o.run_id WHERE o.delivered_at IS NULL AND o.attempts=0
          AND NOT EXISTS (SELECT 1 FROM outbox_rendered rendered WHERE rendered.id=o.id)
          AND e.kind='progress' AND e.text LIKE 'Status: %; native turn: %'
          AND json_extract(r.data,'$.beads.issue_id') IS NOT NULL''').fetchall()
        for row in rows:
            store.db.execute('INSERT OR IGNORE INTO transition_retirements VALUES (?,?,?)',
                             (row['id'], now, 'obsolete harness poll heartbeat; no send attempted'))
            store.db.execute('DELETE FROM outbox WHERE id=?', (row['id'],))
        return len(rows)


AUDIT_ONLY = {'progress', 'working', 'idle', 'native_state', 'manager_reply',
              'manager_compacted', 'compacted', 'turn_completed', 'babysitter_status',
              'queue_boundary', 'queue_continuation_sent', 'started'}
STOP_STATUS = {'interrupted': 'agent-interrupted', 'failed': 'agent-crashed',
               'turn_aborted': 'agent-interrupted', 'agent-crashed': 'agent-crashed',
               'agent-usage-limited': 'agent-usage-limited', 'stopped': 'agent-stopped'}


def bound_issue(store, run):
    wanted = run.get('beads', {}).get('issue_id')
    if not wanted:
        return None
    # A run checkpoint may lag a facade revision; prefer the canonical projection.
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='task_current'").fetchone()
    row = store.db.execute('SELECT data FROM task_current WHERE issue_id=?', (wanted,)).fetchone() if exists else None
    issue = json.loads(row[0]) if row else run.get('beads', {}).get('task_snapshot', {}).get('issue')
    return issue if issue and issue.get('id') == wanted else None


def capture_report(store, run, kind, text, identity, now):
    """Return true when a legacy visible report becomes journal-only or batched."""
    issue = bound_issue(store, run)
    if not issue:
        return False
    if kind in AUDIT_ONLY:
        return True
    statuses = dict(STOP_STATUS, blocked='agent-blocked', completed='agent-turn-ended',
                    awaiting_resume='awaiting-resume', recovered='agent-recovered',
                    supervisor_error='supervisor-error')
    if kind in statuses:
        # Repeated error text / refreshed clocks do not constitute another stop.
        signal(store, run, issue, 'agent', statuses[kind], now)
        return True
    return False  # Explicit asks retain the existing permission/ask protocol.


def steering(store, run, text, identity, now):
    issue = bound_issue(store, run)
    if not issue:
        return
    from .operator_asks import initialize
    initialize(store.db)
    questions = [dict(row) for row in store.db.execute('''SELECT a.id,a.summary,o.text FROM operator_asks a
      LEFT JOIN operator_ask_parts p ON p.ask_id=a.id LEFT JOIN outbox o ON o.id=p.event_id
      WHERE a.run_id=? AND a.resolved_at IS NULL ORDER BY a.created_at,a.id,o.rowid''', (run['id'],))]
    append(store, run, issue, 'steering', issue['status'], identity,
           {'kind': 'steering', 'outstanding_questions': questions, 'steering': text}, now)


def gate(store, run, issue, record, resolved=False):
    binding = record['metadata']['harness_gate']
    payload = {'kind': 'gate', 'gate_id': record['id'], 'subject': binding}
    status = {'deployment': 'deployment-blocked', 'external': 'external-blocked'}.get(
        binding['kind'], 'approval-required')
    key = record['id']
    if resolved:
        resolution = record['metadata']['harness_gate_resolution']
        evidence = resolution['evidence']
        status = 'approval-applied' if evidence['authority_basis'] != 'verified-external' else 'external-gate-resolved'
        payload.update(actor=evidence['issuer'], authority_basis=evidence['authority_basis'],
                       evidence_ref=evidence['evidence_ref'])
        key += ':' + resolution['digest']
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        append(store, run, issue, 'gate', status, key, payload)


def signal(store, run, issue, kind, status, now):
    row = store.db.execute('SELECT signature,sequence FROM transition_signals WHERE run_id=? AND issue_id=? AND kind=?',
                           (run['id'], issue['id'], kind)).fetchone()
    if row and row['signature'] == status:
        return
    sequence = row['sequence'] + 1 if row else 1
    append(store, run, issue, kind, status, sequence, now=now)
    store.db.execute('INSERT OR REPLACE INTO transition_signals VALUES (?,?,?,?,?)',
                     (run['id'], issue['id'], kind, status, sequence))
