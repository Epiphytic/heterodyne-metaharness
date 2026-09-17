import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness.native_hook import enrichment


class ContextTest(unittest.TestCase):
    def test_enrichment_can_import_from_worker_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                context = enrichment(directory, {'agent': 'command'})
            finally:
                os.chdir(previous)
            self.assertNotIn('unavailable', context)

    def test_context_timeout_preserves_identity_recovery_guidance(self):
        with patch('harness.native_hook.subprocess.run', side_effect=subprocess.TimeoutExpired('context', 10)):
            self.assertIn('checkpoint', enrichment('/tmp', {}))
