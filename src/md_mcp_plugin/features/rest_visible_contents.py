"""Actual visible Contents reader and one fixed, persistent property-page command."""
import re


def capabilities():
    return {"feature": "rest_visible_contents", "live_validation": "pending", "RestShape_writer_available": False,
            "fixed_command": "open Simulation Properties page; no restoration of old page is requested",
            "simulation_started": False, "no_OS_input_focus_or_window_restore": True}


def _params(scene_token, budget_ms):
    if type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
        raise ValueError("Current lowercase scene token required")
    if type(budget_ms) is not int or not 50 <= budget_ms <= 1500:
        raise ValueError("budget_ms must be an integer in [50,1500]")
    return {"scene_token": scene_token, "budget_ms": budget_ms}


def register(mcp, context):
    @mcp.tool()
    def rest_visible_contents_inspect(scene_token: str, budget_ms: int = 750) -> dict:
        """Read the actual visible QWidget/PropertyEditorContents branch, pruning all hidden widget pages."""
        return context.call("rest_visible_contents.inspect", _params(scene_token, budget_ms), scene_token=scene_token)

    @mcp.tool()
    def rest_open_simulation_properties(scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Trigger only the existing enabled/noncheckable Simulation Properties action once.

        Keeps MD minimized; reads the resulting page and verifies current native 2D/runtime scope.
        No simulation, menus, arbitrary action, show/raise/activate, Rest write or old-page restore.
        Handler faults/modal behavior cannot be contained; unknown is not retried.
        """
        params = _params(scene_token, budget_ms)
        if type(request_id) is not str or re.fullmatch("[0-9a-f]{32}", request_id) is None:
            raise ValueError("Lowercase UUID-hex request_id required")
        return context.call("rest_visible_contents.open_simulation_properties", params,
                            mutation=True, request_id=request_id, scene_token=scene_token)
