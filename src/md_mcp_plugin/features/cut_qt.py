"""Closed Qt Cut provider routes. No selector, pixel or certificate is public."""
from __future__ import annotations

import re


def capabilities():
    return {"feature": "cut_qt", "live_validation": "anchored_straight_with_existing_seam_and_single_cubic_verified",
            "selector_default": "packaged Mode1 frame provider when configured by the core",
            "surface_preservation": "native partition and exported 3D surface samples checked by default",
            "cut_and_sew": "unsupported", "SDK_Cut_calls": 0,
            "accepts_caller_pixels_or_verified_certificates": False,
            "verification_scope": "Native 2D partition and optional exported world-space surface checks"}


def _text(value, label):
    if type(value) is not str or not value or value != value.strip() or len(value) > 512:
        raise ValueError(label + " must be a bounded current reference")
    return value


def _hex(value, label, size=64):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value) is None:
        raise ValueError(label + " must be lowercase hex of length " + str(size))
    return value


def register(mcp, context):
    @mcp.tool()
    def cut_qt_runtime_capabilities() -> dict:
        """Read trusted provider configuration only; never selects or cuts."""
        return context.call("cut_qt.capabilities", {})

    @mcp.tool()
    def cut_qt_inspect(pattern_uuid: str, scene_token: str | None = None) -> dict:
        """Read shape/line references and fresh PointInfo geometry binding for the current paper.

        Pass the returned shape_uuid and line_uuids with the scene token and topology
        fingerprint to prepare. export_shape_id and export_line_ids are diagnostic.
        """
        if scene_token is not None:
            _hex(scene_token, "scene_token")
        return context.call("cut_qt.inspect", {"pattern_uuid": _text(pattern_uuid, "pattern_uuid")}, scene_token=scene_token)

    @mcp.tool()
    def cut_qt_prepare_line_selection(pattern_uuid: str, shape_uuid: str, line_uuid: str,
                                      topology_fingerprint: str, scene_token: str, request_id: str) -> dict:
        """Use an installed core-owned semantic selector for one current whole native internal line.

        Live-verified profiles are an anchored straight line with an existing seam,
        and an anchored single cubic segment. Supply one line from cut_qt_inspect.
        The packaged atomic provider prepares a frame candidate without changing selection.
        A legacy installed selection provider may select the line during preparation.
        Returns a single-use selection_ref after fresh native, PointInfo and frame checks.
        """
        if type(topology_fingerprint) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", topology_fingerprint) is None:
            raise ValueError("Use the topology_fingerprint from cut_qt_inspect")
        params = {"pattern_uuid": _text(pattern_uuid, "pattern_uuid"), "shape_uuid": _text(shape_uuid, "shape_uuid"),
                  "line_uuid": _text(line_uuid, "line_uuid"), "topology_fingerprint": topology_fingerprint,
                  "scene_token": _hex(scene_token, "scene_token")}
        return context.call("cut_qt.prepare", params, mutation=True,
                            request_id=_hex(request_id, "request_id", 32), scene_token=scene_token)

    @mcp.tool()
    def cut_qt_execute_selected(selection_ref: str, scene_token: str, request_id: str,
                                require_surface_preservation: bool = True) -> dict:
        """Consume one core-generated ref, revalidate, trigger selected Cut at most once and read native after.

        The default also checks before/after exported 3D surface samples, area, orientation
        and non-target groups. Set require_surface_preservation=false for the native 2D
        partition check alone. The result identifies the exact verification scope.
        """
        _text(selection_ref, "selection_ref")
        if re.fullmatch(r"cutqt1:[0-9a-f]{32}", selection_ref) is None:
            raise ValueError("Use the single-use reference from cut_qt_prepare_line_selection")
        if type(require_surface_preservation) is not bool:
            raise ValueError("require_surface_preservation must be an actual boolean")
        params = {"selection_ref": selection_ref, "scene_token": _hex(scene_token, "scene_token"),
                  "require_surface_preservation": require_surface_preservation}
        return context.call("cut_qt.execute", params, mutation=True,
                            request_id=_hex(request_id, "request_id", 32), scene_token=scene_token)
