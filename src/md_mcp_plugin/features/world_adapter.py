"""Typed current-3D primitives. Exact world XYZ/angle writer is not claimed."""
from typing import Literal

from .transforms import WorldRotationRingPlan


def capabilities() -> dict:
    return {'live_validation': 'independent_project_readback_with_measured_xyz_steps',
            'native_bridge': 'packaged Qt 6.10.3 Windows x64 bridge',
            'inspect_3d': 'current view, gizmo and fixed Grid Properties action metadata',
            'read_camera': 'current CMT and CPT evidence bound to the same 3D viewport',
            'canonical_view': 'Front, Left and Quarter camera presets checked by export, with optional same-view garment fit',
            'arrow_step': 'screen-relative NoMod arrow P/R; actual mm delta is measured, never assumed 10mm',
            'gizmo_drag': 'bounded NoMod hover/P/activation/move/R; measured ring motion, with exact angle support limited to calibrated transforms',
            'mesh_readback': 'actual before/after scene OBJ; full current SDK unique-name/exact-group label bijection; no native owner/index inference',
            'matrix_acceptance': 'optional expected rigid world matrix, checked against the complete before/after exports from one primitive',
            'exact_world_transform_writer': 'unsupported', 'native_vertex_writer': 'unsupported', 'solver_rest_writer': 'unsupported'}


