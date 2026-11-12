"""Closed world rotation observation and parent lease tests with no MD host."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import dispatch, mesh, transforms, world_rotation_reconciliation as reconcile
from md_mcp_plugin.errors import PluginError
from md_mcp_plugin.journal import Journal, WORLD_ROTATION, WORLD_ROTATION_OBSERVED, WORLD_ROTATION_RECONCILE


TOKEN = 'a' * 64
PARENT, CHILD, COMPETITOR = 'b' * 32, 'c' * 32, 'd' * 32
IDENTITY = {'pid': 77, 'creation_filetime': 1234, 'project_path': 'D:/sample/rotation.zprj'}
TARGET, NAME = 'Own123', 'front'
POSE = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nvn 0 0 1\ng front\nf 1/1/1 2/2/1 3/3/1\n'
ORIGINAL = {'pattern_uuid': TARGET, 'scene_token': TOKEN, 'axis_world': [0, 0, 1], 'angle_degrees': 90,
            'pivot_world_mm': [0, 0, 0], 'tolerance_mm': .002,
            'ring_plan': {'start': [5, 5], 'activation': [8, 9], 'end': [12, 13]}}
PARAMS = {'blocked_request_id': PARENT, 'scene_token': TOKEN, 'original_params': ORIGINAL}
FINGERPRINT = reconcile.digest({'operation': WORLD_ROTATION, 'params': ORIGINAL, 'scene_token': TOKEN})


def scene_fixture():
    return {'identity': IDENTITY, 'scene_token': TOKEN, 'patterns': [
        {'index': 0, 'pattern_uuid': TARGET, 'information': {'uuid': TARGET, 'name': NAME}}]}


def snapshot(payload, capture_id, asset):
    arrays = mesh.parse_obj(payload)
    result = dict(arrays, schema='md-exported-obj-mesh-v1', scope='scene_garment_OBJ_export', pattern_uuid=None,
                  capture_id=capture_id, scene_token=TOKEN, scene_identity=IDENTITY, space='world', unit='mm',
                  axis_order=['X', 'Y', 'Z'], group_inventory=mesh.group_inventory(arrays), export={'obj_asset': asset})
    result['snapshot_id'] = mesh._hash(result)
    return result


def held(baseline):
    evidence = {'schema': reconcile.SCHEMA, 'parent_request_id': PARENT, 'parent_fingerprint': FINGERPRINT,
                'scene_token': TOKEN, 'scene_identity': IDENTITY, 'pattern_uuid': TARGET, 'group_name': NAME,
                'native_request': {'operation': 'gizmo_drag', 'purpose': 'rotation_ring', **ORIGINAL['ring_plan']},
                'native': {'status': 'SUCCEEDED', 'purpose': 'rotation_ring', 'dispatch_completed': True,
                           'paired_release_verified': True, 'press_outstanding': False},
                'before_mesh': reconcile.world._mesh_summary(baseline), 'before_world_arrays_sha256': reconcile._arrays(baseline),
                'closed_terminal_arrays_sha256': None, 'companion_preflight': None, 'refresh_outcome': None,
                'selection': {'pattern_uuid': TARGET}}
    return {'operation_id': PARENT, 'status': 'UNKNOWN', 'effects_started': True, 'requires_recovery': True,
            'error': {'code': 'POST_READ_FAILED'}, 'result': {'rotation_recovery': evidence}}


class RotationObservationTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = directory / 'before.obj'
        self.path.write_bytes(POSE)
        self.asset = mesh._asset(self.path)[1]
        self.baseline = snapshot(POSE, '1' * 32, self.asset)
        self.prior = held(self.baseline)
        self.evidence = self.prior['result']['rotation_recovery']
        self.state = {'blocked': True, 'process': IDENTITY}
        self.bind_hold()
        self.enterContext(patch.dict(dispatch._RECEIPTS, {PARENT: self.prior}, clear=True))
        self.enterContext(patch.dict(dispatch._PAYLOADS, {PARENT: FINGERPRINT}, clear=True))
        self.enterContext(patch.object(reconcile, 'check_scene_token', return_value=scene_fixture()))
        self.enterContext(patch.object(reconcile, '_closed_views'))
        self.enterContext(patch.object(reconcile.world, '_selection', return_value={'pattern_uuid': TARGET}))
        self.enterContext(patch.object(reconcile.world, '_state', return_value=self.state))
        self.native = self.enterContext(patch.object(reconcile.world, '_validate_raw'))
        self.invoke = self.enterContext(patch.object(reconcile.world, '_invoke'))
        self.capture = self.enterContext(patch.object(reconcile.world, '_mesh_read'))

    def bind_hold(self):
        self.state[reconcile.HOLD_KEY] = reconcile.hold_key(self.evidence)

    def provide(self, payload):
        self.capture.side_effect = [snapshot(payload, key * 32, self.asset) for key in ('2', '3')]

    def test_no_geometry_change_observation_clears_only_owned_latch(self):
        self.provide(POSE)
        reply = reconcile.reconcile(PARAMS)
        self.assertEqual(reply['status'], 'SUCCEEDED', reply)
        self.assertEqual(reply['result']['observation'], 'UNCHANGED')
        self.assertEqual(reply['result']['original_mutation_status'], 'UNKNOWN')
        self.assertFalse(reply['effects_started'])
        self.assertFalse(self.state['blocked'])
        self.assertNotIn(reconcile.HOLD_KEY, self.state)
        self.native.assert_called_once()
        self.invoke.assert_not_called()
        self.assertEqual(reconcile.reconcile(PARAMS)['status'], 'REJECTED')

    def test_unrecorded_mismatch_and_changed_baseline_preserve_latch(self):
        moved = POSE.replace(b'v 1 0 0', b'v 2 0 0')
        self.provide(moved)
        reply = reconcile.reconcile(PARAMS)
        self.assertEqual(reply['status'], 'REJECTED')
        self.assertIn('previously closed', reply['error']['message'])
        self.assertTrue(self.state['blocked'])
        self.path.write_bytes(moved)
        self.provide(POSE)
        reply = reconcile.reconcile(PARAMS)
        self.assertIn('asset changed', reply['error']['message'])
        self.assertTrue(self.state['blocked'])
        self.invoke.assert_not_called()

    def test_unknown_topstitch_setter_cannot_clear_latch_even_with_exact_pose(self):
        self.evidence['companion_preflight'] = {'assignment': {}, 'original_offset_sdk': .25}
        self.evidence['refresh_outcome'] = {'status': 'UNKNOWN', 'effects_started': True, 'requires_recovery': True,
                                            'result': {'sdk_set_calls': 2, 'offset_restored': True}}
        self.bind_hold()
        self.provide(POSE)
        reply = reconcile.reconcile(PARAMS)
        self.assertEqual(reply['status'], 'REJECTED')
        self.assertIn('offset action did not close', reply['error']['message'])
        self.assertTrue(self.state['blocked'])

    def test_original_process_latch_must_match(self):
        self.state[reconcile.HOLD_KEY]['parent_request_id'] = COMPETITOR
        self.provide(POSE)
        self.assertEqual(reconcile.reconcile(PARAMS)['status'], 'REJECTED')
        self.capture.assert_not_called()
        self.assertTrue(self.state['blocked'])

    def test_closed_drag_retains_baseline_when_post_export_fails(self):
        from test_world_adapter import FP, VIEW, native_report

        self.state['blocked'] = False
        self.state.pop(reconcile.HOLD_KEY)
        self.capture.side_effect = [self.baseline, ValueError('temporary OBJ read failed')]
        self.invoke.side_effect = lambda req: {'status': 'SUCCEEDED', 'effects_started': True,
                                               'requires_recovery': False, 'result': native_report(req)}
        matrix = transforms.rotation_matrix(ORIGINAL['axis_world'], ORIGINAL['angle_degrees'], ORIGINAL['pivot_world_mm'])
        params = {'pattern_uuid': TARGET, 'group_name': NAME, 'scene_token': TOKEN,
                  'view_ref': reconcile.world._encode_ref(VIEW, TOKEN), 'view_fingerprint': FP,
                  'purpose': 'rotation_ring', 'expected_matrix_world_mm': matrix, **ORIGINAL['ring_plan']}
        prefix = {'parent_request_id': PARENT, 'parent_fingerprint': FINGERPRINT, 'scene_token': TOKEN,
                  'companion_preflight': None}
        with patch.object(reconcile.world, 'scene', return_value=scene_fixture()):
            reply = reconcile.world._execute('gizmo_drag', params,
                                             expected_world_arrays_sha256=reconcile._arrays(self.baseline),
                                             rotation_recovery=prefix)
        self.assertEqual(reply['status'], 'UNKNOWN', reply)
        evidence = reply['result']['rotation_recovery']
        self.assertEqual(evidence['before_mesh']['export']['obj_asset'], self.asset)
        self.assertTrue(evidence['native']['paired_release_verified'])
        self.assertEqual(self.state[reconcile.HOLD_KEY], reconcile.hold_key(evidence))
        self.assertTrue(self.state['blocked'])


class RotationObservationJournalTests(unittest.TestCase):
    def setUp(self):
        self.journal = Journal(self.enterContext(tempfile.TemporaryDirectory()))
        baseline = snapshot(POSE, '1' * 32, {'sha256': '0' * 64, 'path': 'D:/test/known.obj', 'byte_count': len(POSE)})
        self.prior = held(baseline)
        self.evidence = self.prior['result']['rotation_recovery']
        self.journal.claim(PARENT, WORLD_ROTATION, ORIGINAL, TOKEN)
        self.journal.finish(PARENT, self.prior)
        self.assertTrue(self.journal._matching_lease(PARENT, WORLD_ROTATION))

    def claim(self, request_id):
        return self.journal.claim_recovery(request_id, PARENT, PARAMS, TOKEN, operation=WORLD_ROTATION_RECONCILE)

    def observed(self, request_id):
        matrix = transforms.rotation_matrix(ORIGINAL['axis_world'], ORIGINAL['angle_degrees'], ORIGINAL['pivot_world_mm'])
        proof = dict.fromkeys(('same_scene', 'distinct_captures', 'stable_label_bijection', 'normal_readback_complete',
                               'uv_readback_complete', 'outside_target_export_arrays_exact'), True)
        proof.update(matrix_world_mm=matrix, tolerance_mm=ORIGINAL['tolerance_mm'],
                     exported_geometry_matches_requested_matrix=False,
                     observed=dict.fromkeys(('topology_and_index_maps_exact', 'uv_exact', 'other_export_groups_all_exact'), True))
        return {'status': 'SUCCEEDED', 'operation_id': request_id, 'effects_started': False, 'requires_recovery': False,
                'result': {'verified': True, 'recovery_action': WORLD_ROTATION_OBSERVED, 'recovered_operation_id': PARENT,
                           'original_mutation_status': 'UNKNOWN', 'scene_identity': IDENTITY, 'scene_token': TOKEN,
                           'parent_fingerprint': FINGERPRINT, 'evidence_sha256': reconcile.digest(self.evidence),
                           'native_input_calls': 0, 'sdk_setter_calls': 0, 'capture_ids': ['2' * 32, '3' * 32],
                           'topstitch_original_offset_sdk': None, 'observation': 'UNCHANGED', 'requested_matrix_observed': False,
                           'current_world_arrays_sha256': self.evidence['before_world_arrays_sha256'], 'matrix_acceptance': proof}}

    def test_original_unknown_preserved_and_competing_completion_keeps_new_lease(self):
        self.claim(CHILD)
        self.claim(COMPETITOR)
        self.assertEqual(self.journal.finish(CHILD, self.observed(CHILD))['status'], 'SUCCEEDED')
        parent = self.journal.get(PARENT)
        self.assertEqual(parent['status'], 'UNKNOWN')
        self.assertEqual(parent['receipt'], self.prior)
        next_id = 'e' * 32
        self.journal.claim(next_id, 'next.mutation', {}, TOKEN)
        self.assertEqual(self.journal.finish(COMPETITOR, self.observed(COMPETITOR))['status'], 'NO_OP')
        self.assertEqual(self.journal.finish(CHILD, self.observed(CHILD))['status'], 'SUCCEEDED')
        self.assertTrue(self.journal._matching_lease(next_id, 'next.mutation'))

    def test_tampered_observation_or_different_original_params_never_release(self):
        with self.assertRaises(PluginError):
            self.journal.claim_recovery(CHILD, PARENT, dict(PARAMS, original_params={}), TOKEN,
                                        operation=WORLD_ROTATION_RECONCILE)
        self.claim(CHILD)
        tampered = deepcopy(self.observed(CHILD))
        tampered['result']['sdk_setter_calls'] = 1
        self.assertEqual(self.journal.finish(CHILD, tampered)['status'], 'UNKNOWN')
        self.assertTrue(self.journal._matching_lease(PARENT, WORLD_ROTATION))


if __name__ == '__main__':
    unittest.main()
