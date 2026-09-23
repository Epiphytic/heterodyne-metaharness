"""Beads-owned queue ordering and dependency views; spec/queue-order.md."""
import hashlib
import json
import time

from .beads import BeadsError


def resolve(queue, run, value):
    """Exact native ID, or the immutable facade create-key namespace."""
    identity = value if value.startswith('btq-') else 'btq-harness-' + hashlib.sha256(
        (run['id'] + '\0' + value).encode()).hexdigest()[:24]
    issue = queue.show(identity)
    if not queue.matches(issue):
        raise BeadsError('Task does not match this worker route')
    return issue


def native_order(issue):
    """Ordering metadata as stored natively; bd round-trips metadata values as JSON strings."""
    order = (issue.get('metadata') or {}).get('harness_queue_order')
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except ValueError as exc:
            raise BeadsError(f'Malformed native queue ordering metadata: {exc}') from None
    return order


def order_key(issue):
    order = native_order(issue)
    if order is not None:
        if not isinstance(order, dict) or type(order.get('epoch')) is not int or type(order.get('index')) is not int:
            raise BeadsError('Malformed native queue ordering metadata')
        return (0, -order['epoch'], order['index'], issue['id'])
    return (1, 0, str(issue.get('created_at') or ''), issue['id'])


def pending(queue):
    return [i for i in queue.bd('list', '--status', 'open', '--limit', '0',
                               '--label', 'agent:' + queue.agent, '--label', 'ws:' + queue.ws)
            if queue.matches(i) and not i.get('assignee')]


def prioritize(queue, run, values, issuer):
    """Caller holds harness lock; writes are individually reconciled, never retried."""
    if not issuer.strip():
        raise BeadsError('An issuer record is required (not an authentication claim)')
    issues = [resolve(queue, run, value) for value in values]
    if not issues or len({i['id'] for i in issues}) != len(issues):
        raise BeadsError('Provide distinct tasks to prioritize')
    if any(i.get('status') != 'open' or i.get('assignee') for i in issues):
        raise BeadsError('Prioritize only pending unclaimed tasks; in-flight tasks are unaffected')
    ordered = pending(queue)
    for issue in ordered:
        order_key(issue)  # Fail before mutation on invalid metadata.
    epoch = max([time.time_ns()] + [(native_order(i) or {}).get('epoch', 0) + 1 for i in ordered])
    result = []
    for index, issue in enumerate(issues):
        value = {'epoch': epoch, 'index': index, 'issuer': issuer}
        queue.bd('update', issue['id'], '--set-metadata', 'harness_queue_order=' + json.dumps(value))
        current = queue.show(issue['id'])
        # bd round-trips metadata values as strings; compare the native shape.
        if native_order(current) != value:
            raise BeadsError('Queue order write uncertain; inspect native metadata before retry')
        result.append(current)
    return result


def graph(beads, run):
    queue = beads._queue(run)
    eligible = {i['id'] for i in beads.ready(run)}
    issues = sorted(pending(queue), key=order_key)
    cache = {i['id']: queue.show(i['id']) for i in issues}
    traversed = 0
    def chains(identity, trail):
        nonlocal traversed
        traversed += 1
        if traversed > 2000:
            raise BeadsError('Dependency traversal exceeds bounded view')
        if identity in trail:
            raise BeadsError('Dependency cycle detected')
        if len(trail) >= 64 or len(cache) > 1000:
            raise BeadsError('Dependency graph exceeds bounded view')
        item = cache.setdefault(identity, None)
        if item is None:
            item = cache[identity] = queue.show(identity)
        edges = item.get('dependencies', [])
        if item.get('dependency_count', 0) and not edges:
            raise BeadsError('Native task omitted dependency records')
        paths = []
        for edge in edges:
            if edge.get('dependency_type') != 'blocks' or edge.get('status') == 'closed':
                continue
            child = edge['id']
            paths.append([identity, child])
            paths.extend([[identity] + path for path in chains(child, trail + [identity])])
            if len(paths) > 1000:
                raise BeadsError('Dependency paths exceed bounded view')
        return paths
    return [dict(cache[i['id']], eligible=i['id'] in eligible,
                 blocked_by_chains=chains(i['id'], [])) for i in issues]


def dependency(queue, run, operation, value, blocker, issuer):
    issue, prerequisite = resolve(queue, run, value), resolve(queue, run, blocker)
    if not issuer.strip() or issue['id'] == prerequisite['id'] or issue.get('status') == 'closed':
        raise BeadsError('Require issuer, distinct routed tasks and an open dependent')
    if operation == 'remove' and prerequisite['id'] in issue.get('metadata', {}).get('harness_gates', {}):
        raise BeadsError('Gate dependency removal requires explicit gate reconciliation')
    approval = issue.get('metadata', {}).get('design_approval')
    if operation == 'remove' and prerequisite['id'] == approval:
        raise BeadsError('Approval dependency removal needs the design approval workflow')
    # Native cycle checking remains enabled. No retry on uncertain outcome.
    audit = {'operation': operation, 'blocker': prerequisite['id'], 'issuer': issuer, 'at': time.time()}
    queue.bd('update', issue['id'], '--set-metadata', 'harness_dependency_request=' + json.dumps(audit))
    queue.bd('dep', operation, issue['id'], prerequisite['id'])
    return queue.show(issue['id'])
