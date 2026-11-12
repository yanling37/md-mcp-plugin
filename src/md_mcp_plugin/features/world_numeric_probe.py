"""Inspect already instantiated Translate 3D dialog controls and metadata."""
import re


def capabilities():
    return {"feature": "world_numeric_probe", "live_validation": "empty_dialog_inventory_verified_on_independent_copy", "readonly": True,
            "includes_hidden_instances": True, "write_contract_verified": False}


def register(mcp, context):
    @mcp.tool()
    def world_numeric_probe_inspect(scene_token: str, budget_ms: int = 500) -> dict:
        """Read existing Translate 3D instances, including hidden ones, and their Qt numeric controls and method declarations."""
        if type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
            raise ValueError("Current lowercase scene token required")
        if type(budget_ms) is not int or not 50 <= budget_ms <= 1500:
            raise ValueError("budget_ms must be an integer in [50,1500]")
        return context.call("world_numeric_probe.inspect", {"scene_token": scene_token, "budget_ms": budget_ms},
                            scene_token=scene_token)
