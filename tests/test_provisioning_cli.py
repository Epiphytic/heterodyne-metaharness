"""Config refresh keeps the current supervision policy until a new one is valid."""
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from harness import cli


class OneTickStop:
    def __init__(self):
        self.done = False

    def is_set(self):
        return self.done

    def wait(self, _seconds):
        self.done = True


class ProvisioningCliTest(unittest.TestCase):
    def test_daemon_keeps_ticking_with_last_valid_config_after_reload_error(self):
        store = MagicMock()
        store.root = Path('/tmp/provisioning-cli/workstreams')
        store.runs.return_value = []
        supervisor = MagicMock()
        initial = {'marmot': {}, 'beads': {}}
        supervisor.config = initial
        with (patch.object(cli.threading, 'Event', return_value=OneTickStop()),
              patch.object(cli.threading, 'Thread'),
              patch.object(cli.signal, 'signal'),
              patch.object(cli, 'load_config', side_effect=ValueError('invalid config'))):
            cli.daemon(store, supervisor, initial)
        supervisor.tick.assert_called_once()
        self.assertIs(supervisor.config, initial)

    def test_daemon_reloads_new_config_before_tick(self):
        store = MagicMock()
        store.root = Path('/tmp/provisioning-cli/workstreams')
        store.runs.return_value = []
        supervisor = MagicMock()
        initial = {'marmot': {}, 'beads': {}}
        fresh = {'marmot': {'socket': '/tmp/updated'}, 'beads': {'enabled': False},
                 'provisioning_revision': 'policy-v2'}
        supervisor.config = initial
        with (patch.object(cli.threading, 'Event', return_value=OneTickStop()),
              patch.object(cli.threading, 'Thread'),
              patch.object(cli.signal, 'signal'),
              patch.object(cli, 'load_config', return_value=fresh)):
            cli.daemon(store, supervisor, initial)
        supervisor.tick.assert_called_once()
        self.assertIs(supervisor.config, fresh)
        self.assertEqual(supervisor.config['provisioning_revision'], 'policy-v2')

    def test_delivery_keeps_last_transport_after_reload_error(self):
        store = MagicMock()
        transport = object()
        with (patch.object(cli, 'Store', return_value=store),
              patch.object(cli, 'Marmot', return_value=transport),
              patch.object(cli, 'load_config', side_effect=ValueError('invalid config')),
              patch.object(cli, 'deliver') as deliver):
            cli.delivery_loop('/tmp/provisioning-cli', {'ops_group': 'group'}, OneTickStop())
        deliver.assert_called_once_with(store, transport, ops_group='group')
        store.db.close.assert_called_once()
