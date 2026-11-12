"""Retain a frame quarantine while recovering only the proven MeshBox mode entry."""

from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from md_mcp_plugin.embedded import cloth_numeric_runtime as runtime, cloth_position_recovery as recovery, common, dispatch
from md_mcp_plugin.errors import PluginError
from md_mcp_plugin.journal import Journal


ID, CHILD, TOKEN, PIN = 'a' * 32, 'b' * 32, 'c' * 64, 'd' * 64
IDENTITY = {'pid': 123, 'creation_filetime': 456, 'project_path': 'sealed.zprj'}
SCENE = {'scene_token': TOKEN, 'identity': IDENTITY}
ARGS = {'blocked_request_id': ID, 'scene_token': TOKEN}
CAMERA = {'cmt_sha256': 'e' * 64, 'cpt_sha256': 'f' * 64, 'mapping': {'viewport_size': [652, 737]}}
BASELINE = {'capture_id': 'before', 'arrays_sha256': '0' * 64, 'topology_fingerprint': 'topology', 'export_options': {'thin': True}}
MODE = {'mode': 'MovePattern', 'sdk_selection': {'patterns': ['owner']}, 'physical_state_unchanged': True}
MESHBOX = dict(MODE, mode='MeshBox')
NATIVE = {'mode_readback_verified': True, 'original_scope_preserved': True, 'physical_state_unchanged': True,
          'root_window': '100', 'ticket': '1', 'process_id': 123, 'creation_filetime': '456'}
INVERSE = {'status': 'MOVE_RESTORED', 'ticket': '1', 'scene_token': TOKEN, 'scene_stable': True,
           'sample_transaction_held': False, 'native': dict(NATIVE, schema='md-qt-cloth-meshbox-entry/1',
               status='MOVE_RESTORED', command='restore', return_code=0, transaction_held=False,
               requires_recovery=False, restore_required=False, QAction_trigger_calls=1, global_input_calls=0,
               focus_change_calls=0, event_dispatch_calls=0, point_selection_calls=0, geometry_setter_calls=0)}


def original_receipt(fingerprint):
    endpoints = {'scene_item_grabber_absent': True}
    frame = {'schema': 'md-qt-world-frame-probe/1', 'operation': 'capture', 'status': 'PARTIAL', 'return_code': -10,
             'error_code': 'FRAME_CAPTURE_ENDPOINT_CHANGED', 'process_id': 123, 'process_creation_filetime': '456',
             'capture_effects_started': True, 'requires_recovery': True, 'effects_started': False, 'verified': False,
             'background_endpoints_verified': False, 'before': endpoints, 'after': deepcopy(endpoints)}
    frame.update(dict.fromkeys(('gl_state_restored', 'capture_recovery_verified', 'original_Qt_context_surface_restored',
                 'original_WGL_context_dc_restored', 'original_framebuffer_bindings_restored', 'owned_resources_cleanup_verified'), True))
    frame.update(dict.fromkeys(('OS_input_calls', 'focus_calls', 'activation_calls', 'camera_switch_calls',
                                'model_setter_calls', 'scene_input_calls'), 0))
    entry = {'status': 'MESHBOX_VERIFIED', 'ticket': '1', 'scene_token': TOKEN,
             'scene_stable': True, 'sample_transaction_held': True, 'native': NATIVE}
    proof = {'request_id': ID, 'fingerprint': fingerprint, 'source_sha256': '1' * 64, 'camera': CAMERA,
             'frame_bridge_sha256': PIN, 'frame_failure': frame}
    result = {'scene_token': TOKEN, 'scene_identity': IDENTITY, 'requested_position_verified': False,
              'entry_verified': True, 'mode_before': MODE, 'mode_during': MESHBOX, 'ticket': '1', 'entry': entry,
              'mesh_before': BASELINE, 'position_recovery': proof}
    return deepcopy({'status': 'UNKNOWN', 'operation_id': ID, 'effects_started': True, 'requires_recovery': True,
                     'error': {'code': 'CLOTH_POSITION_RECOVERY_REQUIRED'}, 'result': result})


