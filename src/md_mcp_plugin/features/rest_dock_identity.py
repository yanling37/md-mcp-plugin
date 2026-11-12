"""Fixed property-dock identity inspection; no toggle/control operation exposed."""
import re


def capabilities():
    return {"feature": "rest_dock_identity", "live_validation": "pending", "readonly": True,
            "hidden_numeric_read": False, "toggle_restore_authorized": False, "RestShape_writer_available": False}


def register(mcp, context):
    @mcp.tool()
    def rest_dock_identity_inspect(scene_token: str, budget_ms: int = 750) -> dict:
        """Read fixed property candidates including hidden identities, ancestor docks/stacks and action associations.

        No hidden numeric traversal, action/slot/toggle, simulation, logger changes or panel creation.
        """
        if type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
            raise ValueError("Current lowercase scene token required")
        if type(budget_ms) is not int or not 50 <= budget_ms <= 1500:
            raise ValueError("budget_ms must be an integer in [50,1500]")
        return context.call("rest_dock_identity.inspect", {"scene_token": scene_token, "budget_ms": budget_ms}, scene_token=scene_token)
