"""Background initialization of the native Rest host on a disposable project."""


def capabilities():
    return {
        'feature': 'rest_cold', 'native_bridge': 'rest_cold',
        'requires_minimized_md': True, 'requires_disposable_project': True,
        'may_change_simulated_positions_in_memory': True,
        'completion_tool': 'md_operation_status',
    }


def register(mcp, context):
    @mcp.tool()
    def rest_initialize_host(scene_token: str, scratch_project_sha256: str, request_id: str) -> dict:
        """Initialize the native Rest host in a fresh, minimized MD process.

        Open a disposable saved .zprj copy and enable Pinching Simulation Override
        through the existing Rest tools first. Supply the SHA-256 of that copy.
        Simulation may change in-memory cloth positions. The tool never saves the
        project. Poll md_operation_status with the same request ID until settled,
        then restore the original Override value and reopen the working project.
        """
        return context.call('rest_cold.begin', {'scene_token': scene_token, 'scratch_project_sha256': scratch_project_sha256},
                            mutation=True, request_id=request_id, scene_token=scene_token)
