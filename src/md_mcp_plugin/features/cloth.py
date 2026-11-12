"""Cloth XYZ/rest tools. Native write support is deliberately not inferred."""
from __future__ import annotations

import math
import re
from typing import Literal
from typing_extensions import TypedDict


class ClothVertexReference(TypedDict):
    kind: Literal["cloth_vertex_snapshot_v1"]
    pattern_uuid: str
    scene_token: str
    topology_fingerprint: str
    snapshot_id: str
    vertex_index: int


class ClothPositionTarget(TypedDict):
    vertex_ref: ClothVertexReference
    position_mm: list[float]


def capabilities() -> dict:
    """Static implementation claims only; importing this module never contacts MD."""
    return {
        "live_validation": "single_vertex_xyz_verified_on_flat_and_two_pattern_fixtures",
        "cloth.probe_runtime": {
            "status": "supported",
            "basis": "SDK metadata inspection for the current MD runtime.",
        },
        "cloth.read_scene_positions": {
            "status": "unverified",
            "basis": "GetClothPositions(List[float]) -> None is an output getter. Python list copy-back, units and owner mapping need live validation.",
        },
        "cloth.get_positions": {
            "status": "conditional",
            "basis": "World-mm OBJ positions and vertex references verified in two projects. Requires unique SDK-name/OBJ-label binding. Solver-rest and native index bindings are unavailable.",
        },
        "cloth.set_positions": {
            "status": "conditional",
            "basis": "The meshbox_plane method requires one owner-bound vertex, a unique outline and current 3D calibration. Front X/Y and Quarter-to-Front depth were verified on both the flat and two-pattern fixtures.",
        },
        "cloth.rest_shape": {
            "status": "conditional",
            "method": "native_archive",
            "basis": "Stopped Selected Update verified on flat and two-pattern fixtures, and Reset verified on the saved flat reference, on MD 2026.1.237.",
        },
        "math_helpers": {
            "status": "supported",
            "basis": "Snapshot references, relative perspective projection and measured XYZ residuals; these are calculations, not MD execution evidence.",
        },
    }


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 512:
        raise ValueError(f"{label} must be a nonempty, unpadded string of at most 512 characters")
    return value


def _finite(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _validate_targets(pattern_uuid: str, targets: list[ClothPositionTarget], scene_token: str, tolerance_mm: float) -> None:
    _text(pattern_uuid, "pattern_uuid")
    _text(scene_token, "scene_token")
    if _finite(tolerance_mm, "tolerance_mm") <= 0:
        raise ValueError("tolerance_mm must be positive")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4096:
        raise ValueError("targets must contain 1..4096 vertices")
    seen = set()
    snapshot_keys = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"vertex_ref", "position_mm"}:
            raise ValueError("Each target requires vertex_ref and position_mm")
        ref = target["vertex_ref"]
        if not isinstance(ref, dict) or set(ref) != set(ClothVertexReference.__annotations__):
            raise ValueError("A complete cloth snapshot reference is required")
        if ref["kind"] != "cloth_vertex_snapshot_v1" or ref["pattern_uuid"] != pattern_uuid:
            raise ValueError("Reference kind/owner differs from pattern_uuid")
        if ref["scene_token"] != scene_token:
            raise ValueError("STALE_REFERENCE: reference scene differs")
        for field in ("topology_fingerprint", "snapshot_id"):
            if not isinstance(ref[field], str) or re.fullmatch(r"sha256:[0-9a-f]{64}", ref[field]) is None:
                raise ValueError(f"{field} must be a SHA-256 fingerprint")
        index = ref["vertex_index"]
        if type(index) is not int or index < 0 or index in seen:
            raise ValueError("Snapshot vertex indices must be nonnegative and unique")
        seen.add(index)
        snapshot_keys.add((ref["topology_fingerprint"], ref["snapshot_id"]))
        xyz = target["position_mm"]
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise ValueError("position_mm must contain exactly three world coordinates")
        for coordinate in xyz:
            _finite(coordinate, "position_mm coordinate")
    if len(snapshot_keys) != 1:
        raise ValueError("All targets must belong to one mesh snapshot")


