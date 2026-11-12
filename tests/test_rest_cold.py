"""The durable lease spans asynchronous native initialization and observation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from md_mcp_plugin.config import Config
from md_mcp_plugin.context import Context, REST_COLD_START
from md_mcp_plugin.errors import PluginError


class ColdRestTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.transport = Mock()
        self.config = Config('127.0.0.1', 7421, 30, Path(directory.name), Path(__file__).resolve().parents[1] / 'src')
        self.context = Context(self.config, self.transport)
        self.id = 'a' * 32
        self.token = 'b' * 64
        self.params = {'scene_token': self.token, 'scratch_project_sha256': 'c' * 64}

    def begin(self):
        return self.context.call(REST_COLD_START, self.params, mutation=True, request_id=self.id, scene_token=self.token)

    def test_pending_holds_lease_until_another_client_observes_settlement(self):
        pending = {'status': 'PENDING', 'result': {'native': {'status': 'PENDING'}}, 'effects_started': True}
        terminal = {'status': 'SUCCEEDED', 'result': {'native': {'status': 'INITIALIZED'}}, 'effects_started': True}
        self.transport.execute.side_effect = [pending, pending, terminal]
        self.assertEqual(self.begin()['status'], 'PENDING')
        self.assertEqual(self.begin()['status'], 'PENDING')
        other = Context(self.config, self.transport)
        self.assertEqual(other.operation_status(self.id)['status'], 'PENDING')
        self.assertTrue(other.journal.lease.is_file())
        self.assertEqual(other.operation_status(self.id)['status'], 'SUCCEEDED')
        self.assertFalse(other.journal.lease.exists())
        self.assertEqual(self.begin()['status'], 'SUCCEEDED')
        self.assertEqual([call.args[0] for call in self.transport.execute.call_args_list],
                         [REST_COLD_START, 'rest_cold.poll', 'rest_cold.poll'])

    def test_lost_begin_receipt_is_observed_without_redispatch(self):
        terminal = {'status': 'SUCCEEDED', 'result': {'native': {'status': 'INITIALIZED'}}, 'effects_started': True}
        self.transport.execute.side_effect = [PluginError('LOST', 'Receipt lost', outcome='unknown'), terminal]
        self.assertEqual(self.begin()['status'], 'UNKNOWN')
        self.assertTrue(self.context.journal.lease.is_file())
        self.assertEqual(self.context.operation_status(self.id)['status'], 'SUCCEEDED')
        self.assertFalse(self.context.journal.lease.exists())
        self.assertEqual([call.args[0] for call in self.transport.execute.call_args_list], [REST_COLD_START, 'rest_cold.poll'])
