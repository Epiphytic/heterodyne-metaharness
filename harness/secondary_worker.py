"""One explicit secondary slot; preserves the primary claim, pane and worktree."""
import copy
import uuid

from .agents import Adapter
from .beads import BeadsError
from .task_workspace import prepare
from .workspace import git


def permitted(supervisor, run):
    settings = supervisor.config.get('beads', {}).get('blockers', {})
    if settings.get('enabled') is not True or settings.get('secondary_enabled') is not True:
        raise BeadsError('Secondary execution is not explicitly enabled')
    if run.get('resume_required') or run.get('beads', {}).get('recovery_required') or run.get('state') == 'stopped':
        raise BeadsError('Run-wide stop/recovery holds both workers')
    if run.get('beads', {}).get('pickup_enabled') is False:
        raise BeadsError('Pickup is paused')


def start(supervisor, run, issue_id):
    permitted(supervisor, run)
    if run.get('secondary'):
        raise BeadsError('Secondary slot already retained; reconcile it before another launch')
    if run.get('native_turn_state') != 'idle' or not run.get('native_session_id'):
        raise BeadsError('Primary requires confirmed native idle before secondary admission')
    if run.get('observed_state') == 'awaiting_approval':
        raise BeadsError('Unresolved native approval prevents secondary admission')
    queue = supervisor.beads._queue(run)
    if (queue.state / 'paused').exists():
        raise BeadsError('Queue pickup is paused')
    primary_id = run['beads']['issue_id']
    primary = queue.show(primary_id)
    if primary.get('assignee') != queue.worker or primary.get('status') != 'in_progress':
        raise BeadsError('Primary ownership changed')
    from .task_gates import metadata
    gates = metadata(primary).get('harness_gates', {})
    blockers = [key for key, binding in gates.items() if binding.get('blocker')
                and queue.show(key).get('status') != 'closed']
    if not blockers:
        raise BeadsError('Primary must have an unresolved managed blocker')
    eligible = {item['id'] for item in supervisor.beads.ready(run)}
    if issue_id == primary_id or issue_id not in eligible:
        raise BeadsError('Secondary task must be independently eligible')
    # Initial profile deliberately retains the clean-boundary rule. It cannot
    # infer whether dirty primary files are quiescent, even from an idle hook.
    if git(run['workdir'], 'status', '--porcelain'):
        raise BeadsError('Secondary profile requires a clean retained primary checkout')
    from .task_review import can_handoff
    for item in queue.bd('list', '--assignee', queue.worker, '--status', 'in_progress', '--limit', '0'):
        if item['id'] != primary_id and item.get('status') == 'in_progress':
            can_handoff(run, queue, queue.show(item['id']))
    record = prepare(run, supervisor.store.root, issue_id, primary_id)
    config = copy.deepcopy(supervisor.config.get('beads', {}).get('blockers', {}).get('secondary_provider', {}))
    agent = config.pop('agent', run['agent'])
    if agent != run['agent'] or agent not in ('codex', 'claude'):
        raise BeadsError('Secondary provider must match enrolled coding agent')
    config.setdefault('env', {}).update(supervisor.beads.environment(run))
    slot = dict(id=run['id'], agent=agent, managed_role='secondary', workdir=record['path'],
                tmux_session=run['tmux_session'] + '-secondary', config=config,
                beads=dict(run['beads'], issue_id=issue_id), created_at=supervisor.clock(),
                launch_prompt='', prompt_state='not_replayed', phase='claiming', primary_id=primary_id,
                blocker_ids=blockers)
    if agent == 'claude':
        slot['native_session_id'] = str(uuid.uuid5(uuid.NAMESPACE_URL,
            'workstream-secondary:' + run['id'] + ':' + issue_id))
    # Identity/claim intent persists before mutation. Unknown outcomes require
    # inspection; start never retries a possibly claimed/launched slot.
    run['secondary'] = slot
    supervisor.persist(run)
    queue.bd('update', issue_id, '--claim')
    issue = queue.show(issue_id)
    if issue.get('assignee') != queue.worker or issue.get('status') != 'in_progress' or not supervisor.beads._allowed(queue, issue):
        raise BeadsError('Secondary claim uncertain or no longer eligible')
    slot['phase'] = 'launching'
    supervisor.persist(run)
    supervisor.launch(run, slot)
    slot['phase'] = 'ready-for-steering'
    supervisor.persist(run)
    from .tasks import observe, record_claim_baseline
    observe(supervisor.store, issue)
    record_claim_baseline(supervisor.store, run, issue)
    return slot


def target(run):
    slot = run.get('secondary')
    if not slot or slot.get('phase') not in ('ready-for-steering', 'working'):
        raise BeadsError('Secondary slot is not ready; reconcile launch state')
    return slot


def observe(supervisor, run):
    slot = run.get('secondary')
    if not slot:
        return
    info = supervisor.tmux.inspect(slot)
    if not info.get('alive'):
        slot['error'] = 'Secondary pane missing/exited; explicit recovery required'
        supervisor.persist(run)
        return
    observation = Adapter(slot['agent']).observe(slot, info.get('text', ''))
    native = observation.get('native_session_id')
    if native:
        slot['native_session_id'] = native
    slot['pane_id'] = info['pane_id']
    slot['observed_state'] = observation['state']
    supervisor.persist(run)


def finish(supervisor, run):
    permitted(supervisor, run)
    slot = target(run)
    if slot.get('native_turn_state') != 'idle' or slot.get('observed_state') == 'awaiting_approval':
        raise BeadsError('Secondary must reach a confirmed idle boundary')
    queue = supervisor.beads._queue(run)
    issue = queue.show(slot['beads']['issue_id'])
    if issue.get('status') != 'closed' or issue.get('assignee') != queue.worker:
        raise BeadsError('Secondary lifecycle must be completed by its owner')
    if git(slot['workdir'], 'status', '--porcelain'):
        raise BeadsError('Secondary checkout must remain clean')
    from .task_blockers import assert_worker_unblocked
    assert_worker_unblocked(supervisor.beads, run)
    slot['phase'] = 'stopping'
    supervisor.persist(run)
    supervisor.tmux.stop(slot)
    slot['phase'] = 'retained'
    run.setdefault('retained_secondary', []).append(slot)
    del run['secondary']
    supervisor.persist(run)
    # Parent pane and conversation never moved. Authorized steering can now resume.
    return {'primary_id': run['beads']['issue_id'], 'ready_for_steering': True}


def submission_target(supervisor, run, role):
    """Apply the same block/recovery checks to direct input and inbox draining."""
    from .task_blockers import assert_worker_unblocked
    if role == 'manager':
        return supervisor.manager_run(run)
    if role == 'secondary':
        permitted(supervisor, run)
        destination = target(run)
        if not destination.get('native_session_id'):
            raise BeadsError('Register secondary native identity before steering')
    elif role == 'worker':
        if run.get('secondary'):
            raise BeadsError('Finish secondary slot before resuming primary')
        destination = run
    else:
        raise BeadsError('Unknown submission target')
    assert_worker_unblocked(supervisor.beads, destination)
    return destination
