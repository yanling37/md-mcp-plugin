"""Fixed Rest Override metadata reader; no tool enters Simulation or writes Rest."""
import re
from typing import Literal


def capabilities():
    return {'feature': 'rest_adapter', 'scope': 'current Rest host and control inventory', 'readonly': True}


def register(mcp, context):
    @mcp.tool()
    def rest_adapter_inspect_host_controls(scene_token: str, budget_ms: int = 750) -> dict:
        """Catalog existing standard Qt controls under the current Override host, including hidden controls."""
        if type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
            raise ValueError("Current lowercase scene token required")
        if type(budget_ms) is not int or not 50 <= budget_ms <= 1500:
            raise ValueError("budget_ms must be an integer in [50,1500]")
        return context.call("rest_adapter.inspect_host_controls", {"scene_token": scene_token, "budget_ms": budget_ms},
                            scene_token=scene_token)

