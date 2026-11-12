"""Camera recovery journal checks with temporary files and a fake transport."""
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
from md_mcp_plugin.journal import WORLD_CAMERA, WORLD_CAMERA_FAILURE, WORLD_CAMERA_OBSERVED, WORLD_CAMERA_RECOVERY

PARENT, CHILD, TOKEN = 'a' * 32, 'b' * 32, 'c' * 64


def observation():
    return {'operation_id': CHILD, 'status': 'SUCCEEDED', 'effects_started': False, 'requires_recovery': False,
            'result': {'verified': True, 'scene_token': TOKEN, 'view': 'left', 'current_camera_observed': True,
                       'recovered_operation_id': PARENT, 'recovery_action': WORLD_CAMERA_OBSERVED}, 'error': None}


class FakeTransport:
    def __init__(self, receipt, parent_error):
        self.receipt, self.parent_error = receipt, parent_error
        self.calls = []

    def execute(self, operation, params, **kwargs):
        self.calls.append((operation, copy.deepcopy(params), kwargs))
        if operation == WORLD_CAMERA:
            return {'operation_id': PARENT, 'status': 'UNKNOWN', 'requires_recovery': True, 'effects_started': True,
                    'result': {'verified': False}, 'error': {'code': self.parent_error}}
        if operation == WORLD_CAMERA_RECOVERY:
            return copy.deepcopy(self.receipt)
        raise AssertionError(f'Unexpected call {operation}')


class CameraRecoveryTests(unittest.TestCase):
    def context_with_parent(self, directory, receipt=None, parent_error=WORLD_CAMERA_FAILURE, fit=False, view='left'):
        transport = FakeTransport(observation() if receipt is None else receipt, parent_error)
        context = Context(Config('127.0.0.1', 1, 1, Path(directory), ROOT / 'src'), transport)
        params = {'view': view, 'fit_garment': True} if fit else {'view': view}
        original = context.call(WORLD_CAMERA, params, mutation=True, request_id=PARENT, scene_token=TOKEN)
        self.assertEqual(original['status'], 'UNKNOWN')
        return context, transport, original

    def test_verified_observation_releases_only_original_lease_without_replaying_camera(self):
        with tempfile.TemporaryDirectory(prefix='md-camera-reconcile-') as directory:
            context, transport, original = self.context_with_parent(directory)
            completed = context.reconcile_world_camera(PARENT, CHILD, TOKEN)
            self.assertEqual(completed['status'], 'SUCCEEDED')
            self.assertFalse(context.journal.lease.exists())
            parent = context.operation_status(PARENT)
            self.assertEqual(parent.pop('recovery'), {'request_id': CHILD, 'status': WORLD_CAMERA_OBSERVED,
                                                     'original_status': 'UNKNOWN'})
            self.assertEqual(parent, original)
            self.assertEqual(context.reconcile_world_camera(PARENT, CHILD, TOKEN), completed)
            self.assertEqual(len(transport.calls), 2)
            self.assertEqual(transport.calls[-1], (WORLD_CAMERA_RECOVERY, {'blocked_request_id': PARENT,
                'scene_token': TOKEN, 'view': 'left'}, {'operation_id': CHILD, 'scene_token': TOKEN}))

    def test_wrong_original_error_scene_or_lease_never_dispatches(self):
        with tempfile.TemporaryDirectory(prefix='md-camera-reconcile-') as directory:
            context, transport, _ = self.context_with_parent(directory, parent_error='TRANSPORT_ERROR')
            self.assertEqual(context.reconcile_world_camera(PARENT, CHILD, TOKEN)['error']['code'], 'RECOVERY_NOT_APPLICABLE')
            self.assertEqual(len(transport.calls), 1)
        with tempfile.TemporaryDirectory(prefix='md-camera-reconcile-') as directory:
            context, transport, _ = self.context_with_parent(directory)
            self.assertEqual(context.reconcile_world_camera(PARENT, CHILD, 'd' * 64)['error']['code'], 'RECOVERY_NOT_APPLICABLE')
            lease = json.loads(context.journal.lease.read_text(encoding='utf8'))
            lease['operation'] = 'project.open'
            context.journal.lease.write_text(json.dumps(lease), encoding='utf8')
            self.assertEqual(context.reconcile_world_camera(PARENT, CHILD, TOKEN)['error']['code'], 'RECOVERY_LEASE_MISMATCH')
            self.assertEqual(len(transport.calls), 1)

    def test_unbound_success_retains_original_record_and_raw_observation(self):
        variants = [('child_operation_id', 'operation_id', PARENT), ('requires_recovery_false', 'requires_recovery', True),
                    ('verified', 'result.verified', 1), ('current_scene_token', 'result.scene_token', 'd' * 64),
                    ('view', 'result.view', 'front'), ('current_camera_observed', 'result.current_camera_observed', False),
                    ('recovered_operation_id', 'result.recovered_operation_id', CHILD), ('recovery_action', 'result.recovery_action', 'OTHER')]
        for check, field, value in variants:
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix='md-camera-reconcile-') as directory:
                receipt = observation()
                if field.startswith('result.'):
                    receipt['result'][field.split('.', 1)[1]] = value
                else:
                    receipt[field] = value
                context, _, original = self.context_with_parent(directory, receipt)
                lease = context.journal.lease.read_bytes()
                answer = context.reconcile_world_camera(PARENT, CHILD, TOKEN)
                self.assertEqual(answer['status'], 'UNKNOWN')
                self.assertIn(check, answer['error']['failed_checks'])
                self.assertEqual(answer['receipt'], receipt)
                self.assertEqual(context.journal.get(PARENT), original)
                self.assertEqual(context.journal.lease.read_bytes(), lease)

    def test_fit_recovery_binds_option_and_does_not_claim_requested_framing(self):
        for returned_fit in (True, False):
            with self.subTest(returned_fit=returned_fit), tempfile.TemporaryDirectory(prefix='md-camera-fit-') as directory:
                receipt = observation()
                receipt['result'].update(fit_garment=returned_fit, requested_framing_verified=False)
                context, transport, _ = self.context_with_parent(directory, receipt=receipt, fit=True)
                outcome = context.reconcile_world_camera(PARENT, CHILD, TOKEN)
                self.assertEqual(transport.calls[-1][1]['fit_garment'], True)
                self.assertEqual(outcome['status'], 'SUCCEEDED' if returned_fit else 'UNKNOWN')
                self.assertEqual(context.journal.lease.exists(), not returned_fit)

    def test_quarter_fit_recovery_clears_the_original_lease(self):
        with tempfile.TemporaryDirectory(prefix='md-camera-quarter-') as directory:
            receipt = observation()
            receipt['result'].update(view='quarter', fit_garment=True, requested_framing_verified=False)
            context, transport, _ = self.context_with_parent(directory, receipt=receipt, fit=True, view='quarter')
            outcome = context.reconcile_world_camera(PARENT, CHILD, TOKEN)
            self.assertEqual(outcome['status'], 'SUCCEEDED', outcome)
            self.assertEqual(transport.calls[-1][1], {'view': 'quarter', 'fit_garment': True,
                                                     'blocked_request_id': PARENT, 'scene_token': TOKEN})
            self.assertFalse(context.journal.lease.exists())


if __name__ == '__main__':
    unittest.main()
