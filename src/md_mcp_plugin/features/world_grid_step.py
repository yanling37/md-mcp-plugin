"""Read the Grid step and qualify a temporary axis movement in the current scene."""

from typing import Literal

from ..journal import GRID_AXIS


def capabilities():
    return {'feature': 'world_grid_step', 'scope': 'current Grid Properties value and original page readback',
            'live_validation': 'grid_value_and_page_restore_verified_on_independent_copies',
            'current_project_scope': 'current UUID and verified labels; generalized scope pending live validation',
            'recovery': 'same_scene_same_process_original_request'}


def register(mcp, context):
    @mcp.tool()
    def world_grid_step_inspect(scene_token: str, request_id: str) -> dict:
        """Read the current 3D arrow step in mm, restoring the original property page before returning.

        Acquires the mutation journal because the Grid Properties page may need to open temporarily.
        """
        return context.call('world_grid_step.inspect', {'scene_token': scene_token}, mutation=True,
                            request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_grid_step_translate_axis(pattern_uuid: str, axis: Literal['x', 'y', 'z'], distance_mm: float,
                                       scene_token: str, request_id: str,
                                       verification_profile: Literal['strict', 'position_with_obj_normal_quantization'] = 'strict') -> dict:
        """Move the current selected pattern along one axis using a temporary Grid step.

        Select the current pattern in Front for X/Y or Left for Z. Uses signed
        millimetres with one decimal place, reads the moved OBJ, and verifies
        restoration of the original Grid field and property page. The exported
        groups must bind uniquely. Other panels and their accessories are protected.
        The optional position profile independently verifies XYZ and bounds
        each target OBJ normal component to one six-decimal quantum.
        A failed matrix check can include a measured move and a restored Grid.
        """
        params = {'pattern_uuid': pattern_uuid, 'axis': axis, 'distance_mm': distance_mm, 'scene_token': scene_token}
        if verification_profile != 'strict':
            params['verification_profile'] = verification_profile
        return context.call(GRID_AXIS, params, mutation=True,
                            request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_grid_step_recover_axis(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Restore the original Grid field for an eligible same-process request without moving geometry."""
        return context.recover_grid_axis(blocked_request_id, request_id, scene_token)