def register(mcp, context) -> None:
    @mcp.tool()
    def cloth_probe_runtime(scene_token: str | None = None) -> dict:
        """Inspect cloth and Rest API signatures for the current MD runtime."""
        return context.call('cloth.probe_runtime', {}, scene_token=scene_token)

    @mcp.tool()
    def cloth_read_scene_positions(sample_count: int = 4, scene_token: str | None = None) -> dict:
        """Diagnostic scene-wide XYZ getter: native units/order unverified, no editable owner/vertex references. Returns a bounded sample and count/hash."""
        if type(sample_count) is not int or not 0 <= sample_count <= 32:
            raise ValueError("sample_count must be an integer in [0, 32]")
        return context.call("cloth.read_scene_positions", {"sample_count": sample_count}, scene_token=scene_token)

    @mcp.tool()
    def cloth_get_positions(
        pattern_uuid: str,
        scene_token: str | None = None,
        vertex_indices: list[int] | None = None,
        probe_native_getter: bool = False,
        include_vertex_refs: bool = False,
    ) -> dict:
        """Read current world XYZ in mm and optionally issue scene-, topology-, and pose-bound OBJ vertex references. Use vertex_indices to select compact OBJ rows, or [] for topology metadata. References identify exported vertices."""
        _text(pattern_uuid, "pattern_uuid")
        return context.call("cloth.get_positions", {"pattern_uuid": pattern_uuid, "scene_token": scene_token,
                            "vertex_indices": vertex_indices, "probe_native_getter": probe_native_getter,
                            "include_vertex_refs": include_vertex_refs},
                            scene_token=scene_token)

    @mcp.tool()
    def cloth_set_positions(
        pattern_uuid: str,
        targets: list[ClothPositionTarget],
        scene_token: str,
        tolerance_mm: float = 0.002,
        request_id: str | None = None,
        method: Literal["meshbox_plane"] | None = None,
    ) -> dict:
        """Request absolute world XYZ in mm. The explicit meshbox_plane method uses one current OBJ vertex reference and a displacement in the current 3D camera plane. Select its pattern UUID in MovePattern/World and frame it for calibration. The method requires a uniquely resolved 2D outline and verifies the complete scene OBJ after writing."""
        _validate_targets(pattern_uuid, targets, scene_token, tolerance_mm)
        if method not in (None, 'meshbox_plane'):
            raise ValueError('Unknown Cloth position method')
        params = {"pattern_uuid": pattern_uuid, "targets": targets, "scene_token": scene_token, "tolerance_mm": tolerance_mm}
        if method is not None:
            params['method'] = method
        return context.call(
            "cloth.set_positions",
            params,
            mutation=True, request_id=request_id, scene_token=scene_token,
        )

    @mcp.tool()
    def cloth_recover_positions(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Restore the original mode after a verified MeshBox entry failed before selection.

        Supply the original unresolved Cloth position request and its current scene token.
        Recovery never repeats the vertex edit and the failed frame bridge remains quarantined.
        """
        return context.recover_cloth_positions(blocked_request_id, request_id, scene_token)

    @mcp.tool()
    def cloth_reconcile_rest_archive(blocked_request_id: str, request_id: str, scene_token: str) -> dict:
        """Observe the original closed native Rest action with a fresh archive and complete current OBJ.

        Retains its original outcome and releases its lease only after the same scene
        and owner have been independently observed. Sends no second Rest button action.
        """
        return context.reconcile_cloth_rest_archive(blocked_request_id, request_id, scene_token)

    @mcp.tool()
    def cloth_rest_shape(
        pattern_uuid: str,
        action: Literal["update_from_current", "reset_to_pattern"],
        scene_token: str,
        request_id: str | None = None,
        method: Literal['native_archive'] = 'native_archive',
    ) -> dict:
        """Update Rest from current 3D state or reset the reference for one selected UUID.

        The native_archive method requires MD 2026.1.237 minimized with stopped simulation,
        the existing hidden Rest host in Selected Patterns mode, and one current pattern
        selected. It checks serialized Rest coordinates against the current ordered OBJ.
        """
        _text(pattern_uuid, "pattern_uuid")
        _text(scene_token, "scene_token")
        if action not in ("update_from_current", "reset_to_pattern"):
            raise ValueError("action must be update_from_current or reset_to_pattern")
        if method != 'native_archive':
            raise ValueError('Unknown Cloth Rest method')
        params = {"pattern_uuid": pattern_uuid, "action": action, "scene_token": scene_token, "method": method}
        return context.call(
            "cloth.rest_shape",
            params,
            mutation=True, request_id=request_id, scene_token=scene_token,
        )
