import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from harness.lifecycle import observe_events, READ_LIMIT


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / 'rollout.jsonl'
        self.path.touch()
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?)', ('session-1', str(self.path)))
        self.run = {'agent': 'codex', 'native_session_id': 'session-1',
                    'config': {'codex_home': str(self.home), 'claude_home': str(self.home)}}

    def append(self, record):
        with self.path.open('a') as stream:
            stream.write(json.dumps(record) + '\n')

    def test_codex_explicit_end_and_cursor(self):
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})
        self.append({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': 'not final'}})
        self.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': 'Finished this turn.'}})
        events = observe_events(self.run)
        self.assertEqual([x['kind'] for x in events], ['working', 'turn_completed'])
        self.assertEqual(events[-1]['summary'], 'Finished this turn.')
        self.assertEqual(observe_events(self.run), [])
        self.assertNotIn('state', self.run)

    def test_exact_identity_only(self):
        self.run['native_session_id'] = 'another-session'
        self.append({'type': 'event_msg', 'payload': {'type': 'task_complete'}})
        self.assertEqual(observe_events(self.run), [])

    def test_partial_record_is_retried(self):
        self.path.write_text('{"type":"event_msg",')
        self.assertEqual(observe_events(self.run), [])
        with self.path.open('a') as stream:
            stream.write('"payload":{"type":"task_complete","last_agent_message":"done"}}\n')
        self.assertEqual(observe_events(self.run)[0]['summary'], 'done')

    def test_oversize_record_does_not_stall_or_exceed_read_budget(self):
        self.path.write_text('x' * (READ_LIMIT + 100) + '\n')
        self.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': 'done'}})
        self.assertEqual(observe_events(self.run), [])
        self.assertEqual(self.run['lifecycle_cursor']['offset'], READ_LIMIT)
        self.assertEqual(observe_events(self.run)[0]['kind'], 'turn_completed')

    def test_claude_final_text_and_compaction(self):
        self.run['agent'] = 'claude'
        directory = self.home / 'projects' / 'project'
        directory.mkdir(parents=True)
        self.path = directory / 'session-1.jsonl'
        self.append({'type': 'assistant', 'sessionId': 'session-1', 'message': {'stop_reason': 'tool_use', 'content': [{'type': 'text', 'text': 'Working'}]}})
        self.append({'type': 'system', 'subtype': 'compact_boundary', 'sessionId': 'session-1'})
        self.append({'type': 'assistant', 'sessionId': 'session-1', 'message': {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Final answer'}, {'type': 'tool_result', 'content': 'secret tool output'}]}})
        self.append({'type': 'assistant', 'sessionId': 'session-1', 'isSidechain': True, 'message': {'stop_reason': 'end_turn', 'content': []}})
        self.append({'type': 'assistant', 'sessionId': 'other', 'message': {'stop_reason': 'end_turn', 'content': []}})
        events = observe_events(self.run)
        self.assertEqual([x['kind'] for x in events], ['compacted', 'turn_completed'])
        self.assertEqual(events[-1]['summary'], 'Final answer')
        self.assertEqual(self.run['native_session_id'], 'session-1')

    def test_codex_compaction_and_approval(self):
        self.append({'type': 'compacted', 'payload': {}})
        self.append({'type': 'event_msg', 'payload': {'type': 'exec_approval_request', 'command': 'sensitive'}})
        self.assertEqual([e['kind'] for e in observe_events(self.run)], ['compacted', 'approval'])

    def test_hermes_does_not_invent_completion(self):
        self.run['agent'] = 'hermes'
        self.assertEqual(observe_events(self.run), [])

    def hermes_database(self):
        self.run['agent'] = 'hermes'
        self.run['config']['hermes_home'] = str(self.home)
        db = sqlite3.connect(self.home / 'state.db')
        db.executescript("""
            CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                timestamp REAL, finish_reason TEXT,tool_calls TEXT,_compressed_summary INTEGER);
            CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT,model_config TEXT,source TEXT,ended_at REAL);
            INSERT INTO sessions VALUES('session-1',NULL,NULL,'{}','cli',NULL);
            INSERT INTO messages VALUES(1,'session-1','assistant','working',1,'tool_calls','[]',0);
            INSERT INTO messages VALUES(2,'session-1','assistant','Final reply',2,'stop',NULL,0);
            INSERT INTO messages VALUES(3,'session-1','assistant','not final',3,NULL,NULL,0);
        """)
        db.commit()
        self.addCleanup(db.close)
        return db

    def test_hermes_only_explicit_final(self):
        self.hermes_database()
        events = observe_events(self.run)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['summary'], 'Final reply')
        self.assertEqual(observe_events(self.run), [])

    def test_hermes_follows_compression_not_delegate_and_dedupes_tail(self):
        db = self.hermes_database()
        original = observe_events(self.run)[0]
        db.executescript("""
            UPDATE sessions SET end_reason='compression',ended_at=4 WHERE id='session-1';
            INSERT INTO sessions VALUES('delegate','session-1',NULL,'{"_delegate_from":"session-1"}','cli',NULL);
            INSERT INTO sessions VALUES('session-2','session-1',NULL,'{}','cli',NULL);
            INSERT INTO messages VALUES(4,'session-2','assistant','Final reply',2,'stop',NULL,0);
            INSERT INTO messages VALUES(5,'session-2','assistant','New reply',5,'stop',NULL,0);
        """)
        events = observe_events(self.run)
        self.assertEqual([e['kind'] for e in events], ['compacted'])
        self.assertEqual(self.run['native_session_id'], 'session-2')
        events = observe_events(self.run)
        self.assertEqual([e['summary'] for e in events], ['New reply'])
        self.assertNotEqual(events[0]['id'], original['id'])

    def test_hermes_in_place_compaction_clone_not_forwarded_twice(self):
        db = self.hermes_database()
        observe_events(self.run)
        db.execute("INSERT INTO messages VALUES(4,'session-1','assistant','Final reply',2,'stop',NULL,0)")
        db.commit()
        self.assertEqual(observe_events(self.run), [])

    def test_retry_ids_stable_and_rotation_resets_cursor(self):
        self.append({'type': 'event_msg', 'payload': {'type': 'task_complete'}})
        event = observe_events(self.run)[0]
        self.run.pop('lifecycle_cursor')
        self.assertEqual(observe_events(self.run)[0]['id'], event['id'])
        self.path.unlink()
        self.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})
        self.assertEqual(observe_events(self.run)[0]['kind'], 'working')


if __name__ == '__main__':
    unittest.main()
