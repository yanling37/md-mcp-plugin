"""Offline fixed-recipe/selection/OBJ readback checks. No DLL load or MD."""
import copy
import ctypes
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
adapter = importlib.import_module('md_mcp_plugin.embedded.world_adapter')
mesh = importlib.import_module('md_mcp_plugin.embedded.mesh')
feature = importlib.import_module('md_mcp_plugin.features.world_adapter')
TOKEN = 'a' * 64
VIEW = '11111111-2222-3333-4444-555555555555'
FP = 'b' * 64


def current_scene():
    return {'scene_token': TOKEN, 'identity': {'project_path': 'current-copy.zprj', 'pid': 123},
            'patterns': [{'index': 0, 'pattern_uuid': 'uuid-A', 'information': {'name': 'paper-A', 'uuid': 'uuid-A'}},
                         {'index': 1, 'pattern_uuid': 'uuid-B', 'information': {'name': 'paper-B', 'uuid': 'uuid-B'}}]}


def descriptor():
    return {'view': '3d', 'native_view_ref': VIEW, 'view_fingerprint': FP,
            'mapping_fingerprint': 'c' * 64, 'scene_item_grabber_absent': True}


def request(operation='arrow_step'):
    value = {'operation': operation, 'budget_ms': 750, 'native_view_ref': VIEW, 'view_fingerprint': FP}
    if operation == 'arrow_step':
        value['direction'] = 'right'
    elif operation == 'gizmo_drag':
        value.update(start=[100., 100.], activation=[112., 100.], end=[114., 102.], purpose='rotation_ring')
    else:
        value = {'operation': 'inspect', 'budget_ms': 750}
    return value


def native_report(req):
    operation = req['operation']; count = 0 if operation == 'inspect' else 2 if operation == 'arrow_step' else 5
    raw = {'schema': adapter.SCHEMA, 'return_code': 0, 'operation': operation, 'status': 'SUCCEEDED',
           'verified': True, 'effects_started': bool(count), 'requires_recovery': False,
           'event_calls': count, 'scene_mouse_calls': count if operation == 'gizmo_drag' else 0,
           'key_event_calls': count if operation == 'arrow_step' else 0,
           'zero_operation_counts': dict.fromkeys(adapter.ZERO_COUNTS, 0),
           'semantic_geometry_verified': False, 'world_axis_mapping_verified': False, 'world_transform_verified': False,
           'rotation_angle_verified': False, 'native_target_hit_verified': False,
           'paired_release_verified': True, 'press_outstanding': False, 'dispatch_completed': True,
           'physical_state_unchanged': True, 'all_mouse_events_nonspontaneous': True,
           'before': descriptor(), 'after': descriptor(), 'key_events': [], 'mouse_events': []}
    if operation == 'arrow_step':
        raw['key_events'] = [{'stage': s, 'key': adapter.DIRECTIONS[req['direction']], 'modifiers': 0,
                              'auto_repeat': False, 'count': 1, 'sent': True, 'returned': True, 'nonspontaneous': True} for s in ('press', 'release')]
    elif operation == 'gizmo_drag':
        raw.update(purpose=req['purpose'], profile='NoMod_hover_press_activation_move_release_v1')
        for i, (stage, button, buttons) in enumerate((('hover', 0, 0), ('press', 1, 1), ('activation', 0, 1), ('move', 0, 1), ('release', 1, 0))):
            raw['mouse_events'].append({'stage': stage, 'button': button, 'buttons': buttons, 'modifiers': 0,
                                       'viewport_logical': req['start'] if i < 2 else req['activation'] if i == 2 else req['end'],
                                       'sent': True, 'returned': True})
    return raw


