"""Current idle background scope shared by Rest and companion verification."""
from md_mcp_plugin.errors import PluginError
from . import world_adapter


_BUILD = [2026, 1, 237]


def _read_scope(current):
    observed = world_adapter.inspect_3d({'budget_ms': 1500})
    scope = world_adapter._camera_fit_scope(observed)
    result = observed['result']
    if result['scene_token'] != current['scene_token'] or not result['physical_before']['background_safe']:
        raise PluginError('REST_SCOPE_CHANGED', 'The current idle background view is required')
    scope['checked_actions'] = result['after']['mapping']['checked_actions']
    return scope
