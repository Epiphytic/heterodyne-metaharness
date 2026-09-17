import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness.memory import codex_recall, claude_recall, memory_context, recall, repository_key
from install_memory import install_instructions


class MemoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        self.sessions = self.home / 'sessions/2026/09/16'
        self.sessions.mkdir(parents=True)
        repository_key.cache_clear()

    def transcript(self, name, cwd, messages):
        records = [{'type': 'session_meta', 'payload': {'cwd': str(cwd), 'id': name}}]
        records += [{'type': 'event_msg', 'payload': {'type': 'user_message', 'message': message}}
                    for message in messages]
        path = self.sessions / (name + '.jsonl')
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return path

    def test_codex_scopes_and_searches_without_database(self):
        self.transcript('own', self.repo, ['fix marmot routing'])
        self.transcript('other', self.home / 'unrelated', ['marmot secret'])
        rows = codex_recall(self.repo, 'marmot', home=self.home)
        self.assertEqual([row['id'] for row in rows], ['own'])
        self.assertEqual(codex_recall(self.repo, 'absent', home=self.home), [])
        self.assertEqual(list(self.home.glob('*.sqlite')), [])

    def test_codex_ignores_tool_output_symlinks_and_partial_records(self):
        path = self.transcript('own', self.repo, ['safe message'])
        with path.open('a') as stream:
            stream.write(json.dumps({'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'secret'}}) + '\n')
            stream.write('{"partial":')
        (self.sessions / 'link.jsonl').symlink_to(path)
        self.assertEqual(len(codex_recall(self.repo, home=self.home)), 1)
        self.assertEqual(codex_recall(self.repo, 'secret', home=self.home), [])

    def test_worktrees_share_repository_identity(self):
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.email=test@test', '-c', 'user.name=Test',
                        'commit', '--allow-empty', '-qm', 'init'], check=True)
        checkout = self.home / 'checkout'
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'add', '--detach', str(checkout)],
                       check=True, capture_output=True)
        self.transcript('worktree', checkout, ['shared history'])
        self.assertEqual(codex_recall(self.repo, home=self.home)[0]['id'], 'worktree')

    def test_claude_filters_foreign_sessions_and_explicitly_enables_backend(self):
        sessions = {'sessions': [{'id': 'own', 'cwd': str(self.repo)},
                                  {'id': 'foreign', 'cwd': str(self.home / 'other')} ]}
        hits = {'results': [{'session_id': 'foreign', 'snippet': 'secret'},
                            {'session_id': 'own', 'snippet': 'safe'}]}
        results = [subprocess.CompletedProcess([], 0, json.dumps(value)) for value in (sessions, hits)]
        with patch('harness.memory.subprocess.run', side_effect=results) as run, \
                patch('harness.memory.repository_key', side_effect=str):
            self.assertEqual(claude_recall(self.repo, 'phrase'), [hits['results'][1]])
        self.assertEqual(run.call_args.kwargs['env']['SESSION_RECALL_ENABLE_CLAUDE_BACKEND'], '1')

    def test_context_bounded_and_failure_nonblocking(self):
        run = {'repo': str(self.repo), 'agent': 'codex'}
        with patch('harness.memory.recall', return_value=[{'summary': 'x' * 20000}]):
            self.assertLess(len(memory_context(run)), 3000)
        with patch('harness.memory.recall', side_effect=FileNotFoundError):
            self.assertIn('unavailable', memory_context(run))
        self.assertEqual(memory_context({'agent': 'hermes'}), '')
        with self.assertRaises(ValueError):
            recall('codex', None)

    def test_global_instructions_preserved_and_idempotent(self):
        path = self.home / '.codex/AGENTS.md'
        path.parent.mkdir()
        path.write_text('Existing project policy.\n')
        install_instructions(self.home)
        first = path.read_text()
        install_instructions(self.home)
        self.assertEqual(path.read_text(), first)
        self.assertTrue(first.startswith('Existing project policy.'))
        self.assertEqual(first.count('<!-- BEGIN HERMES SESSION RECALL -->'), 1)


if __name__ == '__main__':
    unittest.main()
