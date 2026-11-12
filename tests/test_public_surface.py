"""The distributable package exposes current-scene operations only."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from md_mcp_plugin.embedded import dispatch, world_grid_step
from md_mcp_plugin.server import create_server


class PublicSurfaceTests(TestCase):
    def test_current_project_tools_remain_available(self):
        server = create_server(context=SimpleNamespace())
        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertTrue({
            'md_connect', 'md_project_open', 'md_project_save_copy', 'set_curve_handle_xy',
            'cut_qt_prepare_line_selection', 'cut_qt_execute_selected', 'cloth_set_positions',
            'cloth_rest_shape', 'cloth_reconcile_rest_archive', 'rest_adapter_inspect_host_controls',
            'rest_initialize_host',
            'world_adapter_set_canonical_view', 'world_grid_step_translate_axis', 'md_rotate_pattern_world',
        } <= names)
        self.assertTrue({
            'rest_adapter_trial_selected_rest', 'rest_adapter_probe_selected_rest_visual',
            'world_adapter_trial_topstitch_rotation', 'md_plan_pattern_world_rotation',
        }.isdisjoint(names))

    def test_unknown_internal_operation_rejects_before_native_configuration(self):
        with patch.object(dispatch, '_module', wraps=dispatch._module) as load:
            result = dispatch.execute({'operation': 'cut_qt.prepare_private_fixture', 'params': {}})
        self.assertEqual(result['error']['code'], 'UNSUPPORTED_OPERATION')
        self.assertFalse(result['effects_started'])
        self.assertEqual([call.args for call in load.call_args_list], [('cut_qt',)])

    def test_curve_recovery_configures_canvas_before_current_input_readback(self):
        steps = []
        operation = 'project.recover_curve'

        def recover(params):
            steps.append('recover')
            return {'status': 'SUCCEEDED', 'verified': True, 'effects_started': False}

        modules = {'project': SimpleNamespace(OPERATIONS={operation: recover}),
                   'native_setup': SimpleNamespace(configure_canvas=lambda: steps.append('configure_canvas'))}
        with patch.object(dispatch, '_module', side_effect=modules.__getitem__):
            response = dispatch.execute({'operation': operation, 'params': {}})
        self.assertEqual(response['status'], 'SUCCEEDED')
        self.assertEqual(steps, ['configure_canvas', 'recover'])

    def test_grid_scope_uses_current_owner_and_preserves_other_groups(self):
        current = {'scene_token': 'a' * 64, 'identity': {'md_version': [2026, 1, 237]}}
        snapshot = {key: [] for key in world_grid_step.world_frame.MESH_FIELDS}
        snapshot['group_inventory'] = {'groups': {'panel-a': {}, 'panel-b': {}}}
        with (patch.object(world_grid_step, 'check_scene_token', return_value=current),
              patch.object(world_grid_step.world_adapter, '_mesh_read', return_value=snapshot),
              patch.object(world_grid_step.world_adapter, 'bind_export_labels',
                           return_value={'additional_unclassified_groups': []}) as bind):
            self.assertIs(world_grid_step._current_scene(current['scene_token']), current)
            result = world_grid_step._current_target_scope(current, 'owner-a', 'panel-a')
        bind.assert_called_once_with(current, snapshot, 'owner-a', 'panel-a')
        self.assertEqual(result['protected_groups'], ['panel-b'])

    def test_grid_rejects_unclassified_accessories_before_any_move(self):
        current = {'scene_token': 'a' * 64, 'identity': {'md_version': [2026, 1, 237]}}
        snapshot = {key: [] for key in world_grid_step.world_frame.MESH_FIELDS}
        with (patch.object(world_grid_step.world_adapter, '_mesh_read', return_value=snapshot),
              patch.object(world_grid_step.world_adapter, 'bind_export_labels',
                           return_value={'additional_unclassified_groups': ['unknown-object']}),
              patch.object(world_grid_step.world_adapter, 'inspect_topstitch_assignments', return_value={'status': 'REJECTED'})):
            with self.assertRaisesRegex(ValueError, 'protected assignments'):
                world_grid_step._current_target_scope(current, 'owner-a', 'panel-a')
