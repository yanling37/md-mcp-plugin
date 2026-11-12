"""One fixed boolean UI setting. Does not expose Rest solver operations."""
import re


def capabilities():
    return {"feature": "rest_pinching_override", "UI_setting_writer": True, "solver_rest_writer": False,
            "simulation_started": False, "fixed_semantic_row": "Simulation Properties / Pinching Simulation Override",
            "live_validation": "pending"}


def register(mcp, context):
    @mcp.tool()
    def rest_set_pinching_override(scene_token: str, request_id: str, enabled: bool, budget_ms: int = 750) -> dict:
        """Set only the currently visible Pinching Simulation Override checkbox and read it back.

        Native batch keeps MD minimized. No simulation/brush/Update Rest/Reset Rest action.
        A changed value causes exactly one public checkbox click; a matching value causes none.
        Restore with the returned original_ui_value when this independent trial is finished.
        Unknown results must never be replayed automatically.
        """
        if type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
            raise ValueError("Current scene token required")
        if type(request_id) is not str or re.fullmatch("[0-9a-f]{32}", request_id) is None:
            raise ValueError("Lowercase UUID hex request_id required")
        if type(enabled) is not bool or type(budget_ms) is not int or not 50 <= budget_ms <= 1500:
            raise ValueError("Actual bool and bounded integer budget required")
        return context.call("rest_pinching_override.set_pinching_override",
                            {"scene_token": scene_token, "enabled": enabled, "budget_ms": budget_ms},
                            mutation=True, request_id=request_id, scene_token=scene_token)
