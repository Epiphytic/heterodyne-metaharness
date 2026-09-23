import ast
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness import channel_status as status
from harness import channel_status_hook as hook
from install_channel_status import plan, HOOK, ANCHOR
from install_brain import apply


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        path = self.home/'workstreams/harness.sqlite3'; path.parent.mkdir()
        self.db = sqlite3.connect(path); self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE runs (group_id TEXT,state TEXT,data TEXT); CREATE TABLE task_current(issue_id TEXT,data TEXT);')
        self.run = dict(id='run',name='mdk',agent='codex',state='working',native_turn_state='running',
                        tmux_session='worker',pane_id='%7',beads={'workstream':'mdk'})
        self.db.execute('INSERT INTO runs VALUES (?,?,?)',('aa','working',json.dumps(self.run)))
        self.issue('active','in_progress', [{'stage':'tested'}])
        self.issue('pending','open')
        self.issue('closed','closed')
        self.db.commit()

    def issue(self, identity, state, lifecycle=None, **extra):
        data = dict(id=identity,title=identity+' title',status=state,created_at='1',
                    labels=['agent:codex','ws:mdk'],metadata={'harness_lifecycle':lifecycle or []},**extra)
        self.db.execute('INSERT INTO task_current VALUES (?,?)',(identity,json.dumps(data)))

    def test_exact_render_and_read_only(self):
        path = self.home/'workstreams/harness.sqlite3'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        data = status.snapshot(self.home,'AA')
        expected = '''Workstream: mdk (run)
State: working; native: running
Queue: cached projection; claim eligibility requires live validation.
Stages (latest recorded): claimed=0, committed=0, tested=1, pr-open=0, merged=0, final-tested=0, deployed=0, done=1
In flight:
active | active title [executing]
Pending (claim order; blocked entries labeled):
pending | pending title [ready]
Pane %7, run run; captured 2026-09-20T22:00:00+00:00; live
one
two'''
        for _ in range(2):
            self.assertEqual(status.render(data,('live','one\ntwo'),'2026-09-20T22:00:00+00:00'),expected)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_unknown_ambiguous_and_route_isolation(self):
        self.assertIn('Known workstreams: mdk',status.render(status.snapshot(self.home,'bb'),('', ''),'now'))
        self.db.execute('INSERT INTO task_current VALUES (?,?)', ('foreign',json.dumps(dict(id='foreign', title='private', status='open',labels=['agent:codex','ws:other']))))
        self.db.commit()
        self.assertNotIn('foreign',[t['id'] for t in status.snapshot(self.home,'aa')['tasks']])
        self.db.execute('INSERT INTO runs VALUES (?,?,?)',('aa','working',json.dumps(dict(self.run,id='other'))))
        self.db.commit()
        with self.assertRaisesRegex(ValueError,'Ambiguous'): status.snapshot(self.home,'aa')

    def test_order_and_missing_dependency_is_not_claimable(self):
        self.db.execute('DELETE FROM task_current WHERE issue_id="pending"')
        self.issue('pending','open',dependencies=[dict(id='missing',dependency_type='blocks')])
        self.issue('first','open')
        self.db.commit()
        data = status.snapshot(self.home,'aa')
        self.assertEqual([r['id'] for r in data['tasks']],['active','first','pending'])
        self.assertEqual(data['tasks'][-1]['view'],'blocked')
        self.assertFalse(data['tasks'][-1]['eligible'])

    def test_terminal_runs_with_open_beads_remain_visible(self):
        for state in ('completed', 'stopped', 'archived'):
            self.run['state'] = state
            self.db.execute('UPDATE runs SET state=?,data=?', (state, json.dumps(self.run)))
            self.db.commit()
            self.assertEqual(status.snapshot(self.home, 'aa')['run']['state'], state)
            self.assertEqual(status.snapshot(self.home, 'bb')['known'], ['mdk'])
        self.db.execute('DELETE FROM task_current WHERE issue_id IN ("active", "pending")')
        self.db.commit()
        self.assertEqual(status.snapshot(self.home, 'aa'), {'known': []})

    def test_capture_only_owned_pane_read_commands_and_25_lines(self):
        output = ['HERMES_WORKSTREAM_RUN=run\n','%7\t0\n','\n'.join(map(str,range(60)))]
        with patch.object(status.subprocess,'run',side_effect=[Mock(stdout=v) for v in output]) as proc:
            state,text = status.capture(self.run)
        self.assertEqual(state,'live'); self.assertEqual(text.splitlines(),list(map(str,range(35,60))))
        self.assertEqual([c.args[0][3] for c in proc.call_args_list],['show-environment','list-panes','capture-pane'])
        self.assertTrue(all(c.kwargs['timeout'] <= 2 for c in proc.call_args_list))
        with patch.object(status.subprocess,'run',return_value=Mock(stdout='HERMES_WORKSTREAM_RUN=other')) as proc:
            self.assertIn('ownership',status.capture(self.run)[0]); self.assertEqual(proc.call_count,1)

    def test_unicode_bound_with_pane_label(self):
        text = status.render(status.snapshot(self.home,'aa'),('live','界'*10000),'now')
        self.assertLessEqual(len(text.encode()),6000)
        self.assertIn('Pane %7',text); self.assertIn('Truncated',text)

    def test_no_model_construction_or_mutating_dependencies_in_entrypoints(self):
        # Closed import/subprocess surface; dynamic model/plugin loading is forbidden.
        allowed = {'argparse','datetime','json','os','pathlib','re','sqlite3','subprocess','time',
                   'task_progress','task_order','asyncio','hashlib','sys'}
        for module in (status,hook):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    self.assertTrue(all(n.name in allowed for n in node.names))
                if isinstance(node,ast.ImportFrom): self.assertIn(node.module,allowed)
        self.assertNotIn('Store(',Path(status.__file__).read_text())


