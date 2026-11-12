"""Typed MCP surface for closed current-view Qt gestures."""
from __future__ import annotations

import math
import re
from typing import Literal


def capabilities() -> dict:
    """Static facts only; this function never loads native code or contacts MD."""
    return {
        "feature": "canvas", "live_validation": {
            "runtime": "MD 2026.1.237 / Windows x64 / Qt 6.10.3",
            "view_read": "2d_and_3d_passed",
            "event_batches": "2d_click_and_drag_passed_on_two_distinct_documents",
            "3d_gestures": "pending", "semantic_geometry_writes": "pending",
        },
        "supported": ["Typed view/click/drag contracts and current-scene reference validation",
                      "Offline-built independent Qt 6.10.3 x64 C ABI bridge"],
        "unverified": ["3D click/drag", "Semantic model hit and domain writeback"],
        "unsupported": ["World XYZ or semantic control-handle setters", "Cross-RPC press, release or mouse grabs",
                        "OS input, focus/activation/restore, arbitrary native pointers or code", "Automatic project import/save/simulation"],
        "native_setup": "Core configures the packaged, SHA256-pinned bridge; no tool-supplied native library path",
        "coordinate_space": "viewport_logical_pixels", "button": "left", "max_points": 32, "max_event_calls": 34,
        "background": "Bridge sends only nonspontaneous Qt scene events; MD must already be inactive behind another foreground process",
        "verification_scope": "Event delivery/release and public view/state observations; native handler effects are not certified",
    }


def _fingerprint(value, label):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA256 fingerprint")
    return value


def _budget(value):
    if type(value) is not int or not 50 <= value <= 1500:
        raise ValueError("budget_ms must be an integer in [50,1500]")
    return value


def _view(value, all_views=False):
    if type(value) is not str or value not in (("all", "2d", "3d") if all_views else ("2d", "3d")):
        raise ValueError("view must be 2d or 3d (or all when reading)")
    return value


def _path(action, points):
    if type(action) is not str or action not in ("click", "drag") or type(points) is not list:
        raise ValueError("action must be click or drag and points must be logical XY pairs")
    if not ((action == "click" and len(points) == 1) or (action == "drag" and 2 <= len(points) <= 32)):
        raise ValueError("click needs one point; drag needs 2..32 points including its start")
    for point in points:
        if type(point) is not list or len(point) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10_000_000 for v in point
        ):
            raise ValueError("Each XY point must be finite, nonnegative viewport logical pixels")
    if action == "drag" and points[0] == points[-1]:
        raise ValueError("A drag must end at a different point")
    return [[float(v) for v in p] for p in points]


def register(mcp, context) -> None:
    @mcp.tool()
    def canvas_runtime_capabilities() -> dict:
        """Inspect trusted native configuration and loaded Qt module identity; never loads the bridge."""
        return context.call("canvas.runtime_capabilities", {})

    @mcp.tool()
    def canvas_views(view: Literal["all", "2d", "3d"] = "all", budget_ms: int = 750) -> dict:
        """Read current MD 2D/3D Qt views and fresh scene-bound references; does not move or activate MD.

        Returns view_ref, view_fingerprint, viewport logical size, DPR, transforms, scroll,
        checked tool actions and scene_token. Scene coordinates are Qt units; they are not
        model millimetres or world XYZ. Does not capture an image or certify hidden camera state.
        """
        return context.call("canvas.views", {"view": _view(view, True), "budget_ms": _budget(budget_ms)})

    @mcp.tool()
    def canvas_gesture(
        view: Literal["2d", "3d"],
        view_ref: str,
        view_fingerprint: str,
        action: Literal["click", "drag"],
        points: list[list[float]],
        scene_token: str,
        request_id: str,
        modifiers: list[Literal["shift", "ctrl", "alt"]] | None = None,
        budget_ms: int = 750,
    ) -> dict:
        """Send ONE complete left-button click/drag to a freshly read current 2D/3D view.

        Points are viewport LOGICAL pixels, independent of DPR: click=[start]; drag=[start,
        intermediate...,end], at most 32 points. Uses the current tool without selecting modes.
        Modifiers affect these synthetic events only. MD must already be inactive/background;
        no window restore, activation or OS input is used. Native preflight validates dynamic
        bounds, references and mappings before any events. Release/cleanup is in this same RPC.

        SUCCEEDED proves the closed event batch and public view readback only; selection hits,
        geometry, handles and XYZ require separate domain readback. Partial/unknown dispatches
        need root recovery; do not retry with a new request_id. Budget is cooperative between
        events, not a native callback timeout. Requires the scene_token returned by canvas_views.
        """
        _view(view)
        if type(view_ref) is not str or not view_ref.startswith("mdcanvas1:") or len(view_ref) > 2048:
            raise ValueError("view_ref must come from canvas_views")
        _fingerprint(view_fingerprint, "view_fingerprint")
        _fingerprint(scene_token, "scene_token")
        path = _path(action, points)
        _budget(budget_ms)
        if modifiers is None:
            modifiers = []
        if type(modifiers) is not list or any(type(m) is not str or m not in ("shift", "ctrl", "alt") for m in modifiers) or len(set(modifiers)) != len(modifiers):
            raise ValueError("modifiers must contain unique event-local shift, ctrl or alt")
        if type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise ValueError("request_id must be a lowercase UUID hex string")
        return context.call("canvas.gesture", {"view": view, "view_ref": view_ref, "view_fingerprint": view_fingerprint,
                            "action": action, "points": path, "scene_token": scene_token,
                            "modifiers": sorted(modifiers), "budget_ms": budget_ms},
                            mutation=True, request_id=request_id, scene_token=scene_token)
