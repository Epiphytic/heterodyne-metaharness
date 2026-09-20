"""Explicit linked delivery ownership; authority: spec/delivery-tasks.md."""
import hashlib
import json
from pathlib import Path

from .beads import BeadsError
from .workspace import git

ROLES = ('implementation', 'review', 'deployment')
STEPS = {'implementation': ('committed', 'tested', 'pr-open'),
         'review': ('merged', 'final-tested'), 'deployment': ('deployed', 'close-ready')}
FIELD = 'harness_delivery'


def identity(run, key):
    return 'btq-harness-' + hashlib.sha256((run['id'] + '\0' + key).encode()).hexdigest()[:24]


def contract(issue):
    value = (issue.get('metadata') or {}).get(FIELD)
    if value is None:
        return None
    if (not isinstance(value, dict) or value.get('version') != 1
            or value.get('role') not in ('parent', *ROLES)
            or not isinstance(value.get('members'), dict)
            or set(value['members']) != {'parent', *ROLES}):
        raise BeadsError('Invalid delivery contract')
    ids = list(value['members'].values())
    if not all(isinstance(key, str) and key for key in ids) or len(set(ids)) != 4:
        raise BeadsError('Delivery members must be four distinct issue IDs')
    if value['members'][value['role']] != issue['id']:
        raise BeadsError('Delivery identity mismatch')
    return value


def members(queue, issue):
    binding = contract(issue)
    result = {role: queue.show(key) for role, key in binding['members'].items()}
    for role, item in result.items():
        other = contract(item)
        if not other or other['members'] != binding['members'] or other['role'] != role:
            raise BeadsError('Incomplete or conflicting delivery group')
    for dependent, blocker in (('review', 'implementation'), ('deployment', 'review')):
        edges = result[dependent].get('dependencies') or []
        if not any(e.get('id') == result[blocker]['id'] and e.get('dependency_type') == 'blocks' for e in edges):
            raise BeadsError('Delivery blocking edge missing')
    for role in ROLES:
        if not any(e.get('id') == result['parent']['id'] and e.get('dependency_type') == 'parent-child'
                   for e in result[role].get('dependencies', [])):
            raise BeadsError('Delivery parent-child edge missing')
    return result


def create(store, supervisor, beads, run, plan):
    """Only creates new deterministic groups; repeated keys reconcile exact content."""
    if (not isinstance(plan, dict) or set(plan) != {'key', 'title', 'description', 'routes'}
            or not isinstance(plan['routes'], dict) or set(plan['routes']) != set(ROLES)):
        raise BeadsError('Delivery plan needs key/title/description and three explicit role routes')
    if not all(isinstance(value, str) and value.strip() for value in
               [plan['key'], plan['title'], plan['description'], *plan['routes'].values()]):
        raise BeadsError('Delivery plan values must be nonempty strings')
    from .store import TERMINAL
    from .tasks import admit, observe
    routes = {'parent': run, **{role: store.get(plan['routes'][role]) for role in ROLES}}
    if any(destination['state'] in TERMINAL for destination in routes.values()):
        raise BeadsError('Cannot route delivery to a terminated session')
    keys = {role: 'delivery:' + plan['key'] + ':' + role for role in routes}
    ids = {role: identity(destination, keys[role]) for role, destination in routes.items()}
    for role, destination in routes.items():
        responsibility = ('Verify and close delivery only after all three steps close.' if role == 'parent' else
                          'Record only these lifecycle stages: ' + ', '.join(STEPS[role]) + '.')
        text = plan['description'] + '\n\nDelivery responsibility: ' + role + '. ' + responsibility + '\n' + (
            'Read linked delivery_steps artifacts through task show; preserve operator review/deployment authority.')
        admit(store, supervisor, beads, store.get(destination['id']), plan['title'] + ' [' + role + ']', text,
                     key=keys[role], kind='task', metadata={FIELD: {'version': 1, 'role': role, 'members': ids}})
    queue = beads._queue(run)
    edges = [(ids[role], ids['parent'], 'parent-child') for role in ROLES]
    edges += [(ids[a], ids[b], 'blocks') for a, b in
              (('review', 'implementation'), ('deployment', 'review'))]
    for child, parent, kind in edges:
        current = queue.show(child)
        if not any(e.get('id') == parent and e.get('dependency_type') == kind for e in current.get('dependencies', [])):
            queue.bd('dep', 'add', child, parent, '--type', kind)
    result = members(queue, queue.show(ids['parent']))
    for issue in result.values():
        observe(store, issue)
    return result


