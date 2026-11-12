"""One persistent MCP server with project-independent tool modules."""
import importlib

from mcp.server.fastmcp import FastMCP

from .config import Config
from .context import Context

FEATURES = ("transforms", "curves", "cloth", "cloth_vertex_context", "cut", "cut_qt", "canvas", "mesh", "scene_adapter", "rest_adapter", "world_adapter", "world_frame", "world_grid_step", "world_numeric_probe", "rest_dock_identity", "rest_visible_contents", "rest_pinching_override", "rest_simulation_context", "rest_cold")


def create_server(config=None, context=None, features=FEATURES):
    context = context or Context(config or Config.from_env())
    mcp = FastMCP("Marvelous Designer AI tools")
    feature_states = {}

    @mcp.tool()
    def md_connect() -> dict:
        """Connect to the configured local MD listener and read the active project. No window/input operation."""
        return context.connect()

    @mcp.tool()
    def md_scene() -> dict:
        """Read current project identity, paper UUIDs/input topology, quality and scene_token. No fixture loading."""
        return context.call("scene.snapshot", {})

    @mcp.tool()
    def md_project_open(path: str, request_id: str, scene_token: str, replace_current: bool = False) -> dict:
        """Open an existing .zprj via explicit non-dialog API options. Replacing a nonempty scene requires replace_current. Never saves/overwrites the input file."""
        return context.call("project.open", {"path": path, "replace_current": replace_current}, mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def md_project_save_copy(path: str, request_id: str, scene_token: str) -> dict:
        """Save current state to a new absolute .zprj filename; existing outputs are rejected. Saving can change the active project path."""
        return context.call("project.save_copy", {"path": path}, mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def md_selected_patterns(scene_token: str | None = None) -> dict:
        """Read selected pattern UUIDs using native per-index flags and corroborating selected names."""
        return context.call("selection.inspect", {}, scene_token=scene_token)

    @mcp.tool()
    def md_select_pattern(pattern_uuid: str, request_id: str, scene_token: str, refresh_selection: bool = False) -> dict:
        """Select exactly the current UUID's pattern, clearing previous selection through the SDK. Does not select a particular handle/line or change geometry; no OS input/window activation."""
        return context.call("selection.select", {"pattern_uuid": pattern_uuid, 'refresh':refresh_selection}, mutation=True, request_id=request_id, scene_token=scene_token)

    @mcp.tool()
    def md_recover_curve_operation(blocked_request_id: str, request_id: str, scene_token: str, expected_source_sha256: str) -> dict:
        """Reload the same saved project after a known-complete curve input batch left a partial target. Discards its unsaved project changes. Requires unchanged native readback, closed input, the saved-file SHA and ownership of the unresolved lease. Keeps the original failure receipt; never repeats the curve write."""
        return context.recover_curve_operation(blocked_request_id, request_id, scene_token, expected_source_sha256)

    @mcp.tool()
    def md_recover_cut_operation(blocked_request_id: str, request_id: str, scene_token: str, expected_source_sha256: str) -> dict:
        """Reload the same saved project after a completed Cut with partial verification.

        This discards the project's unsaved changes. The input batch, current readback and
        saved source SHA must match the original receipt. The original result is retained.
        """
        return context.recover_cut_operation(blocked_request_id, request_id, scene_token, expected_source_sha256)

    @mcp.tool()
    def md_operation_status(request_id: str) -> dict:
        """Read/reconcile a durable request without repeating the mutation. Timeout never means cancelled."""
        return context.operation_status(request_id)

    @mcp.tool()
    def md_reconcile_property_operation(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Observe a known-returned Simulation Properties command after its diagnostic read failed. Requires a complete current page and unchanged original native/runtime scope. Keeps the original outcome and never triggers the command again."""
        return context.reconcile_property_operation(blocked_request_id,request_id,scene_token)

    @mcp.tool()
    def md_reconcile_world_camera(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Observe the current camera after the original fixed-view request returned without verified readback.

        The original view and scene identify the observation. The original UNKNOWN result is retained.
        """
        return context.reconcile_world_camera(blocked_request_id, request_id, scene_token)

    @mcp.tool()
    def md_property_contents(scene_token: str, budget_ms: int = 750) -> dict:
        """Read controls on the currently visible Property Editor page using current owner paths. No page switching or setting writes; numeric UI values do not establish solver/native vertex ownership."""
        return context.call('rest_visible_recovery.inspect',{'scene_token':scene_token,'budget_ms':budget_ms},scene_token=scene_token)

    @mcp.tool()
    def md_capabilities() -> dict:
        """Read packaged capability declarations. Does not prove runtime support or successful live validation."""
        return {"protocol": "md-mcp-plugin/1", "features": feature_states, "connection": {"host": context.config.host, "port": context.config.port}, "native_startup": "pending_verified_autostart_route", "live_validation": "reported_per_feature"}

    for name in features:
        module_name = "md_mcp_plugin.features." + name
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            feature_states[name] = {"status": "implementation_pending"}
            continue
        module.register(mcp, context)
        feature_states[name] = module.capabilities()
    return mcp