class HookTest(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_direct_reply_consumes_without_routing(self):
        adapter = Mock(account_id_hex='bb'); adapter.client.send_final=AsyncMock()
        event=dict(text='/status',account_id_hex='bb',sender_account_id_hex='cc',group_id_hex='aa',message_id_hex='dd')
        with patch.object(hook,'response',AsyncMock(return_value='fixture')) as response:
            self.assertTrue(await hook.handle(adapter,event,{'cc'}))
            key=adapter.client.send_final.call_args.kwargs['idempotency_key']
            self.assertTrue(await hook.handle(adapter,event,{'cc'}))
            self.assertEqual(adapter.client.send_final.call_args.kwargs['idempotency_key'],key)
            self.assertTrue(await hook.handle(adapter,dict(event,sender_account_id_hex='ee'),{'cc'}))
            self.assertEqual(response.await_count,2)
            self.assertFalse(await hook.handle(adapter,dict(event,text='ordinary input'),{'cc'}))
            adapter.client.send_final.side_effect=OSError('offline')
            with self.assertRaises(OSError): await hook.handle(adapter,event,{'cc'})

    async def test_process_timeout_kills_and_reaps_without_fallback(self):
        process = Mock(returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError), wait=AsyncMock())
        with patch.object(hook.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            self.assertIn('timed out', await hook.response('aa'))
        process.kill.assert_called_once(); process.wait.assert_awaited_once()
        self.assertEqual(spawn.call_args.args[1:], ('-B','-m','harness.channel_status','--group','aa'))

    async def test_installed_hook_returns_before_gateway_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'plugin').mkdir(); adapter=root/'plugin/adapter.py'
            adapter.write_text('class Adapter:\n    async def dispatch(self,event):\n'+ANCHOR+'            raise AssertionError("model fallback")\n')
            apply(plan(adapter),root/'private-backup')
            self.assertEqual(plan(adapter),{'files':[],'creates':[]})
            namespace={'Path':Path,'__file__':str(adapter),'resolve_allowed_message_senders':lambda:{'cc'}}
            exec(adapter.read_text(),namespace)
            obj=namespace['Adapter'](); obj.account_id_hex='bb'; obj.client=Mock(send_final=AsyncMock())
            with patch.object(hook,'response',AsyncMock(return_value='fixture')):
                await obj.dispatch(dict(text='/status',account_id_hex='bb',sender_account_id_hex='cc',group_id_hex='aa',message_id_hex='dd'))
            obj.client.send_final.assert_awaited_once()
            adapter.write_text(adapter.read_text().replace('status_module.handle','broken.handle'))
            with self.assertRaises(ValueError): plan(adapter)
