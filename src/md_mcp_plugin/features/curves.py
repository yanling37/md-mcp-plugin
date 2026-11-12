"""Public Bezier tools. All live work goes through context.call."""
from __future__ import annotations

import math


def capabilities() -> dict:
    """Static evidence only; importing this module never contacts MD."""
    return {
        "feature": "curves",
        "live_validation": "native reads and independent outer handle writes verified on two documents and two patterns",
        "supported": [
            "Offline parsing of native four-point cubic Bezier IDs in millimetres",
            "Owner, topology, geometry and scene-bound handle references",
            "Absolute XY writes of one independently editable outer Bezier handle",
            "Native readback of the target, fixed endpoints, opposite handles and other 2D semantics",
        ],
        "tested_target_errors_mm": [0.000233, 0.000912],
        "preconditions": ["Current UUID selected", "Edit Curve Point mode", "Fit Selected", "Unique visible handle binding"],
        "unsupported": [
            "Internal or linked handles, hidden targets and ambiguous projections",
        ],
        "default_write_entry_point": "set_curve_handle_xy",
        "verification_scope": "Native 2D geometry and semantics",
    }


def _text(value: str, name: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 4096 or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _coordinate(value: float, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10_000_000:
        raise ValueError(f"{name} must be finite and within +/-10000000 mm")
    return float(value)


def register(mcp, context) -> None:
    @mcp.tool()
    def curve_runtime_capabilities() -> dict:
        """Read actual API symbols and the trusted writer's availability; never edits geometry."""
        return context.call("curves.runtime_capabilities", {})

    @mcp.tool()
    def curve_handles(pattern_uuid: str, scene_token: str | None = None) -> dict:
        """Read cubic Bezier curves and handle refs from the current pattern UUID; XY is 2D mm.

        References include their owner and fresh topology/geometry fingerprints. Re-read after
        any scene change. Unsupported spline encodings are reported without conversion.
        """
        _text(pattern_uuid, "pattern_uuid")
        if scene_token is not None:
            _text(scene_token, "scene_token")
        return context.call("curves.inspect", {"pattern_uuid": pattern_uuid}, scene_token=scene_token)

    @mcp.tool()
    def set_curve_handle_xy(
        pattern_uuid: str,
        handle_ref: str,
        x_mm: float,
        y_mm: float,
        scene_token: str,
        tolerance_mm: float = 0.002,
        request_id: str | None = None,
    ) -> dict:
        """Set one independently editable outer Bezier handle to absolute 2D XY in mm.

        Select its pattern with md_select_pattern, set Edit Curve Point mode, and Fit Selected.
        Use a fresh scene_token and handle_ref from curve_handles and a unique request_id.
        The operation measures the current input mapping and verifies the native target,
        fixed endpoints, opposite handles and other 2D semantics before reporting success.
        """
        _text(pattern_uuid, "pattern_uuid")
        _text(handle_ref, "handle_ref")
        _text(scene_token, "scene_token")
        if request_id is not None:
            _text(request_id, "request_id")
        x_mm, y_mm = _coordinate(x_mm, "x_mm"), _coordinate(y_mm, "y_mm")
        if type(tolerance_mm) not in (int, float) or not math.isfinite(tolerance_mm) or not 0 < tolerance_mm <= 0.002:
            raise ValueError("tolerance_mm must be finite and in (0, 0.002] mm")
        return context.call(
            "curves.set_handles",
            {"pattern_uuid": pattern_uuid, "handle_ref": handle_ref, "target_xy_mm": [x_mm, y_mm],
             "tolerance_mm": float(tolerance_mm)},
            mutation=True, request_id=request_id, scene_token=scene_token,
        )
