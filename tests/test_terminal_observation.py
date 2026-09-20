import json
import unittest
from unittest.mock import Mock
from harness.tmux import Tmux
from harness.agents import Adapter
from harness.terminal_observation import retain, MAX_BYTES
from harness.permission_relay import approval_region


class TerminalObservationTest(unittest.TestCase):
    def test_fifty_lines_without_character_truncation(self):
        lines = [f'{i}: ' + 'x' * 80 for i in range(80)]
        result = Adapter('command').observe({}, '\n'.join(lines))
        self.assertEqual(result['summary'], '\n'.join(lines[-50:]))

    def test_long_prompt_heading_beyond_window_is_retained(self):
        region = ('Would you like to run the following command?\n'
                  + 'reason and command\n' * 300
                  + 'Press enter to confirm or esc to cancel\n')
        result = Adapter('codex').observe({}, 'irrelevant prior output\n' + region)
        self.assertEqual(result['state'], 'awaiting_approval')
        self.assertEqual(result['summary'], region)
        self.assertIsNone(approval_region(region + 'normal output\n' * 5))

    def test_exact_activity_restart_and_identity_change(self):
        run = {'native_session_id': 'a', 'launched_at': 1}
        info = {'pane_id': '%1', 'text': 'Working (1s)\nhello'}
        self.assertIsNone(retain(run, info, 1))
        restored = json.loads(json.dumps(run))
        self.assertFalse(retain(restored, info, 2))
        self.assertEqual(restored['terminal_buffer']['last_changed_at'], 1)
        info['text'] = 'Working (2s)\nhello'
        self.assertTrue(retain(restored, info, 3))
        restored['native_session_id'] = 'b'
        self.assertIsNone(retain(restored, info, 4))
        self.assertEqual(restored['terminal_buffer']['text'], info['text'])

    def test_bounded_buffer_hash_covers_discarded_prefix(self):
        run = {}
        info = {'pane_id': '%1', 'text': 'a' + 'x' * MAX_BYTES}
        retain(run, info, 1)
        self.assertTrue(run['terminal_buffer']['truncated'])
        self.assertEqual(len(run['terminal_buffer']['text']), MAX_BYTES)
        info['text'] = 'b' + 'x' * MAX_BYTES
        self.assertTrue(retain(run, info, 2))

    def test_owned_tmux_requests_extended_scrollback(self):
        tmux = Tmux('fixture')
        tmux._owner = Mock(return_value=True)
        tmux._call = Mock(side_effect=[Mock(stdout='%1\t0\t\t\n'), Mock(stdout='history\n' * 350)])
        info = tmux.inspect({'id': 'run', 'tmux_session': 'owned', 'pane_id': '%1'})
        tmux._call.assert_called_with('capture-pane', '-p', '-t', '%1', '-S', '-2000')
        self.assertEqual(len(info['text'].splitlines()), 350)
