"""Capture a current 3D frame for world-coordinate planning."""


def capabilities():
    return {'feature': 'world_frame', 'capture_scope': 'current 3D viewport with matching camera and scene',
            'includes_native_vertex_or_ring_identity': False,
            'live_validation': 'selected_pattern_camera_png_and_stable_world_obj_verified',
            'ring_observation': 'image contour candidates tied to a verified frame request'}


def register(mcp, context):
    @mcp.tool()
    def world_frame_capture(scene_token: str, request_id: str, pattern_uuid: str | None = None,
                            include_mesh: bool = False) -> dict:
        """Capture the current background 3D viewport and bind the PNG to its camera and scene.

        Pass a pattern UUID to require it as the only selected piece. With
        include_mesh, verify simulation is off and compare two world OBJ exports.
        """
        params = {'scene_token': scene_token}
        if pattern_uuid is not None:
            params['pattern_uuid'] = pattern_uuid
        if include_mesh:
            params['include_mesh'] = True
        return context.call('world_frame.capture', params, mutation=True,
                            request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def world_frame_observe_rings(frame_request_id: str, scene_token: str, roi_px: list[int] | None = None) -> dict:
        """Read ring contour candidates in a verified frame. ROI uses physical PNG pixels."""
        params = {'frame_request_id': frame_request_id, 'scene_token': scene_token}
        if roi_px is not None:
            params['roi_px'] = roi_px
        return context.call('world_frame.observe_rings', params, scene_token=scene_token)

    @mcp.tool()
    def world_frame_calibrate_outline(frame_request_id: str, scene_token: str, focal_seed_px: float) -> dict:
        """Fit the selected outline against its retained world OBJ and validate separate edges."""
        return context.call('world_frame.calibrate_outline', {'frame_request_id': frame_request_id,
                            'scene_token': scene_token, 'focal_seed_px': focal_seed_px}, scene_token=scene_token)
