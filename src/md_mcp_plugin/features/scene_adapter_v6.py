"""Closed current-2D scene tools; all native work goes through context.call."""
from __future__ import annotations

import math
import re
from typing import Literal


def capabilities():
    return {"feature": "scene_adapter_v6", "live_validation": "pending", "qt_required": "6.10.3 release x64",
            "operations": ["inspect", "capture", "bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped", "set_tool", "fit_selected", "fit_all", "zoom_in_at", "zoom_out_at", "cut_selected"],
            "coordinate_space": "viewport logical pixels; Qt scene units are not model mm",
            "fixed_profile": "NoMod hover/press; Alt move/release; no cross-RPC grabs",
            "native_mm_mapping_verified": False, "native_handle_hit_verified": False,
            "native_cut_partition_verified": False, "capture_path": "Allocated only under the core-configured internal root",
            "no_OS_input_or_focus_or_window_restore": True}


def _budget(value):
    if type(value) is not int or not 50 <= value <= 1500:
        raise ValueError("budget_ms must be an integer in [50,1500]")
    return value


def _hex(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Current lowercase SHA256 fingerprint/token required")
    return value


def _scope(ref, fingerprint, token):
    if type(ref) is not str or not ref.startswith("mdscene6:") or len(ref) > 2048:
        raise ValueError("Use the reference returned by scene_adapter_v6_inspect_2d")
    return {"view_ref": ref, "view_fingerprint": _hex(fingerprint), "scene_token": _hex(token)}


def _request_id(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("request_id must be lowercase UUID hex")
    return value


def _xy(value):
    if type(value) is not list or len(value) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10000000 for v in value):
        raise ValueError("Finite nonnegative viewport logical XY required")
    return [float(v) for v in value]


def register(mcp, context):
    @mcp.tool()
    def scene_adapter_v6_zoom_out_at(view_ref: str, view_fingerprint: str, point: list[float], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Fixed scene hover then viewport +120 wheel at one fresh current logical point.

        No OS input, arbitrary delta, loop or automatic retry. Actual scale and native
        preservation require independent readback; no assumed 1.2 or private pivot.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.zoom_out_at", dict(params, point=_xy(point), budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_bezier_drag_precise_unsnapped(view_ref: str, view_fingerprint: str, start: list[float], end: list[float], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Fixed five phases: hover/P(NoMod), internal activationMove/finalMove/R(Alt).

        Activation lies along start鈫抏nd by max(12,min(32,startDragDistance+2)) logical
        pixels; all points must fit current viewport. Snap flags temporarily off
        with original-state RAII restoration. No caller activation/recipe/modifiers.
        Real activation, native hit and precision still require independent readback.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        a, b = _xy(start), _xy(end)
        if a == b:
            raise ValueError("Nonzero drag required")
        return context.call("scene_adapter_v6.bezier_drag_precise_unsnapped", dict(params, start=a, end=b, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_bezier_drag_unsnapped(view_ref: str, view_fingerprint: str, start: list[float], end: list[float], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Fixed four-phase Bezier batch with only Snap to Pattern/Smart Guide temporarily off.

        The two unique current-owner actions are triggered only when needed, then
        restored by original state after paired Release (including failure cleanup).
        No arbitrary QAction/setting/key. Does not prove semantic hit or mm precision.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        a, b = _xy(start), _xy(end)
        if a == b:
            raise ValueError("Nonzero drag required")
        return context.call("scene_adapter_v6.bezier_drag_unsnapped", dict(params, start=a, end=b, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_zoom_in_at(view_ref: str, view_fingerprint: str, point: list[float], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """One fixed NoMod scene hover then NoButton/NoMod viewport -120 vertical wheel.

        Requires fresh current 2D refs and Edit Curve Point mode. No OS wheel, focus,
        configurable delta, loop or automatic retry. Dispatch is not proof of actual
        zoom, pivot, magnification or native preservation; track/read back independently.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.zoom_in_at", dict(params, point=_xy(point), budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_fit_selected(view_ref: str, view_fingerprint: str, scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Fit the current 2D view to exactly one SDK-selected pattern through its fixed native action. No OS input or window activation; model coordinates are unchanged."""
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call('scene_adapter_v6.fit_selected', dict(params, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_fit_all(view_ref: str, view_fingerprint: str, scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Trigger the fixed current 2D Fit All action once, regardless of SDK selection count.

        Requires fresh current 2D references and an idle background window. Check a fresh
        2D capture to confirm that all intended patterns are in frame.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.fit_all", dict(params, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_runtime_capabilities() -> dict:
        """Read trusted configuration state without loading native code."""
        return context.call("scene_adapter_v6.runtime_capabilities", {})

    @mcp.tool()
    def scene_adapter_v6_inspect_2d(budget_ms: int = 750) -> dict:
        """Read current 2D view/tool refs, public metadata and zoom/fit/frame action declarations.

        No method/action is invoked. This does not establish native-mm mappings or handler callability.
        """
        return context.call("scene_adapter_v6.inspect", {"budget_ms": _budget(budget_ms)})

    @mcp.tool()
    def scene_adapter_v6_capture_2d(view_ref: str, view_fingerprint: str, scene_token: str, budget_ms: int = 750) -> dict:
        """Paint the current 2D viewport once, then resolve its owned framebuffer to PNG.

        No caller path, window restore, focus or event pump. Normal Qt/MD paint cache effects are
        allowed. Owned GL objects/context/bindings require cleanup; full GL purity and semantic
        scene content remain unproved. Root independently checks actual native preservation.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.capture", dict(params, budget_ms=_budget(budget_ms)), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_bezier_drag(view_ref: str, view_fingerprint: str, start: list[float], end: list[float], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """One fixed 2D NoMod hover/press, Alt move/release batch in current Edit Curve Point mode.

        This is a viewport primitive: native handle ownership, mm targets and preservation must be
        established independently. No arbitrary modifiers/events, OS input, focus or project changes.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        a, b = _xy(start), _xy(end)
        if a == b:
            raise ValueError("Nonzero drag required")
        return context.call("scene_adapter_v6.bezier_drag", dict(params, start=a, end=b, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_set_tool(view_ref: str, view_fingerprint: str, tool: Literal["transform_pattern", "pattern_edit", "edit_curve_point"], scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Set exactly one of three fixed current-owner 2D tool actions, or verify already-current.

        At most one QAction trigger; no arbitrary name, input or implicit model write verification.
        """
        if type(tool) is not str or tool not in ("transform_pattern", "pattern_edit", "edit_curve_point"):
            raise ValueError("Only the three fixed 2D tools exist")
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.set_tool", dict(params, tool=tool, budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)

    @mcp.tool()
    def scene_adapter_v6_cut_selected(view_ref: str, view_fingerprint: str, selection_fingerprint: str, scene_token: str, request_id: str, budget_ms: int = 750) -> dict:
        """Trigger only actionCutPattern2D once in Edit Pattern mode with one fresh public Qt selection.

        This proves action return only, never native line identity or a correct partition. Domain
        selection and native after-readback are caller/core obligations; partial/unknown is not retried.
        """
        params = _scope(view_ref, view_fingerprint, scene_token)
        return context.call("scene_adapter_v6.cut_selected", dict(params, selection_fingerprint=_hex(selection_fingerprint), budget_ms=_budget(budget_ms)),
                            mutation=True, request_id=_request_id(request_id), scene_token=scene_token)
