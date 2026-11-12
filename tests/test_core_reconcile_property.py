"""Property-command reconciliation against temporary durable records only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from md_mcp_plugin.config import Config
from md_mcp_plugin.context import Context
from md_mcp_plugin.errors import PluginError


PARENT, RECONCILE = 'a'*32, 'c'*32
OPEN = 'rest_visible_contents.open_simulation_properties'
OBSERVE = 'rest_visible_recovery.reconcile_open'


class ReadOnlyReconcileTransport:
    def __init__(self, receipt):
        self.receipt, self.calls = receipt, []

    def execute(self, operation, params, **kwargs):
        self.calls.append((operation, copy.deepcopy(params), kwargs))
        if operation == OPEN:
            raise PluginError('TRANSPORT_ERROR', 'Unknown original command outcome',
                              outcome='unknown', retry_safe=False)
        if operation != OBSERVE:
            raise AssertionError('Reconciliation attempted a different command')
        return copy.deepcopy(self.receipt)


class PropertyReconcileTests(unittest.TestCase):
    def make_context(self, receipt):
        base = ROOT / '.state' / 'property-reconcile-tests'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        transport = ReadOnlyReconcileTransport(receipt)
        context = Context(Config('127.0.0.1', 7421, 1, Path(temporary.name), ROOT/'src'), transport)
        original = context.call(OPEN, {}, mutation=True, request_id=PARENT, scene_token='b'*64)
        self.assertEqual('UNKNOWN', original['status'])
        return context, transport, copy.deepcopy(context.journal.get(PARENT))

    def test_verified_observation_releases_once_without_rewriting_parent_outcome(self):
        context, transport, original = self.make_context(dict(status='SUCCEEDED',
            result=dict(verified=True), effects_started=False, requires_recovery=False, error=None))
        completed = context.reconcile_property_operation(PARENT, RECONCILE, 'd'*64)
        self.assertEqual('SUCCEEDED', completed['status'])
        parent = context.journal.get(PARENT)
        annotation = parent.pop('recovery')
        self.assertEqual(original, parent)  # Original UNKNOWN, result, error, receipt survive.
        self.assertEqual('PROPERTY_COMMAND_OBSERVED', annotation['status'])
        self.assertEqual('UNKNOWN', annotation['original_status'])
        self.assertFalse(context.journal.lease.exists())
        self.assertEqual(completed, context.reconcile_property_operation(PARENT, RECONCILE, 'd'*64))
        replayed_parent = context.call(OPEN, {}, mutation=True, request_id=PARENT, scene_token='b'*64)
        self.assertEqual('UNKNOWN', replayed_parent['status'])
        self.assertEqual([OPEN, OBSERVE], [r[0] for r in transport.calls])
        self.assertEqual(dict(blocked_request_id=PARENT, scene_token='d'*64), transport.calls[1][1])

    def test_failed_or_unverified_observation_keeps_original_lease_and_blocks_new_action(self):
        for status, verified in [('FAILED', True), ('SUCCEEDED', False), ('UNKNOWN', False)]:
            with self.subTest(status=status, verified=verified):
                context, transport, original = self.make_context(dict(status=status,
                    result=dict(verified=verified), error=dict(code='NOT_OBSERVED'),
                    effects_started=False, requires_recovery=status == 'UNKNOWN'))
                before = context.journal._path(PARENT).read_bytes()
                result = context.reconcile_property_operation(PARENT, RECONCILE, 'd'*64)
                self.assertEqual(status, result['status'])
                self.assertEqual(original, context.journal.get(PARENT))
                self.assertEqual(before, context.journal._path(PARENT).read_bytes())
                self.assertEqual(PARENT, json.loads(context.journal.lease.read_text())['request_id'])
                denied = context.call('project.open', {}, mutation=True,
                                      request_id='e'*32, scene_token='f'*64)
                self.assertEqual('MUTATION_BUSY', denied['error']['code'])
                self.assertEqual(result, context.reconcile_property_operation(PARENT, RECONCILE, 'd'*64))
                self.assertEqual([OPEN, OBSERVE], [r[0] for r in transport.calls])


if __name__ == '__main__':
    unittest.main()
