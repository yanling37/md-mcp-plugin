"""Verify the closed drag recovery without calling Marvelous Designer."""
import base64
import copy
import importlib
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from md_mcp_plugin.config import Config
from md_mcp_plugin.context import Context
from md_mcp_plugin.embedded import curve_release_recovery as recovery, curves
from md_mcp_plugin.errors import PluginError
from md_mcp_plugin.journal import encoded

PARENT = 'a' * 32
CHILD = 'b' * 32
PRE_TOKEN = '1' * 64
CURRENT_TOKEN = '2' * 64
SOURCE_HASH = '3' * 64
TARGET = [3.25, 2.0]
FINGERPRINTS = {'scene_fingerprint': '4' * 64, 'geometry_fingerprint': '5' * 64,
                'topology_fingerprint': '6' * 64, 'scope': '7' * 64}


def reference(fingerprints):
    identity = {'version': 2, 'pattern_uuid': 'pattern', 'shape_kind': 'outer', 'shape_key': None,
                'line_key': 'line', 'handle_key': 'handle', 'role': 'start', **fingerprints}
    return 'mdcurve2:' + base64.urlsafe_b64encode(encoded(identity).encode()).decode().rstrip('=')


def original_request():
    return {'pattern_uuid': 'pattern', 'target_xy_mm': TARGET[:], 'tolerance_mm': .002,
            'handle_ref': reference({**FINGERPRINTS, 'scene_fingerprint': '8' * 64, 'geometry_fingerprint': '9' * 64})}


def original_receipt():
    start = [2.5, 1.0]
    def probe(operation, image=None):
        return {'operation': operation, 'status': 'SUCCEEDED', 'error': None, 'event_calls': 0, 'events': [],
                'paired_release_verified': False, 'requires_recovery': False, 'snap_settings': None, 'image': image}

    events = []
    for stage, button, buttons, modifiers, point in (('hover', 0, 0, 0, [5, 5]), ('press', 1, 1, 0, [5, 5]),
            ('move', 0, 1, 134217728, [9, 9]), ('release', 1, 0, 134217728, [9, 9])):
        events.append({'stage': stage, 'sent': True, 'returned': True, 'accepted': True, 'send_return': True,
                       'button': button, 'buttons': buttons, 'modifiers': modifiers,
                       'scene': point[:], 'viewport_logical': point[:], 'screen': point[:]})
    trace = [probe('inspect'), probe('capture', {'sha256': '0' * 64}),
             {'operation': 'native_readback', 'stage': 'after_initial_capture', 'actual_xy_mm': start,
              'target_error_mm': math.dist(start, TARGET), 'fixed_endpoints_and_other_semantics_preserved': True},
             {'operation': 'initial_semantic_projection', 'anchor_max_error_px': .25, 'target_marker_error_px': .2,
              'missing_anchor_ids': []},
             {'operation': 'bezier_drag', 'status': 'PARTIAL', 'error': {'code': 'COOPERATIVE_BUDGET_EXCEEDED'},
              'event_calls': 4, 'requires_recovery': True, 'paired_release_verified': True, 'events': events}]
    provider = {'status': 'PARTIAL', 'effects_started': True, 'geometry_effects_started': True, 'requires_recovery': True,
                'closed_input_batch_verified': False, 'error': {'code': 'CURVE_BEZIER_DRAG_FAILED'}, 'trace': trace}
    verification = {'verified': True, 'target_reached': True, 'handle_identity_preserved': True,
                    'fixed_endpoints_and_other_semantics_preserved': True, 'scene_identity_preserved': True,
                    'scene_unchanged': False, 'target_error_mm': 0.0, 'actual_xy_mm': TARGET[:],
                    'fixed_endpoints_and_other_native_data_unchanged': True}
    handle = {'handle_ref': reference(FINGERPRINTS), 'xy_mm': TARGET[:], 'independently_addressable': True}
    readback = {'pattern_uuid': 'pattern', 'unit': 'mm', 'native_endpoint_binding': {'verified': True},
                'curves': [{'handles': [handle]}], **FINGERPRINTS}
    return {'operation_id': PARENT, 'status': 'PARTIAL', 'effects_started': True, 'requires_recovery': True,
            'error': {'code': 'HANDLE_PROVIDER_RECOVERY_REQUIRED'},
            'result': {'provider': 'fresh_native_2d_handle_provider', 'provider_called': True, 'write_attempted': True,
                       'requires_recovery': True, 'provider_error': None, 'automatic_retry_allowed': False,
                       'provider_receipt': provider, 'verification': verification, 'readback': readback}}


def replacement():
    return {'operation_id': CHILD, 'status': 'SUCCEEDED', 'requires_recovery': False, 'effects_started': True, 'error': None,
            'result': {'verified': True, 'native_scene_unchanged': True, 'old_bridge_retired': True,
                       'successor_bridge_verified': True, 'recovered_operation_id': PARENT, 'recovery_action': recovery.REPLACED,
                       'source_sha256': SOURCE_HASH, 'scene_token': CURRENT_TOKEN, 'target_xy_mm': TARGET[:],
                       'native_fingerprints': FINGERPRINTS.copy(), 'native_successor_epoch': 1,
                       'process_identity': {'pid': 21, 'creation_filetime': 133},
                       'predecessor_bridge_sha256': 'a' * 64, 'successor_bridge_sha256': 'b' * 64}}