def history(issue):
    from .task_stages import STAGES
    binding = contract(issue)
    events = (issue.get('metadata') or {}).get('harness_lifecycle') or []
    expected = STEPS.get(binding['role'], ())
    if [e.get('stage') for e in events] != list(expected):
        raise BeadsError('Delivery step has unfinished lifecycle evidence')
    for event in events:
        digest = hashlib.sha256(json.dumps([event['stage'], event['evidence']], sort_keys=True).encode()).hexdigest()
        if event.get('digest') != digest or event['stage'] not in STAGES:
            raise BeadsError('Delivery lifecycle digest mismatch')
    return events


def prior_history(queue, issue):
    group = members(queue, issue)
    role = contract(issue)['role']
    prior = ROLES[:ROLES.index(role)] if role in ROLES else ROLES
    events = []
    for step in prior:
        if group[step].get('status') != 'closed':
            raise BeadsError('Prior delivery step is not closed')
        pinned = artifact(group[step])
        from .task_stages import validate_evidence
        for event in history(group[step]):
            validate_evidence({'workdir': pinned['checkout']}, event['stage'], event['evidence'], events, require_head=False)
            events.append(event)
    return events


def artifact(issue):
    value = (issue.get('metadata') or {}).get('harness_delivery_artifact')
    if not isinstance(value, dict) or not all(value.get(k) for k in ('checkout', 'commit', 'branch')):
        raise BeadsError('Delivery step lacks pinned artifact')
    path = value['checkout']
    if not Path(path).is_absolute() or git(path, 'rev-parse', '--show-toplevel') != str(Path(path).resolve()):
        raise BeadsError('Delivery artifact checkout unavailable')
    if git(path, 'rev-parse', value['commit'] + '^{commit}') != value['commit'] or git(path, 'log', '-1', '--format=%G?', value['commit']) != 'G':
        raise BeadsError('Delivery artifact commit/signature mismatch')
    if history(issue)[-1]['evidence']['commit'] != value['commit']:
        raise BeadsError('Delivery artifact does not match completed step')
    return value


def close_step(queue, run, issue):
    from .task_stages import validate_evidence, clean_commit
    binding = contract(issue)
    prior = prior_history(queue, issue)
    if binding['role'] == 'parent':
        # prior_history validates all required children using their pinned checkouts.
        return
    events = history(issue)
    pinned = artifact(issue)
    clean_commit(run, pinned['commit'])
    for event in events:
        validate_evidence(run, event['stage'], event['evidence'], prior, require_head=False)
        prior.append(event)


def projection(db, issue):
    """Read-only linked evidence for the already route-validated current task."""
    binding = contract(issue)
    if not binding:
        return {}
    result = {}
    for role, key in binding['members'].items():
        row = db.execute('SELECT data FROM task_current WHERE issue_id=?', (key,)).fetchone()
        if not row:
            continue
        other = json.loads(row[0])
        linked = contract(other)
        if not linked or linked['members'] != binding['members'] or linked['role'] != role:
            raise BeadsError('Conflicting linked delivery projection; reconcile task group')
        meta = other.get('metadata') or {}
        result[role] = {'id': key, 'status': other['status'],
                        'artifact': meta.get('harness_delivery_artifact'),
                        'lifecycle': meta.get('harness_lifecycle', [])}
    return result
