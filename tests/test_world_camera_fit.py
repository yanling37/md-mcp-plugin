"""Offline checks for the optional same-view canonical garment fit."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from md_mcp_plugin.embedded import dispatch, world_adapter as adapter
from md_mcp_plugin.embedded.common import digest
from md_mcp_plugin.features import world_adapter as feature


TOKEN = 'a' * 64
REQUEST = 'b' * 32
SCENE = {'scene_token': TOKEN, 'identity': {'pid': 123, 'project_path': 'private.zprj'}}
FRONT = {'right_world': [1., 0., 0.], 'up_world': [0., 1., 0.],
         'matrix': [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 20], [0, 0, 0, 1]],
         'cmt_sha256': 'c' * 64, 'cpt_sha256': 'd' * 64}
FITTED = {**FRONT, 'matrix': [[1, 0, 0, 1], [0, 1, 0, 3], [0, 0, 1, 30], [0, 0, 0, 1]],
          'cmt_sha256': 'e' * 64}
QUARTER = {**FRONT, 'right_world': [0.707107, 0.0, -0.707107],
           'matrix': [[0.707107, 0.0, 0.707107, 12.0], [0.0, 1.0, 0.0, 0.0],
                      [-0.707107, 0.0, 0.707107, 20.0], [0.0, 0.0, 0.0, 1.0]]}


def inspected():
    return {'status': 'SUCCEEDED', 'result': {
        'physical_after': {'background_safe': True, 'foreground_window': 1234, 'cursor_position': [32, 12]},
        'after': {'native_view_ref': 'same', 'scene_item_grabber_absent': True, 'mapping_fingerprint': 'a' * 64,
                  'view_fingerprint': 'b' * 64, 'viewport': 'same',
                  'mapping': {'enabled': True, 'relative_visible': True, 'viewport_size': [652, 737],
                              'checked_actions': [{'object_name': 'front'}], 'global_origin': [8, 97]}}}}


class CameraFitTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.zoom = Mock(return_value=None)
        self.zoom.__doc__ = 'SetZoomView() -> None'
        self.zoom.side_effect = lambda: self.calls.append('fit')
        utility = SimpleNamespace(SetCamViewPoint=lambda index: self.calls.append(('view', index)),
                                  SetZoomView=self.zoom, GetViewPoint=lambda: 2)
        self.modules = {'utility_api': utility, 'export_api': SimpleNamespace(GenerateZcmrFrom3DWindow=object())}

    def run_fit(self, views, cameras=(FRONT, FITTED), **changes):
        with patch.object(adapter.importlib, 'import_module', side_effect=self.modules.__getitem__), \
             patch.object(adapter, 'scene', return_value=SCENE), patch.object(adapter, '_invoke', side_effect=views), \
             patch.object(adapter, '_read_camera_basis', side_effect=cameras):
            return adapter.set_canonical_view({'view': 'front', 'fit_garment': True, **changes})

    def test_front_then_exactly_one_fit_and_reports_actual_framing(self):
        before, middle, after = (inspected() for _ in range(3))
        after['result']['physical_after']['cursor_position'] = [999, 999]
        result = self.run_fit([before, middle, after])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertEqual(self.calls, [('view', 2), 'fit'])
        self.assertEqual(result['camera'], FITTED)
        self.assertTrue(result['fit_command_returned'])
        self.assertTrue(result['framing_change_observed'])

    def test_current_view_drift_stops_before_fit_and_remains_observable(self):
        first, changed = inspected(), inspected()
        changed['result']['after']['mapping']['global_origin'] = [19, 27]
        receipt = self.run_fit([first, changed])
        self.assertEqual(receipt['status'], 'UNKNOWN')
        self.assertEqual(receipt['error']['code'], 'CANONICAL_VIEW_NOT_VERIFIED')
        self.assertEqual(self.calls, [('view', 2)])
        self.assertTrue(receipt['result']['camera_observation']['viewpoint_returned'])
        self.assertFalse(receipt['result']['camera_observation']['fit_attempted'])
        receipt['operation_id'] = REQUEST
        payload = {'operation': 'world_adapter.set_canonical_view', 'params': {'view': 'front', 'fit_garment': True},
                   'scene_token': TOKEN}
        recovery = {'blocked_request_id': REQUEST, 'view': 'front', 'scene_token': TOKEN, 'fit_garment': True}
        with patch.dict(dispatch._RECEIPTS, {REQUEST: receipt}), patch.dict(dispatch._PAYLOADS, {REQUEST: digest(payload)}), \
             patch.object(adapter, 'scene', return_value=SCENE), patch.object(adapter, '_invoke', return_value=first), \
             patch.object(adapter.importlib, 'import_module', side_effect=self.modules.__getitem__), \
             patch.object(adapter, '_read_camera_basis', return_value=FRONT):
            reply = adapter.reconcile_camera(recovery)
            wrong = adapter.reconcile_camera(dict(recovery, fit_garment=False))
        self.assertEqual(reply['status'], 'SUCCEEDED', reply)
        self.assertFalse(reply['effects_started'])
        self.assertTrue(reply['fit_garment'])
        self.assertFalse(reply['requested_framing_verified'])
        self.assertEqual(wrong['status'], 'REJECTED')
        self.zoom.assert_not_called()

    def test_fit_exception_keeps_unresolved_request(self):
        self.zoom.side_effect = RuntimeError('runtime did not return from fit')
        result = self.run_fit([inspected(), inspected()])
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(result['requires_recovery'])
        self.assertEqual(result['error']['code'], 'CANONICAL_VIEW_CALL_UNRESOLVED')
        self.assertTrue(result['result']['camera_observation']['fit_attempted'])
        self.assertFalse(result['result']['camera_observation']['fit_returned'])

    def test_quarter_fit_reads_the_n237_basis_even_when_viewpoint_reports_minus_one(self):
        self.modules['utility_api'].GetViewPoint = lambda: -1
        fitted = {**QUARTER, 'cmt_sha256': 'e' * 64}
        result = self.run_fit([inspected(), inspected(), inspected()], (QUARTER, fitted), view='quarter')
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertEqual(self.calls, [('view', 3), 'fit'])
        self.assertEqual(result['viewpoint'], -1)
        self.assertEqual(result['camera'], fitted)

    def test_quarter_rejects_a_forward_mismatch_without_fit_and_recovers_from_current_readback(self):
        bad_forward = {**QUARTER, 'matrix': [row.copy() for row in QUARTER['matrix']]}
        bad_forward['matrix'][0][2] = -0.707107
        with patch.object(adapter.importlib, 'import_module', side_effect=self.modules.__getitem__), \
             patch.object(adapter, 'scene', return_value=SCENE), patch.object(adapter, '_invoke', return_value=inspected()), \
             patch.object(adapter, '_read_camera_basis', return_value=bad_forward):
            failed = adapter.set_canonical_view({'view': 'quarter'})
        self.assertEqual(failed['status'], 'UNKNOWN', failed)
        self.assertEqual(failed['error']['code'], 'CANONICAL_VIEW_NOT_VERIFIED')
        self.assertEqual(self.calls, [('view', 3)])
        payload = {'operation': 'world_adapter.set_canonical_view', 'params': {'view': 'quarter'}, 'scene_token': TOKEN}
        with patch.dict(dispatch._RECEIPTS, {REQUEST: dict(failed, operation_id=REQUEST)}), \
             patch.dict(dispatch._PAYLOADS, {REQUEST: digest(payload)}), \
             patch.object(adapter.importlib, 'import_module', side_effect=self.modules.__getitem__), \
             patch.object(adapter, 'scene', return_value=SCENE), patch.object(adapter, '_invoke', return_value=inspected()), \
             patch.object(adapter, '_read_camera_basis', return_value=QUARTER):
            result = adapter.reconcile_camera({'view': 'quarter', 'blocked_request_id': REQUEST, 'scene_token': TOKEN})
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertTrue(result['requested_view_verified'])
        self.assertFalse(result['effects_started'])
        self.assertEqual(self.calls, [('view', 3)])

    def test_explicit_false_and_omission_keep_the_original_host_payload(self):
        tools = {}

        def tool():
            return lambda fn: tools.setdefault(fn.__name__, fn)

        context = SimpleNamespace(call=Mock())
        feature.register(SimpleNamespace(tool=tool), context)
        send = tools['world_adapter_set_canonical_view']
        send('front', TOKEN, REQUEST)
        send('front', TOKEN, REQUEST, False)
        send('front', TOKEN, REQUEST, True)
        self.assertEqual([call.args[1] for call in context.call.call_args_list],
                         [{'view': 'front'}, {'view': 'front'}, {'view': 'front', 'fit_garment': True}])


if __name__ == '__main__':
    unittest.main()
