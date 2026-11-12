import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from md_mcp_plugin.embedded import cloth_numeric_runtime as runtime, dispatch


class ClothNumericRuntime(unittest.TestCase):
    def test_failed_meshbox_frame_retains_bounded_evidence_in_transaction_receipt(self):
        current = {'scene_token': 'test', 'identity': {'pid': 42, 'project_path': 'working.zprj'}}
        base = {'capture_id': 'before', 'arrays_sha256': 'initial', 'export_options': {}}
        binding = {'pattern_uuid': 'p1', 'group_vertex_index': 0, 'export_vertex_index': 0,
                   'source_xyz_mm': [0, 0, 0], 'requested_target_xyz_mm': [0.1, 0, 0]}
        mode = {'mode': 'MovePattern', 'sdk_selection': {'patterns': [{'pattern_uuid': 'p1'}]}}
        proof = {'mode_readback_verified': True, 'original_scope_preserved': True, 'physical_state_unchanged': True}
        native = Mock()
        native.transition.return_value = {'status': 'MESHBOX_VERIFIED', 'scene_stable': True,
                                          'sample_transaction_held': True, 'native': proof}
        initial_frame = {'verified': True, 'scene_token': 'test', 'scene_identity': current['identity'],
                         'camera': {'cpt_sha256': runtime._CALIBRATED_CPT}, 'view': {'mapping': {}}}
        sensitive = 'omitted-image-and-mapping-data' * 10000
        before = {'view': '3d', 'mapping_fingerprint': 'a' * 64, 'scene_item_grabber_absent': True,
                  'mapping': {'viewport_size': [652, 737], 'checked_actions': [sensitive]}}
        after = dict(before, mapping_fingerprint='b' * 64, mapping=dict(before['mapping'], viewport_size=[650, 737]))
        physical = {'background_safe': True, 'foreground_is_other_process': True, 'cursor_query_known': True,
                    'foreground_capture_absent': True, 'thread_focus_absent': True, 'cursor_position': [12, 34]}
        frame_receipt = {'status': 'PARTIAL', 'requires_recovery': True, 'gl_state_restored': True,
                         'background_endpoints_verified': False, 'original_WGL_context_dc_restored': True,
                         'owned_resources_cleanup_verified': True, 'before': before, 'after': after,
                         'physical_before': physical, 'physical_after': dict(physical, cursor_position=[13, 34]),
                         'png_base64': sensitive, 'image': {'path': sensitive}}
        frame_failure = {'status': 'UNKNOWN', 'effects_started': True, 'requires_recovery': True,
                         'error': {'code': 'FRAME_CAPTURE_ENDPOINT_CHANGED'}, 'result': {'native': frame_receipt}}
        frame_api = SimpleNamespace(_require_stopped_simulation=Mock(), capture=Mock(side_effect=[
            {'status': 'SUCCEEDED', 'requires_recovery': False, 'result': initial_frame}, frame_failure]))
        modules = {'cloth_numeric_adapter': SimpleNamespace(bind_position_target=Mock(return_value=binding)),
                   'cloth_meshbox_native': native, 'cloth_meshbox_current': object(),
                   'cloth_vertex_drag_native': SimpleNamespace(load=lambda: (object(), 'bridge')),
                   'world_frame': frame_api,
                   'native_setup': SimpleNamespace(configure_world_adapter=lambda: {'configured': True})}

        def select(_native, _planning, _current, _window, pending, _snapshot, _target):
            runtime.meshbox._frame_3d(pending)

        with patch.object(sys, runtime._STATE_KEY, None, create=True), \
                patch.object(runtime.meshbox, '_state', return_value={'pending': None}), \
                patch.object(runtime.meshbox, '_mode', side_effect=[mode, dict(mode, mode='MeshBox')]), \
                patch.object(runtime.cloth_vertex_context, '_root_window', return_value=100), \
                patch.object(runtime, '_module', side_effect=modules.__getitem__), \
                patch.object(dispatch, '_module', side_effect=modules.__getitem__), \
                patch.object(runtime, '_export', return_value=base), \
                patch.object(runtime, '_summary', side_effect=lambda x: x), \
                patch.object(runtime, '_fingerprint', return_value='source'), \
                patch.object(runtime, '_select', side_effect=select):
            result = runtime.run(current, {}, .002)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertTrue(result['requires_recovery'])
            self.assertEqual(native.transition.call_count, 1)
            self.assertTrue(getattr(sys, runtime._STATE_KEY)['frame_unresolved'])
        diagnostic = result['result']['frame_3d_failure']
        self.assertEqual(diagnostic['error_code'], 'FRAME_CAPTURE_ENDPOINT_CHANGED')
        observed = diagnostic['native']
        self.assertTrue(observed['gl_state_restored'])
        self.assertFalse(observed['background_endpoints_verified'])
        self.assertTrue(observed['physical_before']['foreground_capture_absent'])
        self.assertEqual(observed['physical_after']['cursor_position'], [13, 34])
        self.assertEqual(observed['before']['mapping_fingerprint'], 'a' * 64)
        self.assertEqual(observed['after']['mapping_fingerprint'], 'b' * 64)
        self.assertEqual(observed['mapping_changed_fields'], ['viewport_size'])
        self.assertFalse(observed['snapshot_equal'])
        encoded = json.dumps(diagnostic)
        self.assertLess(len(encoded), 1500)
        self.assertNotIn('omitted-image-and-mapping-data', encoded)

    def test_rejected_frame_with_no_native_receipt_remains_resolved(self):
        pending = {'scene': {'scene_token': 'test', 'identity': {}}, 'report': {}}
        frame_failure = {'status': 'REJECTED', 'effects_started': False, 'requires_recovery': False,
                         'error': {'code': 'BACKGROUND_SCOPE_REQUIRED'}, 'result': {'native': None}}
        modules = {'world_frame': SimpleNamespace(capture=lambda _params: frame_failure),
                   'native_setup': SimpleNamespace(configure_world_adapter=lambda: {'configured': True})}
        with patch.object(dispatch, '_module', side_effect=modules.__getitem__):
            with self.assertRaisesRegex(ValueError, 'BACKGROUND_SCOPE_REQUIRED'):
                runtime.meshbox._frame_3d(pending)
        self.assertFalse(pending['frame_unresolved'])
        self.assertEqual(pending['report']['frame_3d_failure'], {
            'status': 'REJECTED', 'effects_started': False, 'requires_recovery': False,
            'error_code': 'BACKGROUND_SCOPE_REQUIRED'})

    def test_closed_batch_needs_verified_absolute_position_to_succeed(self):
        current = {'scene_token': 'test', 'identity': {'pid': 42, 'project_path': 'working.zprj'}}
        base = {'capture_id': 'before', 'arrays_sha256': 'initial', 'export_options': {}}
        post = dict(base, capture_id='post', arrays_sha256='edited')
        frame = {'camera': {'cpt_sha256': runtime._CALIBRATED_CPT}, 'view': {'mapping': {}}, 'image': {'height': 737}}
        selection = {'patterns': [{'pattern_uuid': 'p1'}]}
        mode = {'mode': 'MovePattern', 'sdk_selection': selection}
        binding = {'pattern_uuid': 'p1', 'group_vertex_index': 0, 'export_vertex_index': 0,
                   'source_xyz_mm': [0, 0, 0], 'requested_target_xyz_mm': [0.1, 0, 0]}
        plan = {'stage_required': True, 'viewport_points': {}}
        proof = {'mode_readback_verified': True, 'original_scope_preserved': True, 'physical_state_unchanged': True}

        def select(_native, _planning, _current, _window, pending, _snapshot, _target):
            pending['report'].update(selection_3d_before=frame, selection_3d_after=frame)

        for verified in (False, True):
            with self.subTest(verified=verified):
                native = Mock()
                native.transition.side_effect = [
                    {'status': 'MESHBOX_VERIFIED', 'scene_stable': True, 'sample_transaction_held': True, 'native': proof},
                    {'status': 'MOVE_RESTORED', 'scene_stable': True, 'sample_transaction_held': False, 'native': proof},
                ]
                modules = {'cloth_numeric_adapter': SimpleNamespace(
                    bind_position_target=Mock(return_value=binding), plan_position_write=Mock(return_value=plan),
                    verify_fresh_selected_frame=Mock(),
                    verify_position_write=Mock(return_value={'verified': True, 'absolute_target_reached': verified})),
                    'cloth_meshbox_native': native, 'cloth_meshbox_current': object(),
                    'cloth_vertex_drag_native': SimpleNamespace(load=lambda: (object(), 'bridge')),
                    'world_frame': SimpleNamespace(_require_stopped_simulation=Mock())}
                exports = [base, dict(base, capture_id='fresh'), post, dict(post, capture_id='final')]
                with patch.object(sys, runtime._STATE_KEY, None, create=True), \
                        patch.object(runtime.meshbox, '_state', return_value={'pending': None}), \
                        patch.object(runtime.meshbox, '_mode', side_effect=[mode, dict(mode, mode='MeshBox'), mode]), \
                        patch.object(runtime.meshbox, '_frame_3d', return_value=frame), \
                        patch.object(runtime.cloth_vertex_context, '_root_window', return_value=100), \
                        patch.object(runtime, '_module', side_effect=modules.__getitem__), \
                        patch.object(runtime, '_export', side_effect=exports), \
                        patch.object(runtime, '_summary', side_effect=lambda x: x), \
                        patch.object(runtime, '_fingerprint', return_value='source'), \
                        patch.object(runtime, '_read', return_value=b'png'), \
                        patch.object(runtime, '_select', side_effect=select), \
                        patch.object(runtime, '_dispatch', return_value=({'paired_release_verified': True}, True)):
                    result = runtime.run(current, {}, .002)
                    self.assertEqual(result['status'], 'SUCCEEDED' if verified else 'UNKNOWN')
                    self.assertIs(result['result']['requested_position_verified'], verified)
                    self.assertTrue(result['result']['mode_restored'])
                    self.assertEqual(native.transition.call_count, 2)
                    self.assertIs(getattr(sys, runtime._STATE_KEY) is None, verified)


if __name__ == '__main__':
    unittest.main()
