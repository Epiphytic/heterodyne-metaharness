import json
import unittest

from harness.review_schedule import advance, suspended


class ScheduleTests(unittest.TestCase):
    def test_absolute_deadlines_and_duplicate_ticks(self):
        state = {}
        self.assertIsNone(advance(state, 0, False))
        for due in (600, 1800, 5400, 9000, 12600):
            self.assertIsNone(advance(state, due - 1, False))
            self.assertEqual(advance(state, due, False), due)
            self.assertIsNone(advance(state, due, False))

    def test_restart_missed_slots_one_review_and_backward_clock(self):
        state = {}
        advance(state, 100, False)
        state = json.loads(json.dumps(state))
        self.assertEqual(advance(state, 10100, False), 600)
        self.assertIsNone(advance(state, 10100, False))
        self.assertEqual(state['due'], 12600)
        self.assertIsNone(advance(state, 9000, False))
        self.assertEqual(state['observed_at'], 10100)

    def test_suspend_resume_keeps_remaining_active_time(self):
        state = {}
        advance(state, 0, False)
        advance(state, 300, True)
        state = json.loads(json.dumps(state))
        self.assertIsNone(advance(state, 5000, False))
        self.assertIsNone(advance(state, 5299, False))
        self.assertEqual(advance(state, 5300, False), 600)

    def test_native_activity_releases_ask_but_pid_does_not(self):
        run = {'native_turn_state': 'idle', 'state': 'working', 'pid': 1}
        self.assertTrue(suspended(run, True))
        self.assertFalse(suspended(run, True, other_work=True))
        run['native_turn_state'] = 'working'
        self.assertFalse(suspended(run, True))
        run['native_turn_state'] = 'awaiting_approval'
        self.assertTrue(suspended(run, False))
