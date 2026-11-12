"""A native idle guard may reject restore before it can echo the held lease."""

from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import dispatch, world_grid_step as grid


TOKEN = 'a' * 64
REQUEST = 'b' * 32
LEASE = 'e37ee303-744d-4822-81ed-97e284004a2e'
IDENTITY = {'pid': 123, 'creation_filetime': 456, 'project_path': 'independent.zprj', 'md_version': [2026, 1, 237]}
SCENE = {'scene_token': TOKEN, 'identity': IDENTITY}
PARAMS = {'scene_token': TOKEN, 'pattern_uuid': 'owner', 'axis': 'x', 'distance_mm': .5}
RECOVER = {'scene_token': TOKEN, 'blocked_request_id': REQUEST}
GUARD = 'MINIMIZED_BACKGROUND_GRID_SCOPE_REQUIRED'


def guard_rejection():
    raw = {'schema': grid.SCHEMA, 'operation': 'restore', 'status': 'REJECTED', 'return_code': 1,
           'reason': GUARD, 'process_id': IDENTITY['pid'], 'process_creation_filetime': str(IDENTITY['creation_filetime']),
           'restore_required': True, 'restore_token': None, 'original_step_mm': None, 'step_mm': None, 'step_2d_mm': None,
           'original_stack': {}, 'opened_stack': {}, 'return_pair_closed': True, 'original_page_restored': False,
           'grid_widget_commit_verified': False, 'effective_step_verified': False, 'verified': False,
           'effects_started': False, 'requires_recovery': False,
           'step_set_value_calls': 0, 'return_press_calls': 0, 'return_release_calls': 0, 'action_trigger_calls': 0,
           'page_restore_calls': 0, 'OS_input_calls': 0, 'focus_calls': 0, 'event_pump_calls': 0}
    return {'status': 'REJECTED', 'result': raw, 'error': {'code': GUARD},
            'effects_started': False, 'requires_recovery': False}


class RestoreIdleGuardTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.state = {'blocked': False}
        self.restore_rejections = [guard_rejection()]
        self.calls = []
        self.arrow = stack.enter_context(patch.object(grid.world_adapter, '_execute', return_value={
            'status': 'REJECTED', 'result': {}, 'error': {'code': 'BACKGROUND_IDLE_SCOPE_REQUIRED'},
            'effects_started': False, 'requires_recovery': False}))
        for target, name, value in (
            (grid, '_state', self.state), (grid, 'scene', SCENE), (grid, 'check_scene_token', SCENE),
            (grid, '_current_target_scope', {'world_arrays_sha256': 'mesh'}),
            (grid.world_adapter, '_target', ({}, 'group')), (grid.world_adapter, '_selection', None),
            (grid.world_adapter, 'read_camera', {'status': 'SUCCEEDED', 'result': {
                'camera': {'right_world': [1, 0, 0], 'up_world': [0, 1, 0]}}}),
            (grid.world_adapter, 'inspect_3d', {'status': 'SUCCEEDED', 'result': {
                'after': {'view_ref': 'view', 'view_fingerprint': 'fingerprint'}}}),
            (grid.world_frame, '_require_stopped_simulation', None),
        ):
            stack.enter_context(patch.object(target, name, return_value=value))
        stack.enter_context(patch.object(grid, '_invoke', side_effect=self.native))
        stack.enter_context(patch.object(dispatch, '_module'))
        stack.enter_context(patch.object(dispatch, '_RECEIPTS', {}))
        stack.enter_context(patch.object(dispatch, '_PAYLOADS', {}))

    def native(self, request):
        self.calls.append(request['operation'])
        temporary = request['operation'] == 'set_temporary'
        if temporary:
            self.state['restore_token'] = LEASE
        else:
            self.assertEqual(request['restore_token'], LEASE)
            if self.restore_rejections:
                return self.restore_rejections.pop(0)
            self.state.pop('restore_token')
            self.state.pop('axis_lease')
        raw = {'schema': grid.SCHEMA, 'operation': request['operation'], 'status': 'SUCCEEDED',
               'process_id': IDENTITY['pid'], 'process_creation_filetime': str(IDENTITY['creation_filetime']),
               'original_page_restored': True, 'return_pair_closed': True, 'restore_token': LEASE,
               'original_step_mm': 10, 'step_mm': request['step_mm'] if temporary else 10,
               'restore_required': temporary, 'verified': True, 'grid_widget_commit_verified': True,
               'effects_started': True, 'requires_recovery': False}
        return {'status': 'SUCCEEDED', 'result': raw, 'error': None,
                'effects_started': True, 'requires_recovery': False}

    def parent(self):
        original = grid.translate_axis(PARAMS)
        dispatch._RECEIPTS[REQUEST] = dict(original, operation_id=REQUEST)
        dispatch._PAYLOADS[REQUEST] = grid.digest({'operation': grid.AXIS_OPERATION, 'params': PARAMS, 'scene_token': TOKEN})
        return original

    def test_guard_rejection_requires_exact_scope_and_zero_action_proof(self):
        saved = {'scene_identity': IDENTITY, 'restore_token': LEASE}
        self.assertTrue(grid._restore_guard_rejected(guard_rejection(), saved, LEASE))
        for field, value in (('process_id', 124), ('process_creation_filetime', '457'),
                             ('reason', 'GRID_RESTORE_TOKEN_DIFFERS'), ('return_code', True),
                             ('restore_required', False), ('original_page_restored', True),
                             ('original_stack', {'page': 1}), ('action_trigger_calls', 1),
                             ('step_set_value_calls', 1), ('return_press_calls', 1),
                             ('return_release_calls', 1), ('page_restore_calls', 1),
                             ('OS_input_calls', False), ('focus_calls', 1), ('event_pump_calls', 1)):
            with self.subTest(field=field):
                reply = guard_rejection()
                reply['result'][field] = value
                self.assertFalse(grid._restore_guard_rejected(reply, saved, LEASE))
        reply = guard_rejection()
        reply['result'].pop('restore_token')
        self.assertFalse(grid._restore_guard_rejected(reply, saved, LEASE))
        self.assertFalse(grid._restore_guard_rejected(guard_rejection(), saved, 'other'))

    def test_bound_recovery_repeats_only_restore_and_keeps_original_unknown(self):
        self.restore_rejections.append(guard_rejection())
        parent = self.parent()
        self.assertEqual(parent['status'], 'UNKNOWN')
        self.assertTrue(parent['result']['grid_recovery_available'])
        self.assertTrue(parent['result']['geometry_closed_or_absent'])
        original = deepcopy(dispatch._RECEIPTS[REQUEST])
        blocked = grid.recover_axis(RECOVER)
        self.assertEqual(blocked['status'], 'UNKNOWN')
        self.assertTrue(self.state['axis_lease']['safe_to_retry'])
        recovered = grid.recover_axis(RECOVER)
        self.assertEqual(recovered['status'], 'SUCCEEDED', recovered)
        self.assertTrue(recovered['result']['grid_restore_verified'])
        self.assertEqual(recovered['result']['restored_step_mm'], 10)
        self.assertEqual(dispatch._RECEIPTS[REQUEST], original)
        self.assertEqual(self.calls, ['set_temporary', 'restore', 'restore', 'restore'])
        self.arrow.assert_called_once()

    def test_changed_temporary_receipt_cannot_authorize_recovery(self):
        parent = self.parent()
        self.assertTrue(parent['result']['grid_recovery_available'])
        parent['result']['temporary_step']['result']['grid_widget_commit_verified'] = False
        rejected = grid.recover_axis(RECOVER)
        self.assertEqual(rejected['status'], 'REJECTED')
        self.assertEqual(self.calls, ['set_temporary', 'restore'])

    def test_unverified_movement_cannot_authorize_restore_recovery(self):
        self.arrow.return_value.update(status='UNKNOWN', effects_started=True, requires_recovery=True)
        parent = self.parent()
        self.assertFalse(parent['result']['geometry_closed_or_absent'])
        self.assertFalse(parent['result']['grid_recovery_available'])
        self.assertEqual(grid.recover_axis(RECOVER)['status'], 'REJECTED')
        self.assertEqual(self.calls, ['set_temporary', 'restore'])