class FakeTransport:
    def __init__(self):
        self.operations = []
        self.reply = replacement()
        self.dropped = False

    def execute(self, operation, params, **kwargs):
        self.operations.append(operation)
        if operation == recovery.RECOVERY and params == {'blocked_request_id': PARENT, 'expected_source_sha256': SOURCE_HASH}:
            if self.dropped:
                raise PluginError('LISTENER_TIMEOUT', 'Timed out', outcome='unknown')
            return copy.deepcopy(self.reply)
        if operation == 'runtime.receipt' and params == {'operation_id': CHILD}:
            return {'status': 'SUCCEEDED', 'result': {'found': True, 'receipt': copy.deepcopy(self.reply)}}
        raise AssertionError(f'Unexpected transport operation {operation}')


class ClosedDragRecoveryTests(unittest.TestCase):
    def test_embedded_reload_accepts_the_already_loaded_older_curves_module(self):
        old_reference_parser = curves._reference_identity
        del curves._reference_identity
        try:
            embedded = importlib.reload(recovery)
            self.assertEqual(embedded.released_curve_drag_proof(original_receipt(), PARENT)['target_xy_mm'], TARGET)
        finally:
            curves._reference_identity = old_reference_parser

    def test_proof_rejects_cleanup_release_and_other_late_failures(self):
        self.assertEqual(recovery.released_curve_drag_proof(original_receipt(), PARENT,
                         original_params=original_request())['target_xy_mm'], TARGET)
        variants = [{'operation': 'zoom'}, {'requires_recovery': 1}, {'event_calls': True},
                    {'error': {'code': 'POST_OPERATION_SCOPE_CHANGED'}}]
        for patch in variants:
            with self.subTest(patch=patch):
                receipt = original_receipt()
                receipt['result']['provider_receipt']['trace'][-1].update(patch)
                with self.assertRaises(ValueError):
                    recovery.released_curve_drag_proof(receipt, PARENT)
        receipt = original_receipt()
        receipt['result']['provider_receipt']['trace'][-1]['events'][-1]['stage'] = 'cleanup_release'
        with self.assertRaises(ValueError):
            recovery.released_curve_drag_proof(receipt, PARENT)

    def setup_context(self, directory):
        transport = FakeTransport()
        context = Context(Config('127.0.0.1', 1, 1, Path(directory), ROOT / 'src'), transport)
        context.journal.claim(PARENT, recovery.WRITE, original_request(), PRE_TOKEN)
        parent = context.journal.finish(PARENT, original_receipt())
        return context, transport, parent

    def recover(self, context, request_id=CHILD):
        return context.reconcile_released_curve_drag(PARENT, request_id, CURRENT_TOKEN, SOURCE_HASH)

    def test_dropped_reply_and_replay_keep_original_receipt_and_exact_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            context, transport, original = self.setup_context(directory)
            transport.dropped = True
            self.assertEqual(self.recover(context)['status'], 'UNKNOWN')
            self.assertTrue(context.journal._matching_lease(PARENT, recovery.WRITE))
            self.assertEqual(self.recover(context, 'd' * 32)['error']['code'], 'RECOVERY_PENDING')
            recovered = context.operation_status(CHILD)
            self.assertEqual(recovered['status'], 'SUCCEEDED')
            self.assertEqual(self.recover(context), recovered)
            self.assertEqual(context.operation_status(CHILD), recovered)
            self.assertEqual(transport.operations, [recovery.RECOVERY, 'runtime.receipt'])
            self.assertFalse(context.journal.lease.exists())
            parent = context.journal.get(PARENT)
            self.assertEqual(parent.pop('curve_release_recovery_request_id'), CHILD)
            self.assertEqual(parent.pop('recovery'), {'request_id': CHILD, 'status': recovery.REPLACED, 'original_status': 'PARTIAL'})
            self.assertEqual(parent, original)

    def test_altered_original_request_or_invalid_successor_cannot_release(self):
        with tempfile.TemporaryDirectory() as directory:
            context, transport, original = self.setup_context(directory)
            invalid = {**original, 'params': {**original['params'], 'target_xy_mm': [13.0, 14.0]}}
            context.journal._write_record(PARENT, invalid)
            self.assertEqual(self.recover(context)['error']['code'], 'RECOVERY_NOT_APPLICABLE')
            self.assertEqual(transport.operations, [])
            context.journal._write_record(PARENT, original)
            transport.reply['result']['source_sha256'] = '0' * 64
            self.assertEqual(self.recover(context)['error']['code'], 'CURVE_RELEASE_RECOVERY_RECEIPT_NOT_VERIFIED')
            self.assertTrue(context.journal._matching_lease(PARENT, recovery.WRITE))
            self.assertNotIn('recovery', context.journal.get(PARENT))


if __name__ == '__main__':
    unittest.main()
