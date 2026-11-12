"""Public read-only Cloth vertex context observation."""


def capabilities():
    return {'context_observation': 'current Qt mesh mode, selected items and SDK pattern selection',
            'native_vertex_identity_available': False, 'live_validation': 'move_pattern_world_context_verified_on_independent_copy'}


def register(mcp, context):
    @mcp.tool()
    def cloth_vertex_context_inspect(scene_token: str) -> dict:
        """Observe current Cloth vertex mode, Qt items and SDK pattern selection.

        Returns native vertex identity as unavailable and has zero input effects.
        """
        return context.call('cloth_vertex_context.inspect', {'scene_token': scene_token}, scene_token=scene_token)
