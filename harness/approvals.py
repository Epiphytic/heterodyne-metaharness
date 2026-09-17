"""Conservative harness decisions, never native prompt responses; spec/approvals.md."""
import hashlib
import json
from pathlib import Path
import re
import time

from .beads import BeadsError

THRESHOLD = 3


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS approval_asks (
      id TEXT PRIMARY KEY, run_id TEXT, issue_id TEXT, pattern TEXT, data TEXT,
      classification TEXT, decision TEXT, evidence_ref TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS approval_policy (
      scope TEXT, pattern TEXT, decision TEXT, source TEXT, version INTEGER,
      PRIMARY KEY(scope,pattern));
    CREATE TABLE IF NOT EXISTS approval_versions (
      version INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, pattern TEXT,
      decision TEXT, source TEXT, evidence_ref TEXT, created_at REAL);
    ''')


def classify(run, request):
    argv = request.get('argv')
    if not isinstance(argv, list) or not argv or not all(isinstance(x,str) and x for x in argv):
        return 'unrecognized-command'
    if request.get('issue_id') != run.get('beads', {}).get('issue_id') or not request.get('evidence_ref'):
        return 'task-relevance-unverified'
    root = Path(run['workdir']).resolve()
    if Path(request.get('cwd','')).resolve() != root:
        return 'out-of-scope-path'
    words = ' '.join(argv).lower()
    if any(word in words for word in ('.ssh', '.env', 'credential', 'password', 'secret', 'token', '.aws')):
        return 'credential-access'
    if any(word in argv for word in ('--force','-f','--hard','--force-with-lease')):
        return 'force-operation'
    if Path(argv[0]).name in ('systemctl','service','reboot','shutdown','kill','killall','pkill'):
        return 'service-lifecycle'
    if Path(argv[0]).name in ('rm','rmdir','mv','dd','mkfs','shred'):
        return 'destructive-operation'
    if Path(argv[0]).name in ('curl','wget','ssh','scp','rsync','nc') or ('git' == argv[0] and 'push' in argv):
        return 'network-operation'
    if any(any(char in token for char in ('\n','\r','\0',';','|','`','$','>','<')) for token in argv):
        return 'shell-or-expansion'
    # Fixed argv forms avoid aliases, shell parsing and executable flags such
    # as git --ext-diff. Executable resolution remains the native gate's job.
    if argv in (['git','status','--short'], ['git','diff','--no-ext-diff','--stat'],
                ['git','log','-5','--oneline'], ['git','rev-parse','HEAD']):
        return 'scoped-read'
    if len(argv)==4 and argv[:2]==['sed','-n'] and re.fullmatch(r'[1-9][0-9]{0,5}(,[1-9][0-9]{0,5})?p',argv[2]):
        target=(root/argv[3]).resolve()
        if target.is_relative_to(root) and target.is_file():
            return 'scoped-read'
        return 'out-of-scope-path'
    # Tests, git hooks, module entrypoints and PR creation execute code or use
    # network credentials. A command name alone cannot prove them harmless.
    return 'execution-needs-review'


def _policy(db, scope, pattern, decision, source, evidence):
    cursor=db.execute('INSERT INTO approval_versions(scope,pattern,decision,source,evidence_ref,created_at) VALUES (?,?,?,?,?,?)',
                      (scope,pattern,decision,source,evidence,time.time()))
    db.execute('INSERT OR REPLACE INTO approval_policy VALUES (?,?,?,?,?)',
               (scope,pattern,decision,source,cursor.lastrowid))


def inspect(store, run):
    initialize(store.db)
    return {'policy':[dict(row) for row in store.db.execute('SELECT * FROM approval_policy WHERE scope IN (?,?)',(run['id'],'global'))],
            'asks':[dict(row) for row in store.db.execute('SELECT * FROM approval_asks WHERE run_id=? ORDER BY created_at DESC LIMIT 100',(run['id'],))],
            'versions':[dict(row) for row in store.db.execute('SELECT * FROM approval_versions WHERE scope IN (?,?) ORDER BY version DESC LIMIT 100',(run['id'],'global'))],
            'native_permissions_modified':False}


def override(store, run, pattern, decision, evidence, scope='run'):
    if not re.fullmatch(r'[a-f0-9]{64}', pattern) or not evidence or decision not in ('allow','route'):
        raise BeadsError('Override requires exact pattern digest, allow/route and operator evidence')
    initialize(store.db)
    with store.db:
        _policy(store.db, 'global' if scope=='global' else run['id'], pattern, decision, 'operator', evidence)
    return inspect(store,run)


def incident(store, run, pattern, evidence):
    """Retroactive incident demotes both tiers; explicit operator review may lift it."""
    if not re.fullmatch(r'[a-f0-9]{64}', pattern) or not evidence:
        raise BeadsError('Incident requires exact pattern digest and retained evidence')
    initialize(store.db)
    with store.db:
        for scope in (run['id'], 'global'):
            _policy(store.db, scope, pattern, 'route', 'incident', evidence)
    return inspect(store, run)


def evaluate(store, beads, run, request):
    initialize(store.db)
    queue=beads._queue(run)
    issue=queue.show(run.get('beads',{}).get('issue_id'))
    if issue.get('assignee')!=queue.worker or issue.get('status')!='in_progress':
        raise BeadsError('Approval classification requires a verified current task owner')
    if not isinstance(request, dict) or not request.get('key') or not request.get('evidence_ref'):
        raise BeadsError('Ask key and evidence reference required')
    if not re.fullmatch(r'[A-Za-z0-9_./:#@+ =-]{1,512}', str(request['evidence_ref'])):
        raise BeadsError('Use a bounded evidence locator, not command output or secret values')
    raw=json.dumps(request,sort_keys=True)
    identity=hashlib.sha256((run['id']+'\0'+str(request['key'])).encode()).hexdigest()
    pattern=hashlib.sha256(json.dumps(request.get('argv'),sort_keys=True).encode()).hexdigest()
    digest=hashlib.sha256(raw.encode()).hexdigest()
    classification=classify(run,request)
    safe=classification=='scoped-read' and not run.get('resume_required') and not run.get('beads',{}).get('recovery_required')
    display={'argv':request.get('argv') if safe else ['[redacted; inspect native ask]'], 'cwd':run['workdir'], 'request_sha256':digest}
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        prior=store.db.execute('SELECT data,decision,classification FROM approval_asks WHERE id=?',(identity,)).fetchone()
        policies=store.db.execute('SELECT decision,source FROM approval_policy WHERE pattern=? AND scope IN (?,?)',(pattern,run['id'],'global')).fetchall()
        denied=any(row['decision']=='route' for row in policies)
        if prior:
            if json.loads(prior['data'])['request_sha256']!=digest:
                raise BeadsError('Ask key already identifies different request')
            decision = prior['decision'] if safe and not denied else 'route'
            if decision == 'route':
                _route(store,run,identity,classification,request['evidence_ref'])
            return {'id':identity,'pattern':pattern,'decision':decision,
                    'classification':prior['classification'],'native_approval_required':True}
        decision='allow' if safe and not denied else 'route'
        store.db.execute('INSERT INTO approval_asks VALUES (?,?,?,?,?,?,?,?,?)',
                         (identity,run['id'],issue['id'],pattern,json.dumps(display),classification,decision,request['evidence_ref'],time.time()))
        if decision=='allow':
            _promote(store.db,run['id'],pattern,request['evidence_ref'])
        else:
            _route(store,run,identity,classification,request['evidence_ref'])
    return {'id':identity,'pattern':pattern,'decision':decision,'classification':classification,'native_approval_required':True}


def _promote(db, run_id, pattern, evidence):
    rows=db.execute("SELECT run_id,COUNT(*) n FROM approval_asks WHERE pattern=? AND decision='allow' GROUP BY run_id HAVING COUNT(*)>=?",(pattern,THRESHOLD)).fetchall()
    scopes=([run_id] if any(row['run_id']==run_id for row in rows) else [])+(['global'] if len(rows)>=2 else [])
    for scope in scopes:
        if not db.execute('SELECT 1 FROM approval_policy WHERE scope=? AND pattern=?',(scope,pattern)).fetchone():
            _policy(db,scope,pattern,'allow','automatic',evidence+f'; threshold={THRESHOLD}; qualifying_runs={len(rows)}')


def _route(store,run,identity,classification,evidence):
    text=(f'Operator question {identity}: review native ask ({classification}). '
          f'Evidence: {evidence}. Inspect workstream approvals {run["id"]} inspect; '
          'record policy review through the override command. Dangerous actions still require native review. '
          'Native permissions remain native; this message does not accept the prompt.')
    store.db.execute('INSERT OR IGNORE INTO inbox(id,run_id,text,created_at,state,target) VALUES (?,?,?,?,?,?)',
                     ('approval:'+identity,run['id'],text,time.time(),'pending','manager'))
    store.event(run,'operator_question',text,'approval-visible:'+identity)
    if run.get('group_id'):
        # A previous ask may predate group binding. Reconcile the same event ID
        # without creating another manager question or changing a routed target.
        store.db.execute('''INSERT OR IGNORE INTO outbox
            (id,run_id,group_id,text,created_at) VALUES (?,?,?,?,?)''',
            ('approval-visible:'+identity,run['id'],run['group_id'],f'[{run["name"]}] {text}',time.time()))


def configure_cli(sub):
    approvals = sub.add_parser('approvals')
    approvals.add_argument('name')
    approval_actions = approvals.add_subparsers(dest='approval_action', required=True)
    approval_actions.add_parser('inspect')
    ask = approval_actions.add_parser('ask'); ask.add_argument('--file', required=True)
    for name in ('override', 'incident'):
        action = approval_actions.add_parser(name)
        action.add_argument('--pattern', required=True)
        action.add_argument('--evidence', required=True)
        if name == 'override':
            action.add_argument('--decision', choices=('allow','route'), required=True)
            action.add_argument('--scope', choices=('run','global'), default='run')


def handle_cli(args, store, beads):
    run = store.get(args.name)
    if args.approval_action == 'inspect':
        return inspect(store, run)
    if args.approval_action == 'ask':
        return evaluate(store, beads, run, json.loads(Path(args.file).read_text()))
    if args.approval_action == 'incident':
        return incident(store, run, args.pattern, args.evidence)
    return override(store, run, args.pattern, args.decision, args.evidence, args.scope)
