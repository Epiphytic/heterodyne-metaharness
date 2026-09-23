"""Opt-in native logging regression; no live gateway or real-home writes."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class GatewayLoggingContractTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HERMES_RUNTIME_PYTHON') and os.environ.get('HERMES_RUNTIME_SOURCE'),
                         'requires explicit native Hermes runtime and source')
    def test_restarted_process_attaches_gateway_handler_after_cli_setup(self):
        program = '''
import logging, sys, time
from pathlib import Path
from hermes_logging import setup_logging
home = Path(sys.argv[1])
setup_logging(hermes_home=home)
setup_logging(hermes_home=home, mode="gateway")
logging.getLogger("gateway.run").info("gateway-log-regression-%s", sys.argv[2])
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    path = home / "logs/gateway.log"
    if path.exists() and "gateway-log-regression-" + sys.argv[2] in path.read_text():
        break
    time.sleep(0.05)
else:
    raise SystemExit("gateway record missing within five seconds")
'''
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HERMES_HOME=directory, PYTHONPATH=os.environ['HERMES_RUNTIME_SOURCE'])
            for name in ('HERMES_WORKSTREAM_RUN', 'HERMES_WORKSTREAM_ROLE', 'BTQ_SESSION_ID'):
                env.pop(name, None)
            for generation in ('before', 'after-restart'):
                result = subprocess.run([os.environ['HERMES_RUNTIME_PYTHON'], '-c', program,
                                         directory, generation], env=env, cwd=directory,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('gateway-log-regression-after-restart',
                          (Path(directory) / 'logs/gateway.log').read_text())