def exported_snapshot(delta=(0, 0, 0), other_delta=0):
    points = [[0, 0, 0], [10, 0, 0], [0, 10, 0], [100, 0, 0], [110, 0, 0], [100, 10, 0]]
    for point in points[:3]:
        for axis in range(3): point[axis] += delta[axis]
    points[3][0] += other_delta
    lines = ['v ' + ' '.join(map(str, p)) for p in points]
    lines += ['vt 0 0', 'vt 1 0', 'vt 0 1', 'vn 0 0 1', 'g paper-B', 'f 1/1/1 2/2/1 3/3/1', 'g paper-A', 'f 4/1/1 5/2/1 6/3/1']
    parsed = mesh.parse_obj(('\n'.join(lines) + '\n').encode())
    value = {'schema': 'md-exported-obj-mesh-v1', 'capture_id': str(delta) + str(other_delta), 'pattern_uuid': None,
             'scene_token': TOKEN, 'scene_identity': current_scene()['identity'], 'scope': 'scene_garment_OBJ_export',
             'space': 'world', 'unit': 'mm', 'axis_order': ['X', 'Y', 'Z'],
             'export': {'obj_asset': {'path': 'offline-fixture.obj', 'sha256': '0' * 64}}, **parsed,
             'group_inventory': mesh.group_inventory(parsed)}
    value['snapshot_id'] = mesh._hash(value)
    return value


class WorldAdapterTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(sys, adapter.STATE_KEY, {'configuration': None, 'library': None, 'call': None, 'blocked': False}, create=True))
        self.enterContext(patch.object(adapter, 'scene', side_effect=lambda: copy.deepcopy(current_scene())))
        self.selected = [False, True]
        self.api = SimpleNamespace(GetSelectedPatternViaIndex=lambda i: self.selected[i], GetSelectedPattern=lambda: ['paper-B'])
        self.enterContext(patch.dict(sys.modules, {'pattern_api': self.api}))

    def native_stub(self, transform=None):
        calls = []

        def call(pid, creation, payload, length, output, cap, written, needed):
            req = json.loads(payload); calls.append(req)
            raw = native_report(req)
            if transform: transform(raw)
            data = json.dumps(raw).encode()
            ctypes.memmove(output, data + b'\0', len(data) + 1)
            ctypes.cast(written, ctypes.POINTER(ctypes.c_uint32))[0] = len(data)
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_uint32))[0] = len(data) + 1
            return raw['return_code']

        self.enterContext(patch.object(adapter, '_load', return_value=(call, {'pid': 123, 'creation_filetime': 456})))
        return calls

    def params(self, operation='arrow_step'):
        req = request(operation)
        req.pop('operation'); req.pop('native_view_ref')
        req.update(pattern_uuid='uuid-B', group_name='paper-B', scene_token=TOKEN, view_ref=adapter._encode_ref(VIEW, TOKEN))
        return req

    def test_configuration_does_not_load_and_refs_reject_other_scene(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'md_qt_world_adapter_bridge.dll'; path.write_bytes(b'offline-pin-test')
            with patch.object(adapter.ctypes, 'CDLL', side_effect=AssertionError('No DLL load')):
                value = adapter.install_native_bridge(str(path), hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertFalse(value['loaded'])
                self.assertFalse(adapter.runtime_capabilities({})['loaded'])
        ref = adapter._encode_ref(VIEW, TOKEN)
        self.assertEqual(adapter._decode_ref(ref, TOKEN), VIEW)
        for value, token in ((ref, 'd' * 64), ('0x123456', TOKEN), (adapter._encode_ref('0x123', TOKEN), TOKEN)):
            with self.assertRaises(adapter.AdapterError): adapter._decode_ref(value, token)

    def test_fixed_arrow_receipt_rejects_wrong_key_modifier_count_or_scope(self):
        req = request(); raw = native_report(req)
        adapter._validate_raw(raw, 0, req)
        changes = [lambda x: x['key_events'][0].update(key=0x01000007),
                   lambda x: x['key_events'][1].update(modifiers=1),
                   lambda x: x.update(event_calls=True), lambda x: x.update(physical_state_unchanged=False),
                   lambda x: x.update(world_transform_verified=True), lambda x: x['zero_operation_counts'].update(OS_input_calls=1),
                   lambda x: x['after'].update(mapping_fingerprint='d' * 64)]
        for change in changes:
            altered = copy.deepcopy(raw); change(altered)
            with self.assertRaises(adapter.AdapterError): adapter._validate_raw(altered, 0, req)

    def test_fixed_five_scene_events_reject_alt_wrong_points_and_fake_hit(self):
        req = request('gizmo_drag'); raw = native_report(req)
        adapter._validate_raw(raw, 0, req)
        for change in (lambda x: x['mouse_events'][2].update(modifiers=0x08000000),
                       lambda x: x['mouse_events'][4].update(viewport_logical=[1, 1]),
                       lambda x: x.update(native_target_hit_verified=True), lambda x: x.update(all_mouse_events_nonspontaneous=False)):
            altered = copy.deepcopy(raw); change(altered)
            with self.assertRaises(adapter.AdapterError): adapter._validate_raw(altered, 0, req)

    def test_selection_uses_false_keep_previous_and_verifies_all_indices(self):
        def select(index, keep):
            self.assertIs(keep, False)
            self.selected[:] = [i == index for i in range(2)]
        self.api.SelectPatternViaIndex = Mock(side_effect=select)
        self.selected[:] = [True, True]
        result = adapter.select_pattern({'pattern_uuid': 'uuid-B', 'scene_token': TOKEN})
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.api.SelectPatternViaIndex.assert_called_once_with(1, False)
        self.assertEqual(result['result']['selection']['selected_index_readbacks'], [False, True])
        self.assertFalse(result['result']['keep_previous_selection'])

    def test_real_python_to_ctypes_boundary_and_obj_readback_measure_not_assume(self):
        calls = self.native_stub()
        with patch.object(adapter, '_mesh_read', side_effect=[exported_snapshot(), exported_snapshot((3.5, -1, 2))]):
            result = adapter.OPERATIONS['world_adapter.arrow_step'](self.params())
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'SUCCEEDED')
        measured = result['result']['measurement']
        self.assertEqual(measured['first_vertex_delta_world_mm'], [3.5, -1, 2])
        self.assertTrue(measured['other_export_groups_all_exact'])
        self.assertFalse(measured['world_transform_verified'])
        self.assertFalse(result['result']['requested_pattern_UUID_to_readback_group_verified'])
        self.assertTrue(result['result']['requested_pattern_UUID_to_exact_group_label_verified'])
        self.assertTrue(result['result']['native']['paired_release_verified'])

    def test_collateral_obj_change_blocks_future_input_and_selection(self):
        calls = self.native_stub()
        with patch.object(adapter, '_mesh_read', side_effect=[exported_snapshot(), exported_snapshot((1, 0, 0), other_delta=.01)]):
            result = adapter.OPERATIONS['world_adapter.arrow_step'](self.params())
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertTrue(result['requires_recovery'])
        with patch.object(adapter, 'scene', side_effect=AssertionError('No MD call while blocked')):
            repeat = adapter.OPERATIONS['world_adapter.arrow_step'](self.params())
            selection = adapter.select_pattern({'pattern_uuid': 'uuid-B', 'scene_token': TOKEN})
        self.assertFalse(repeat['effects_started']); self.assertTrue(repeat['requires_recovery'])
        self.assertFalse(selection['effects_started']); self.assertEqual(len(calls), 1)

    def test_invalid_params_or_nonsingleton_stop_before_native(self):
        calls = self.native_stub()
        for change in ({'direction': 'delete'}, {'key': 123}, {'view_ref': '0x123'}, {'budget_ms': True}):
            params = self.params(); params.update(change)
            self.assertEqual(adapter.OPERATIONS['world_adapter.arrow_step'](params)['status'], 'REJECTED')
        self.selected[:] = [True, True]
        with patch.object(adapter, '_mesh_read', return_value=exported_snapshot()):
            result = adapter.OPERATIONS['world_adapter.arrow_step'](self.params())
        self.assertEqual(result['status'], 'REJECTED'); self.assertEqual(calls, [])
        params = self.params('gizmo_drag'); params.update(end=[1000, 1000])
        self.assertEqual(adapter.OPERATIONS['world_adapter.gizmo_drag'](params)['status'], 'REJECTED')

    def test_bad_native_receipt_is_unknown_and_never_retried(self):
        calls = self.native_stub(lambda raw: raw.update(paired_release_verified=False))
        response = adapter._invoke(request())
        self.assertEqual(response['status'], 'UNKNOWN')
        self.assertTrue(response['requires_recovery'])
        self.assertEqual(adapter._invoke(request())['error']['code'], 'RECOVERY_REQUIRED')
        self.assertEqual(len(calls), 1)

    def test_export_math_reports_no_effect_and_uv_or_topology_failure(self):
        before = exported_snapshot()
        no_effect = adapter.compare_exports(before, exported_snapshot(), 'paper-B')
        self.assertEqual(no_effect['changed_vertex_count'], 0)
        self.assertFalse(no_effect['world_transform_verified'])
        changed = exported_snapshot(); changed['faces'][0] = [0, 2, 1]
        changed['topology_fingerprint'] = 'different'; changed.pop('snapshot_id'); changed['snapshot_id'] = mesh._hash(changed)
        self.assertFalse(adapter.compare_exports(before, changed, 'paper-B')['topology_and_index_maps_exact'])

    def test_complete_label_bijection_and_wrong_uuid_group_stop_before_native(self):
        scene = current_scene(); snapshot = exported_snapshot()
        certificate = adapter.bind_export_labels(scene, snapshot, 'uuid-B', 'paper-B')
        self.assertEqual(len(certificate['pairs']), 2)
        self.assertEqual(certificate['target']['SDK_pattern_index'], 1)
        self.assertFalse(certificate['native_owner_mapping_verified'])
        self.assertFalse(certificate['native_pattern_filter_verified'])
        self.assertTrue(certificate['paper_group_export_vertex_indices_disjoint'])
        calls = self.native_stub(); params = self.params(); params['group_name'] = 'paper-A'
        with patch.object(adapter, '_mesh_read', return_value=snapshot):
            result = adapter.OPERATIONS['world_adapter.arrow_step'](params)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(result['error']['code'], 'UUID_GROUP_LABEL_MISMATCH')
        self.assertEqual(calls, [])

    def test_duplicate_missing_multigroup_or_stale_label_certificate_rejects(self):
        scene = current_scene(); original = exported_snapshot()
        duplicate = copy.deepcopy(scene); duplicate['patterns'][0]['information']['name'] = 'paper-B'
        contradictory = copy.deepcopy(scene); contradictory['patterns'][1]['information']['uuid'] = 'other'
        stale = copy.deepcopy(scene); stale['scene_token'] = 'changed'
        for current in (duplicate, contradictory, stale):
            with self.assertRaises(adapter.AdapterError): adapter.bind_export_labels(current, original, 'uuid-B', 'paper-B')
        with patch.object(adapter, 'MAX_LABEL_FACE_WORK', 1), self.assertRaises(adapter.AdapterError):
            adapter.bind_export_labels(scene, original, 'uuid-B', 'paper-B')
        old_false_owner = copy.deepcopy(original)
        old_false_owner.update(scope='pattern_filtered_OBJ_export', pattern_uuid='uuid-B')
        old_false_owner.pop('snapshot_id'); old_false_owner['snapshot_id'] = mesh._hash(old_false_owner)
        with self.assertRaisesRegex(adapter.AdapterError, 'full current-scene export'):
            adapter.bind_export_labels(scene, old_false_owner, 'uuid-B', 'paper-B')
        for change in (lambda x: x['face_labels'][1].update(groups=['unknown']),
                       lambda x: x['face_labels'][0].update(groups=['paper-B', 'also']),
                       lambda x: x['faces'][1].__setitem__(0, 0)):
            altered = copy.deepcopy(original); change(altered)
            altered['group_inventory'] = mesh.group_inventory(altered)
            altered.pop('snapshot_id'); altered['snapshot_id'] = mesh._hash(altered)
            with self.assertRaises(adapter.AdapterError): adapter.bind_export_labels(scene, altered, 'uuid-B', 'paper-B')

    def test_requested_rotation_translation_readback_is_math_only_and_euclidean(self):
        before = exported_snapshot(); after = exported_snapshot()
        after['capture_id'] = 'independent-after-capture'
        after['positions_mm'][:3] = [[10, -20, 5], [10, -10, 5], [0, -20, 5]]
        after.pop('snapshot_id'); after['snapshot_id'] = mesh._hash(after)
        matrix = [[0, -1, 0, 10], [1, 0, 0, -20], [0, 0, 1, 5], [0, 0, 0, 1]]
        result = adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, after, 'uuid-B', 'paper-B', matrix)
        self.assertTrue(result['exported_geometry_matches_requested_matrix'])
        self.assertEqual(result['maximum_vertex_error_mm'], 0)
        self.assertFalse(result['exact_world_writer_verified'])
        self.assertFalse(result['full_scene_invariants_verified'])
        no_effect = adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, before, 'uuid-B', 'paper-B', matrix)
        self.assertFalse(no_effect['exported_geometry_matches_requested_matrix'])
        after['positions_mm'][0][0] += .0019; after['positions_mm'][0][1] += .0019
        after.pop('snapshot_id'); after['snapshot_id'] = mesh._hash(after)
        diagonal = adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, after, 'uuid-B', 'paper-B', matrix)
        self.assertFalse(diagonal['exported_geometry_matches_requested_matrix'])
        self.assertGreater(diagonal['maximum_vertex_error_mm'], .002)
        with self.assertRaises(ValueError):
            adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, before, 'uuid-B', 'paper-B', [[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 1]])

    def test_assigned_group_positions_and_strict_normals_have_independent_proofs(self):
        def snapshot(positions, normal, capture):
            vertices = ['v ' + ' '.join(map(str, point)) for point in positions]
            rows = ['vt 0 0', 'vt 1 0', 'vt 0 1', 'vn 0 0 1', 'vn 0 0 1', f'vn {normal} 0 1',
                    'g paper-B', 'f 1/1/1 2/2/1 3/3/1', 'g paper-A', 'f 4/1/2 5/2/2 6/3/2',
                    'g stitch', 'f 7/1/3 8/2/3 9/3/3']
            parsed = mesh.parse_obj(('\n'.join(vertices + rows) + '\n').encode())
            value = dict(exported_snapshot(), **parsed, capture_id=capture, group_inventory=mesh.group_inventory(parsed))
            value.pop('snapshot_id')
            value['snapshot_id'] = mesh._hash(value)
            return value

        points = exported_snapshot()['positions_mm'] + [[1, 2, 3], [2, 2, 3], [1, 3, 3]]
        matrix = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        transformed = [point if 3 <= i < 6 else adapter._apply_rigid_matrix(matrix, point)
                       for i, point in enumerate(points)]
        before, after = snapshot(points, 0, 'before'), snapshot(transformed, .00001, 'after')
        proof = adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, after,
            'uuid-B', 'paper-B', matrix, companion_groups=('stitch',))
        self.assertTrue(proof['exported_positions_match_requested_matrix'])
        self.assertFalse(proof['exported_geometry_matches_requested_matrix'])
        self.assertEqual(proof['moving_group_readback']['stitch']['maximum_vertex_error_mm'], 0)
        transformed[3] = [102, 0, 0]
        changed = snapshot(transformed, .00001, 'changed')
        self.assertFalse(adapter.verify_rigid_transform_readback(current_scene(), current_scene(), before, changed,
            'uuid-B', 'paper-B', matrix, companion_groups=('stitch',))['exported_positions_match_requested_matrix'])

    def test_arrow_and_ring_attach_matrix_proof_to_the_single_native_receipt(self):
        calls = self.native_stub()
        before, translated = exported_snapshot(), exported_snapshot(delta=(12.5, -4, 3))
        rotated = exported_snapshot()
        rotated['capture_id'] = 'rotation-after'
        rotated['positions_mm'][:3] = [[10, -20, 5], [10, -10, 5], [0, -20, 5]]
        rotated.pop('snapshot_id')
        rotated['snapshot_id'] = mesh._hash(rotated)
        cases = (
            ('arrow_step', translated, [[1, 0, 0, 12.5], [0, 1, 0, -4], [0, 0, 1, 3], [0, 0, 0, 1]]),
            ('gizmo_drag', rotated, [[0, -1, 0, 10], [1, 0, 0, -20], [0, 0, 1, 5], [0, 0, 0, 1]]),
        )
        for operation, after, matrix in cases:
            with self.subTest(operation=operation), patch.object(adapter, '_mesh_read', side_effect=[before, after]) as read:
                reply = adapter.OPERATIONS[f'world_adapter.{operation}'](dict(self.params(operation), expected_matrix_world_mm=matrix))
                self.assertEqual(reply['status'], 'SUCCEEDED', reply)
                self.assertEqual(read.call_count, 2)
                proof = reply['result']['matrix_acceptance']
                self.assertTrue(proof['exported_geometry_matches_requested_matrix'])
                self.assertEqual(proof['matrix_world_mm'], matrix)
                self.assertFalse(proof['exact_world_writer_verified'])
        self.assertEqual([call['operation'] for call in calls], ['arrow_step', 'gizmo_drag'])
        self.assertTrue(all('expected_matrix_world_mm' not in call for call in calls))

    def test_matrix_expectation_rejects_tiny_motion_and_distinguishes_no_effect(self):
        calls = self.native_stub()
        matrix = [[1, 0, 0, .001], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        params = dict(self.params(), expected_matrix_world_mm=matrix)
        before, after = exported_snapshot(), exported_snapshot()
        after['capture_id'] = 'fresh-no-effect-capture'
        after.pop('snapshot_id')
        after['snapshot_id'] = mesh._hash(after)
        with patch.object(adapter, '_mesh_read', return_value=before):
            reply = adapter.OPERATIONS['world_adapter.arrow_step'](params)
        self.assertEqual(reply['error']['code'], 'MATRIX_EXPECTATION_TOO_SMALL')
        self.assertEqual(calls, [])
        matrix[0][3] = 10
        with patch.object(adapter, '_mesh_read', side_effect=[before, after]):
            reply = adapter.OPERATIONS['world_adapter.arrow_step'](params)
        self.assertEqual(reply['status'], 'SUCCEEDED')
        self.assertTrue(reply['result']['primitive_dispatch_verified'])
        self.assertFalse(reply['result']['matrix_acceptance']['exported_geometry_matches_requested_matrix'])
        self.assertEqual(reply['result']['matrix_acceptance']['observed_maximum_motion_mm'], 0)
        self.assertEqual(len(calls), 1)

    def test_canonical_arrow_requires_current_camera_and_signed_world_direction(self):
        calls = self.native_stub()
        before, after = exported_snapshot(), exported_snapshot(delta=(0, 0, -10))
        matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, -10], [0, 0, 0, 1]]
        params = dict(self.params(), canonical_view='left', expected_matrix_world_mm=matrix)
        with patch.object(adapter, '_mesh_read', side_effect=AssertionError('Reject the wrong axis before export')):
            wrong_axis = adapter.OPERATIONS['world_adapter.arrow_step'](dict(params, canonical_view='front'))
        self.assertEqual(wrong_axis['error']['code'], 'WORLD_DIRECTION_MISMATCH')
        export = SimpleNamespace(GenerateZcmrFrom3DWindow=Mock())
        with patch.dict(sys.modules, {'export_api': export}):
            with patch.object(adapter, '_mesh_read', return_value=before), \
                 patch.object(adapter, '_read_camera_basis', return_value={'right_world': [1, 0, 0], 'up_world': [0, 1, 0]}):
                stale = adapter.OPERATIONS['world_adapter.arrow_step'](params)
            self.assertEqual(stale['error']['code'], 'CAMERA_BASIS_CHANGED')
            self.assertEqual(calls, [])
            camera = {'right_world': [0, 0, -1], 'up_world': [0, 1, 0]}
            with patch.object(adapter, '_mesh_read', side_effect=[before, after]), \
                 patch.object(adapter, '_read_camera_basis', return_value=camera):
                accepted = adapter.OPERATIONS['world_adapter.arrow_step'](params)
        self.assertTrue(accepted['result']['matrix_acceptance']['exported_geometry_matches_requested_matrix'])
        self.assertEqual(accepted['result']['camera_basis_before_input'], camera)
        self.assertEqual(len(calls), 1)

    def test_readonly_group_binding_uses_no_native_or_selection_calls(self):
        with patch.object(adapter, '_mesh_read', return_value=exported_snapshot()), patch.object(adapter, '_invoke', side_effect=AssertionError('No native call')):
            result = adapter.read_group_binding({'pattern_uuid': 'uuid-B', 'group_name': 'paper-B', 'scene_token': TOKEN})
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertFalse(result['effects_started'])
        self.assertEqual(result['result']['label_certificate']['target']['group_name'], 'paper-B')

    def test_feature_routes_only_fixed_operations_with_core_journal(self):
        tools = {}; mcp = SimpleNamespace(tool=lambda: lambda f: tools.setdefault(f.__name__, f)); context = SimpleNamespace(call=Mock())
        feature.register(mcp, context)
        tools['world_adapter_arrow_step']('uuid-B', 'paper-B', 'ref', FP, 'right', TOKEN, 'request')
        args, kwargs = context.call.call_args
        self.assertEqual(args[0], 'world_adapter.arrow_step'); self.assertTrue(kwargs['mutation'])
        self.assertEqual(kwargs['scene_token'], TOKEN); self.assertEqual(kwargs['request_id'], 'request')
        self.assertEqual(feature.capabilities()['exact_world_transform_writer'], 'unsupported')


if __name__ == '__main__': unittest.main()
