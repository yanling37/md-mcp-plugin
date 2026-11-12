"""Explicit current-project OBJ mesh reads; no native vertex writer claim."""


def capabilities() -> dict:
    return {
        'live_validation': 'scene and two distinct exact OBJ groups verified on MD 2026.1.237',
        'mesh.probe_runtime': {'status': 'supported', 'basis': 'Fixed option/API metadata only; no export or MD setters.'},
        'mesh.read_pattern': {'status': 'unsupported', 'basis': 'Live MD 2026.1.237 accepted typed patternList but exported other owners. Returns explicit unverified export diagnostics, never a UUID mesh/ref.'},
        'mesh.read_scene': {'status': 'supported', 'basis': 'Actual garment OBJ read in world XYZ millimetres with explicit thin, unwelded options.'},
        'mesh.read_group': {'status': 'supported', 'basis': 'Exact OBJ group subset with compact v/vt/vn and complete export index maps.'},
        'native_vertex_mapping': {'status': 'unsupported', 'basis': 'OBJ order is an export snapshot order, not simulation/setter order.'},
        'world_transform_backend': {'status': 'unsupported', 'basis': 'Still needs a real apply_transform writer and independently bound complete scene-invariant readback.'},
    }


def register(mcp, context) -> None:
    @mcp.tool()
    def mesh_probe_runtime(scene_token: str | None = None) -> dict:
        """Read fixed OBJ API/option metadata; performs no export or model modification."""
        return context.call('mesh.probe_runtime', {}, scene_token=scene_token)

    @mcp.tool()
    def mesh_read_pattern(pattern_uuid: str, vertex_indices: list[int] | None = None, scene_token: str | None = None) -> dict:
        """Diagnostic UUID-filter request. Current SDK can ignore patternList despite typed readback: returns UNSUPPORTED plus actual unbound export/group diagnostics, no UUID mesh or vertex refs. Use mesh_read_scene or an explicit OBJ-label mesh_read_group."""
        return context.call('mesh.read_pattern', {'pattern_uuid': pattern_uuid, 'vertex_indices': [] if vertex_indices is None else vertex_indices}, scene_token=scene_token)

    @mcp.tool()
    def mesh_read_scene(vertex_indices: list[int] | None = None, scene_token: str | None = None,
                        summary_only: bool = False) -> dict:
        """Fresh scene-wide garment OBJ export in world mm. Use summary_only to compare the canonical digest of positions, UVs, normals, face indices, and labels without returning arrays. The full OBJ remains available at its verified asset path. Vertex references require the full snapshot."""
        params = {'vertex_indices': [] if vertex_indices is None else vertex_indices}
        if summary_only:
            if params['vertex_indices']:
                raise ValueError('summary_only cannot be combined with vertex_indices')
            params['summary_only'] = True
        return context.call('mesh.read_scene', params, scene_token=scene_token)

    @mcp.tool()
    def mesh_read_group(group_name: str, vertex_indices: list[int] | None = None, scene_token: str | None = None) -> dict:
        """Read one exact OBJ group label from a fresh scene export. Compacts vertices/UV/normals and preserves complete bidirectional export index maps and source face indices. Multi-group selected faces are rejected. Group refs are NOT pattern UUID, solver vertex, or native setter refs; other groups including topstitch are excluded by exact label."""
        return context.call('mesh.read_group', {'group_name': group_name, 'vertex_indices': [] if vertex_indices is None else vertex_indices}, scene_token=scene_token)
