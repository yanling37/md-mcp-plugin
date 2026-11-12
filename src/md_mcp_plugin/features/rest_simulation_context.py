"""Readonly fixed Start/Stop/Pinching entry metadata. No control command."""
import re


def capabilities():
    return {"feature": "rest_simulation_context", "readonly": True, "simulation_start_stop_available": False,
            "running_state_contract_verified": False, "RestShape_writer_available": False, "live_validation": "pending"}


def register(mcp, context):
    @mcp.tool()
    def rest_simulation_context_inspect(scene_token: str, budget_ms: int = 750) -> dict:
        """Read fixed SimStop/Drape/Brush actions, their associated buttons, real meta declarations and fixed SDK docs.

        Does not trigger actions, invoke slots or SDK getters, start/stop simulation or create dialogs.
        QAction checked states remain observations; they are not independently verified solver running state.
        """
        if (type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None
                or type(budget_ms) is not int or not 50 <= budget_ms <= 1500):
            raise ValueError("Current lowercase token and integer budget [50,1500] required")
        return context.call("rest_simulation_context.inspect", {"scene_token": scene_token, "budget_ms": budget_ms}, scene_token=scene_token)
