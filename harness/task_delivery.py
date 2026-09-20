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


def steps(binding):
    if binding.get("version") == 2:
        from .task_formulas import profile
        return profile(binding)
    return STEPS


def contract(issue):
    value = (issue.get('metadata') or {}).get(FIELD)
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('version') not in (1, 2):
        raise BeadsError('Invalid delivery contract')
    roles = tuple(steps(value))
    if (value.get('role') not in ('parent', *roles)
            or not isinstance(value.get('members'), dict)
            or set(value['members']) != {'parent', *roles}):
        raise BeadsError('Invalid delivery contract')
    ids = list(value['members'].values())
    if not all(isinstance(key, str) and key for key in ids) or len(set(ids)) != len(roles) + 1:
        raise BeadsError('Delivery members must be distinct issue IDs')
    if value['members'][value['role']] != issue['id']:
        raise BeadsError('Delivery identity mismatch')
    return value


def members(queue, issue):
    binding = contract(issue)
    roles = tuple(steps(binding))
    result = {role: queue.show(key) for role, key in binding['members'].items()}
    for role, item in result.items():
        other = contract(item)
        if not other or other != dict(binding, role=role):
            raise BeadsError('Incomplete or conflicting delivery group')
    for dependent, blocker in zip(roles[1:], roles):
        edges = result[dependent].get('dependencies') or []
        if not any(e.get('id') == result[blocker]['id'] and e.get('dependency_type') == 'blocks' for e in edges):
            raise BeadsError('Delivery blocking edge missing')
    for role in roles:
        if not any(e.get('id') == result['parent']['id'] and e.get('dependency_type') == 'parent-child'
                   for e in result[role].get('dependencies', [])):
            raise BeadsError('Delivery parent-child edge missing')
    return result


def create(store, supervisor, beads, run, plan, binding=None):
    """Only creates new deterministic groups; repeated keys reconcile exact content."""
    binding = binding or {'version': 1}
    stage_map = steps(binding)
    roles = tuple(stage_map)
    if (not isinstance(plan, dict) or set(plan) != {'key', 'title', 'description', 'routes'}
            or not isinstance(plan['routes'], dict) or set(plan['routes']) != set(roles)):
        raise BeadsError('Delivery plan needs key/title/description and exact formula role routes')
    if not all(isinstance(value, str) and value.strip() for value in
               [plan['key'], plan['title'], plan['description'], *plan['routes'].values()]):
        raise BeadsError('Delivery plan values must be nonempty strings')
    from .store import TERMINAL
    from .tasks import admit, observe
    routes = {'parent': run, **{role: store.get(plan['routes'][role]) for role in roles}}
    if any(destination['state'] in TERMINAL for destination in routes.values()):
        raise BeadsError('Cannot route delivery to a terminated session')
    # Parent key stays independent of formula/routes: changed admission cannot fork a group.
    keys = {role: 'delivery:' + plan['key'] + ':' + role for role in routes}
    ids = {role: identity(destination, keys[role]) for role, destination in routes.items()}
    for role, destination in routes.items():
        admit(store, supervisor, beads, store.get(destination['id']), plan['title'] + ' [' + role + ']',
              description(plan, binding, role), key=keys[role],
              kind=('research' if binding.get('formula') == 'research-v1' else 'task'),
              metadata={FIELD: dict(binding, role=role, members=ids)})
    queue = beads._queue(run)
    edges = [(ids[role], ids['parent'], 'parent-child') for role in roles]
    edges += [(ids[a], ids[b], 'blocks') for a, b in
              zip(roles[1:], roles)]
    for child, parent, kind in edges:
        current = queue.show(child)
        if not any(e.get('id') == parent and e.get('dependency_type') == kind for e in current.get('dependencies', [])):
            queue.bd('dep', 'add', child, parent, '--type', kind)
    result = members(queue, queue.show(ids['parent']))
    for issue in result.values():
        observe(store, issue)
    return result


def description(plan, binding, role):
    """Keep legacy request bytes stable while describing versioned ownership."""
    if role == 'parent':
        count = 'three' if binding['version'] == 1 else 'mandatory'
        responsibility = f'Verify and close delivery only after all {count} steps close.'
    else:
        responsibility = 'Record only these lifecycle stages: ' + ', '.join(steps(binding)[role]) + '.'
    return (plan['description'] + '\n\nDelivery responsibility: ' + role + '. ' + responsibility + '\n'
            'Read linked delivery_steps artifacts through task show; preserve operator review/deployment authority.')


def history(issue):
    binding = contract(issue)
    events = (issue.get('metadata') or {}).get('harness_lifecycle') or []
    expected = steps(binding).get(binding['role'], ())
    if [e.get('stage') for e in events] != list(expected):
        raise BeadsError('Delivery step has unfinished lifecycle evidence')
    for event in events:
        digest = hashlib.sha256(json.dumps([event['stage'], event['evidence']], sort_keys=True).encode()).hexdigest()
        if event.get('digest') != digest:
            raise BeadsError('Delivery lifecycle digest mismatch')
    return events


def prior_history(queue, issue):
    group = members(queue, issue)
    binding = contract(issue)
    role = binding['role']
    roles = tuple(steps(binding))
    prior = roles[:roles.index(role)] if role in roles else roles
    events = []
    for step in prior:
        if group[step].get('status') != 'closed':
            raise BeadsError('Prior delivery step is not closed')
        from .task_gates import check
        check(queue, group[step])
        pinned = artifact(group[step])
        from .task_formulas import validate
        for event in history(group[step]):
            validate({'workdir': pinned.get('checkout')}, binding, event['stage'], event['evidence'], events, require_head=False)
            events.append(event)
    return events


def artifact(issue):
    value = (issue.get('metadata') or {}).get('harness_delivery_artifact')
    if contract(issue).get('formula') in ('research-v1', 'configuration-v1'):
        if value != {'evidence_digest': history(issue)[-1]['digest']}:
            raise BeadsError('Delivery evidence artifact mismatch')
        return value
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
    from .task_stages import clean_commit
    binding = contract(issue)
    prior = prior_history(queue, issue)
    if binding['role'] == 'parent':
        # prior_history validates all required children using their pinned checkouts.
        return
    events = history(issue)
    pinned = artifact(issue)
    if 'commit' in pinned:
        clean_commit(run, pinned['commit'])
    for event in events:
        from .task_formulas import validate
        validate(run, binding, event['stage'], event['evidence'], prior, require_head=False)
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
        if not linked or linked != dict(binding, role=role):
            raise BeadsError('Conflicting linked delivery projection; reconcile task group')
        meta = other.get('metadata') or {}
        result[role] = {'id': key, 'status': other['status'],
                        'artifact': meta.get('harness_delivery_artifact'),
                        'lifecycle': meta.get('harness_lifecycle', [])}
    return result