def register(mcp, context) -> None:
    @mcp.tool()
    def world_gizmo_scale_inspect(scene_token: str) -> dict:
        """Read the two fixed Gizmo Scale actions in the current view without triggering them."""
        return context.call('gizmo_scale.inspect', {'scene_token': scene_token}, scene_token=scene_token)

    @mcp.tool()
    def world_gizmo_scale_prove_roundtrip(scene_token: str, request_id: str, roi_px: list[int],
                                         pattern_uuid: str, increase_steps: Literal[2] = 2) -> dict:
        """Measure and restore two Gizmo Scale increments for the current selected pattern UUID."""
        if not pattern_uuid or increase_steps != 2:
            raise ValueError('Specify a current pattern UUID and two scale steps')
        args = {'scene_token': scene_token, 'roi_px': roi_px, 'pattern_uuid': pattern_uuid, 'increase_steps': 2}
        return context.call('gizmo_scale.prove_pair', args,
                            mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_gizmo_scale_recover_pair(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Observe the held native scale phase and restore its verified pending inverses in the original view."""
        return context.recover_gizmo_scale_pair(blocked_request_id, request_id, scene_token)

    @mcp.tool()
    def world_adapter_probe_runtime() -> dict:
        """Read adapter configuration/blocked status. No DLL loading or MD API call."""
        return context.call('world_adapter.probe_runtime', {})

    @mcp.tool()
    def md_reconcile_world_rotation(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Observe a held closed rotation with fresh geometry and assignment readbacks.

        Releases only the original matching lease. The original mutation outcome
        remains recorded and no further drag, offset setter or project reload runs.
        Check md_operation_status first when the original call timed out.
        """
        return context.reconcile_world_rotation(blocked_request_id, request_id, scene_token)

    @mcp.tool()
    def world_adapter_inspect_3d(budget_ms: int = 750, scene_token: str | None = None) -> dict:
        """Read the current 3D viewport, Qt mapping, gizmo state, fixed Grid Properties action and candidate method signatures. Qt scene units are not world mm."""
        return context.call('world_adapter.inspect_3d', {'budget_ms': budget_ms}, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_read_camera(scene_token: str) -> dict:
        """Read current CMT and CPT camera data tied to the same live 3D viewport and scene."""
        return context.call('world_adapter.read_camera', {'scene_token': scene_token}, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_set_canonical_view(view: Literal['front', 'left', 'quarter'], scene_token: str, request_id: str,
                                         fit_garment: bool = False) -> dict:
        """Set Front, Left or Quarter and verify the camera basis. Optionally fit the garment using the same background 3D view."""
        if type(fit_garment) is not bool:
            raise ValueError('fit_garment must be a boolean')
        params = {'view': view}
        if fit_garment:
            params['fit_garment'] = True
        return context.call('world_adapter.set_canonical_view', params, mutation=True,
                            request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_select_pattern(pattern_uuid: str, scene_token: str, request_id: str | None = None) -> dict:
        """Select one fresh UUID using SDK SelectPatternViaIndex(index, keepPreviousSelection=False), then verify all index getters and exact unique name. Selection only, no geometry/OS input. This bool does not choose 3D. Inspect current 3D view after selection before a pixel-based primitive."""
        return context.call('world_adapter.select_pattern', {'pattern_uuid': pattern_uuid, 'scene_token': scene_token}, mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_read_group_binding(pattern_uuid: str, group_name: str, scene_token: str) -> dict:
        """Read current SDK UUID/unique-name to exact OBJ group-label bijection, covering every current paper name with exclusive disjoint groups. Additional groups stay unclassified/protected. This proves labels only, not native mesh ownership or setter indices; exports once, no input or DLL required."""
        return context.call('world_adapter.read_group_binding', {'pattern_uuid': pattern_uuid, 'group_name': group_name, 'scene_token': scene_token}, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_inspect_topstitch_assignments(scene_token: str) -> dict:
        """Read current SDK Topstitch assignments and corroborate exact OBJ group labels."""
        return context.call('world_adapter.inspect_topstitch_assignments', {'scene_token': scene_token}, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_arrow_step(pattern_uuid: str, group_name: str, view_ref: str, view_fingerprint: str,
                                 direction: Literal['left', 'right', 'up', 'down'], scene_token: str,
                                 request_id: str | None = None, budget_ms: int = 750,
                                 expected_matrix_world_mm: list[list[float]] | None = None,
                                 matrix_tolerance_mm: float = .002,
                                 canonical_view: Literal['front', 'left'] | None = None) -> dict:
        """Send one screen-relative Qt arrow press/release and read both complete scene exports.

        An optional 4x4 world-mm translation matrix is checked after dispatch.
        Read result.matrix_acceptance for the comparison. The matrix does not
        select a direction or step size. Position tolerance is at most .002 mm.
        Pass canonical_view to verify the world axes immediately before input.
        """
        params = {'pattern_uuid': pattern_uuid, 'group_name': group_name, 'view_ref': view_ref,
                  'view_fingerprint': view_fingerprint, 'direction': direction, 'scene_token': scene_token,
                  'budget_ms': budget_ms}
        if expected_matrix_world_mm is not None:
            params.update(expected_matrix_world_mm=expected_matrix_world_mm, matrix_tolerance_mm=matrix_tolerance_mm)
        if canonical_view is not None:
            params['canonical_view'] = canonical_view
        return context.call('world_adapter.arrow_step', params, mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_adapter_gizmo_drag(pattern_uuid: str, group_name: str, view_ref: str, view_fingerprint: str,
                                 start: list[float], activation: list[float], end: list[float],
                                 purpose: Literal['translation_gizmo', 'rotation_ring'], scene_token: str,
                                 request_id: str | None = None, budget_ms: int = 750,
                                 expected_matrix_world_mm: list[list[float]] | None = None,
                                 matrix_tolerance_mm: float = .002) -> dict:
        """Send the fixed five-event drag in current viewport logical pixels, total path <=128px.

        Supply points from the current selected target and view. An optional
        world-mm 4x4 matrix checks the resulting exports in matrix_acceptance.
        The drag path alone has no calibrated world axis, pivot or angle.
        """
        params = {'pattern_uuid': pattern_uuid, 'group_name': group_name, 'view_ref': view_ref,
                  'view_fingerprint': view_fingerprint, 'start': start, 'activation': activation, 'end': end,
                  'purpose': purpose, 'scene_token': scene_token, 'budget_ms': budget_ms}
        if expected_matrix_world_mm is not None:
            params.update(expected_matrix_world_mm=expected_matrix_world_mm, matrix_tolerance_mm=matrix_tolerance_mm)
        return context.call('world_adapter.gizmo_drag', params, mutation=True, request_id=request_id, scene_token=scene_token)
