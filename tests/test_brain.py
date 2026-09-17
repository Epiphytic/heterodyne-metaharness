import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from harness import brain


class BrainTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state.sqlite3'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        brain.initialize(self.db)

    def offer(self, state, session='session', native='native', turn='turn'):
        return brain.offer(self.db, session, native, turn, state)

    def ack(self, notice, session='session', native='native', turn='turn'):
        return brain.acknowledge(self.db, session, native, turn, [
            {'role': 'user', 'api_content': notice['context']},
            {'role': 'assistant', 'content': 'Actual model response'}])

    def known(self, session='session'):
        row = self.db.execute('SELECT snapshot FROM brain_known WHERE session=?', (session,)).fetchone()
        return json.loads(row[0]) if row else None

    def test_initial_inventory_is_one_bounded_notice(self):
        state = {'skills/example/file'+str(i): str(i) for i in range(707)}
        notice = self.offer(state)
        self.assertLess(len(notice['context']), 1000)
        self.assertIn('707', notice['context'])
        self.assertIn('not reading or knowing file contents', notice['context'])
        self.assertIsNone(self.known())
        self.assertTrue(self.ack(notice))
        self.assertEqual(self.known(), state)
        self.assertIsNone(self.offer(state))

    def test_failed_delivery_and_stale_turn_do_not_advance(self):
        notice = self.offer({'SOUL.md': 'a'})
        self.assertFalse(brain.acknowledge(self.db, 'session', 'native', 'turn', []))
        self.assertFalse(self.ack(notice, turn='wrong'))
        self.assertIsNone(self.known())
        self.assertEqual(self.offer({'SOUL.md': 'b'}), notice)
        self.assertTrue(self.ack(notice))
        self.assertEqual(self.known(), {'SOUL.md': 'a'})
        next_notice = self.offer({'SOUL.md': 'b'})
        self.assertNotEqual(next_notice['token'], notice['token'])
        self.assertIn('changed "SOUL.md"', next_notice['context'])

    def test_old_context_cannot_ack_current_unseen_offer(self):
        notice = self.offer({'SOUL.md': 'a'})
        messages = [{'role': 'user', 'api_content': notice['context']},
                    {'role': 'assistant', 'content': 'old'},
                    {'role': 'user', 'content': 'current prompt without notice'},
                    {'role': 'assistant', 'content': 'new'}]
        self.assertFalse(brain.acknowledge(self.db, 'session', 'native', 'turn', messages))

    def test_diff_batches_and_independent_sessions(self):
        first = self.offer({'skills/old': 'x'})
        self.ack(first)
        current = {'skills/new'+str(i): 'v' for i in range(65)}
        offered = self.offer(current)
        self.assertNotIn('Initial inventory', offered['context'])
        self.ack(offered)
        self.assertNotEqual(self.known(), current)
        for _ in range(3):
            notice = self.offer(current)
            if notice:
                self.ack(notice)
        self.assertEqual(self.known(), current)
        separate = self.offer(current, session='other')
        self.assertIn('Initial inventory', separate['context'])
        self.assertIsNone(self.known('other'))

    def test_restart_new_native_keeps_pending_and_known(self):
        notice = self.offer({'spec/brain.md': 'a'})
        self.db.close()
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.assertEqual(self.offer({'spec/brain.md': 'a'}, native='compact'), notice)
        self.assertTrue(self.ack(notice, native='compact'))
        self.assertIsNone(self.offer({'spec/brain.md': 'a'}, native='reboot'))
        self.assertFalse(self.ack(notice))

    def test_concurrent_ack_and_offer_serialize(self):
        notice = self.offer({'SOUL.md': 'a'})
        messages = [{'role': 'user', 'content': notice['context']},
                    {'role': 'assistant', 'content': 'response'}]
        def acknowledge(_):
            with sqlite3.connect(self.path) as db:
                return brain.acknowledge(db, 'session', 'native', 'turn', messages)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(acknowledge, range(2))), [False, True])
        def offer(_):
            with sqlite3.connect(self.path) as db:
                return brain.offer(db, 'session', 'native', 'next', {'SOUL.md': 'b'})
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(offer, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertFalse(self.ack(notice))
        self.assertEqual(self.known(), {'SOUL.md': 'a'})

    def test_scan_permission_error_is_not_deletion(self):
        spec = self.root / 'spec'
        spec.mkdir()
        def failing_walk(root, **kwargs):
            kwargs['onerror'](PermissionError('denied'))
            return iter(())
        with patch('harness.brain.os.walk', side_effect=failing_walk):
            with self.assertRaises(PermissionError):
                brain.scan(self.root, spec)

    def test_scan_hashes_content_without_notice_leak_and_detects_deletion(self):
        spec = self.root / 'spec'
        spec.mkdir()
        (spec / 'README.md').write_text('current spec')
        (self.root / 'config.yaml').write_text('secret: NEVER_SHOW_ME')
        skill = self.root / 'skills/a/b/c/d'
        skill.mkdir(parents=True)
        (skill / 'new.anything').write_text('skill content')
        state = brain.scan(self.root, spec)
        self.assertIn('skills/a/b/c/d/new.anything', state)
        notice = self.offer(state)
        self.assertNotIn('NEVER_SHOW_ME', notice['context'])
        self.ack(notice)
        (skill / 'new.anything').unlink()
        self.assertIn('deleted "skills/a/b/c/d/new.anything"', self.offer(brain.scan(self.root, spec))['context'])