class ClothRecoveryTests(unittest.TestCase):
    def test_exact_frame_witness_required(self):
        receipt = original_receipt(PIN)
        self.assertTrue(recovery.parent_witness(receipt, ID, PIN, TOKEN))
        self.assertFalse(recovery.parent_witness(receipt, CHILD, PIN, TOKEN))
        self.assertFalse(recovery.parent_witness(receipt, ID, '0' * 64, TOKEN))
        for key, value in (('gl_state_restored', False), ('before', {}), ('scene_input_calls', 1), ('return_code', -8)):
            with self.subTest(key=key):
                changed = deepcopy(receipt)
                changed['result']['position_recovery']['frame_failure'][key] = value
                self.assertFalse(recovery.parent_witness(changed, ID, PIN, TOKEN))
        receipt['result']['selection_dispatch'] = {}
        self.assertFalse(recovery.parent_witness(receipt, ID, PIN, TOKEN))

    def test_post_restore_observation_reuses_inverse_and_keeps_frame_blocked(self):
        parent = original_receipt(PIN)
        original = deepcopy(parent)
        frame = parent['result']['position_recovery']['frame_failure']
        pending = {'scene': SCENE, 'ticket': 1, 'window': 100, 'report': parent['result'],
                   'pre_selection_frame': True, 'frame_unresolved': True, 'frame_failure': frame}
        frame_state = {'blocked': True, 'busy': False, 'pin': PIN, 'process': IDENTITY,
                       'blocked_frame_receipt_sha256': common.digest(frame)}
        scope = (TOKEN, 'sealed.zprj', 123, 456, 100)
        native_state = {'transaction': {'binding': (scope, 1), 'entry_verified': True,
                                        'selector_attempted': False, 'selector_unresolved': False}}

        def close(*_args):
            native_state['transaction'] = None
            return INVERSE

        transition = Mock(side_effect=close)
        modules = {'world_frame': SimpleNamespace(_state=lambda: frame_state, _require_stopped_simulation=Mock()),
                   'cloth_meshbox_native': SimpleNamespace(check_scope=lambda *_: scope, _state=lambda _: native_state,
                                                          transition=transition)}
        before, after = (dict(BASELINE, capture_id=name) for name in ('pre-recover', 'post-recover'))
        with patch.object(sys, runtime._STATE_KEY, pending, create=True), \
                patch.object(dispatch, '_RECEIPTS', {ID: parent}), patch.object(dispatch, '_PAYLOADS', {ID: PIN}), \
                patch.object(runtime.common, 'check_scene_token', return_value=SCENE), \
                patch.object(runtime, '_module', side_effect=modules.__getitem__), \
                patch.object(runtime, '_observe_recovery', side_effect=[(MESHBOX, before, CAMERA), ValueError('sample changed'),
                                                                        (MODE, after, CAMERA)]):
            self.assertEqual(runtime.recover(ARGS)['status'], 'UNKNOWN')
            result = runtime.recover(ARGS)
            self.assertEqual(result['status'], 'SUCCEEDED', result)
            self.assertFalse(result['effects_started'])
            self.assertIsNone(getattr(sys, runtime._STATE_KEY))
            self.assertEqual(dispatch._RECEIPTS[ID], original)
        transition.assert_called_once_with(SCENE, 100, 1, 'restore')
        self.assertTrue(frame_state['blocked'])

    def test_journal_keeps_original_and_releases_only_verified_matching_child(self):
        with TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            record, _ = journal.claim(ID, recovery.WRITE, {'method': 'meshbox_plane'}, TOKEN)
            receipt = original_receipt(record['fingerprint'])
            journal.finish(ID, receipt)
            with self.assertRaises(PluginError):
                journal.claim_recovery(CHILD, ID, dict(ARGS, scene_token=PIN), PIN, operation=recovery.RECOVERY)
            journal.claim_recovery(CHILD, ID, ARGS, TOKEN, operation=recovery.RECOVERY)
            payload = {'blocked_request_id': ID, 'scene_token': TOKEN, 'scene_identity': IDENTITY,
                       'verified': True, 'source_file_unchanged': True, 'frame_quarantined': True,
                       'transaction_closed': True, 'restore': INVERSE, 'mode_after': MODE,
                       'mesh_after': dict(BASELINE, capture_id='after'), 'camera_after': CAMERA,
                       'recovery_action': recovery.RESTORED, 'original_mutation_status': 'UNKNOWN'}
            child = {'status': 'SUCCEEDED', 'operation_id': CHILD, 'result': payload,
                     'effects_started': True, 'requires_recovery': False}
            forged = deepcopy(child)
            forged['result']['frame_quarantined'] = False
            self.assertEqual(journal.finish(CHILD, forged)['status'], 'UNKNOWN')
            self.assertTrue(journal._matching_lease(ID, recovery.WRITE))
            self.assertEqual(journal.finish(CHILD, child)['status'], 'SUCCEEDED')
            self.assertEqual(journal.finish(CHILD, child)['status'], 'SUCCEEDED')
            self.assertFalse(journal.lease.exists())
            self.assertEqual(journal.get(ID)['receipt'], receipt)
            self.assertEqual(journal.get(ID)['status'], 'UNKNOWN')
