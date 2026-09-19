import json
from pathlib import Path
import tempfile
import unittest

from install_operator_asks import plan
from install_brain import apply


class InstallerTest(unittest.TestCase):
    def test_bounded_replacement_backups_and_repeat_noop(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / 'sources'
            root.mkdir()
            babysitter = root / 'babysitter.py'
            original = ('UNRELATED_POLICY = "keep"\n\ndef notify(pinfo, text):\n    return False\n\n'
                        'def escalate(pinfo, text):\n    return notify(pinfo, text)\n')
            babysitter.write_text(original)
            config = root / 'config.json'
            config.write_text(json.dumps({'marmot': {'socket': 'retain'}, 'other': True}))
            prepared = plan(babysitter, config, Path('/reviewed/wn'), Path('/reviewed/home'))
            result = apply(prepared, base / 'quarantine')
            self.assertEqual(result['changed'], 2)
            self.assertIn('UNRELATED_POLICY = "keep"', babysitter.read_text())
            self.assertIn('operator_ask=True', babysitter.read_text())
            self.assertIn('operator_ask=False', babysitter.read_text())
            self.assertEqual(json.loads(config.read_text())['marmot']['socket'], 'retain')
            self.assertEqual(plan(babysitter, config, Path('/reviewed/wn'), Path('/reviewed/home'))['files'], [])
