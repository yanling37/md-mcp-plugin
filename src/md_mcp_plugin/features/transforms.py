"""Typed tools for explicit world-space whole-pattern transforms.

Importing/registering this module never connects to Marvelous Designer.
The embedded module reports unsupported until a native world backend is bound.
"""
from __future__ import annotations

import math
import re
from typing import Literal

from typing_extensions import NotRequired, TypedDict


class WorldRotationRingPlan(TypedDict):
    frame_request_id: str
    roi_px: list[int]
    start: list[float]
    activation: list[float]
    end: list[float]
    ring_color: NotRequired[Literal['red', 'green', 'blue']]
    gizmo_scale_steps: NotRequired[Literal[2]]
    expanded_ring_support_sha256: NotRequired[str]


def capabilities() -> dict:
    """Static engineering status, not a live SDK probe."""
    return {
        "module": "transforms",
        "live_validation": "fixed_grid_xyz_and_calibrated_xyz_rotation_positions_verified",
        "world_translation": {
            "status": "unsupported",
            "reason": "No verified generic native world/mm writer is bundled.",
            "verified_primitive": "world_adapter_arrow_step with fixed 10 mm step and measured Front/Left basis",
            "verified_decimal_step": "world_grid_step_translate_axis verified on independent copies, all three axes",
        },
        "world_rotation": {
            "status": "requires_current_calibrated_ring_plan",
            "scope": "one bounded drag with independent full exported world matrix readback",
            "verification_profiles": ["strict", "positions"],
            "live_scope": "Calibrated -X, -Y and -Z axis lines at +1 degree on a flat pattern, and -Z with an assigned Texture Topstitch in an independent copy",
            "live_position_error_mm": {"x_flat": 0.0001299483, "y_flat": 0.0000356027, "z_flat": 0.000484,
                                       "panel_with_topstitch": 0.0002423834},
            "scaled_red_preview_tool": "world_gizmo_scale_prove_roundtrip",
            "closed_rotation_observation_tool": "md_reconcile_world_rotation",
            "arbitrary_axis_or_pivot_backend_installed": False,
        },
        "controller": {
            "status": "unverified",
            "implemented": [
                "UUID resolution in the current project",
                "explicit world/mm affine requests",
                "single backend write followed by independent full-scene readback",
            ],
            "requires": "embedded.transforms.install_world_backend(native_backend)",
        },
        "arrangement_fallback": False,
    }


def _uuid(value: str) -> str:
    if (
        not isinstance(value, str) or not value or value.strip() != value
        or len(value) > 256 or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("pattern_uuid must be an exact nonblank UUID string")
    return value


def _number(value: float, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be finite") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _vector(value: list[float], label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} requires exactly three finite numbers")
    return [_number(v, label) for v in value]


def _tolerance(value: float) -> float:
    value = _number(value, "tolerance_mm")
    if not 0 < value <= 0.002:
        raise ValueError("tolerance_mm must be > 0 and <= 0.002 mm")
    return value


def _rotation_params(pattern_uuid, axis_world, angle_degrees, pivot_world_mm, tolerance_mm):
    axis = _vector(axis_world, "axis_world")
    if not any(axis):
        raise ValueError("axis_world must be nonzero")
    angle = _number(angle_degrees, "angle_degrees")
    if not -360 <= angle <= 360:
        raise ValueError("angle_degrees must be in [-360, 360]")
    return {
        "pattern_uuid": _uuid(pattern_uuid),
        "axis_world": axis,
        "angle_degrees": angle,
        "pivot_world_mm": _vector(pivot_world_mm, "pivot_world_mm"),
        "tolerance_mm": _tolerance(tolerance_mm),
    }


def register(mcp, context) -> None:
    """Register tools using the coordination context.call contract."""

    @mcp.tool()
    def md_transform_probe(pattern_uuid: str | None = None) -> dict:
        """Read actual transform symbols/docs and optional current UUID index.

        Does not call a setter, load/save a project, or infer a world contract
        from symbol presence. The default native world writer is unavailable.
        """
        if pattern_uuid is not None:
            _uuid(pattern_uuid)
        return context.call(
            "transforms.probe", {"pattern_uuid": pattern_uuid}, mutation=False
        )

    @mcp.tool()
    def md_translate_pattern_world(
        pattern_uuid: str,
        delta_mm: list[float],
        tolerance_mm: float = 0.002,
        request_id: str | None = None,
        scene_token: str | None = None,
    ) -> dict:
        """Translate one current pattern by [dx, dy, dz] in WORLD millimetres.

        delta_mm has exactly three finite numbers. Executes once only when a
        native world backend is configured; verifies every target vertex and
        unchanged non-target geometry/invariants. Never uses arrangement
        offsets, project import/export, simulation, or automatic restoration.
        """
        params = {
            "pattern_uuid": _uuid(pattern_uuid),
            "delta_mm": _vector(delta_mm, "delta_mm"),
            "tolerance_mm": _tolerance(tolerance_mm),
        }
        return context.call(
            "transforms.translate", params, mutation=True,
            request_id=request_id, scene_token=scene_token,
        )

    @mcp.tool()
    def md_rotate_pattern_world(
        pattern_uuid: str,
        axis_world: list[float],
        angle_degrees: float,
        pivot_world_mm: list[float],
        tolerance_mm: float = 0.002,
        request_id: str | None = None,
        scene_token: str | None = None,
        ring_plan: WorldRotationRingPlan | None = None,
        verification_profile: Literal['strict', 'positions'] = 'strict',
        include_assigned_topstitch: bool = False,
    ) -> dict:
        """Right-hand rotate a whole pattern around an explicit WORLD axis/pivot.

        axis_world is a nonzero three-number direction, normalized internally.
        angle_degrees is finite and in [-360, 360]; pivot_world_mm is an
        explicit three-number WORLD point in mm, never an inferred centroid.
        For the ring route, capture the selected pattern with include_mesh and
        supply that request ID, physical PNG ROI and three calibrated logical
        viewport points. Omit ring_color for blue/Z. Red/X and green/Y require
        a color-qualified path from the current frame. The positions profile
        verifies the same vertex tolerance and preservation of UV, topology
        and other groups. It also reports the separate normal error.
        For a calibrated enlarged red path, supply gizmo_scale_steps=2 and
        expanded_ring_support_sha256 from the qualified enlarged frame.
        Enable include_assigned_topstitch to refresh one bound Texture Topstitch
        and verify both groups against the same requested matrix.
        """
        params = _rotation_params(pattern_uuid, axis_world, angle_degrees, pivot_world_mm, tolerance_mm)
        if verification_profile not in ('strict', 'positions') or verification_profile == 'positions' and ring_plan is None:
            raise ValueError('The positions profile requires a current rotation ring plan')
        if type(include_assigned_topstitch) is not bool or include_assigned_topstitch and ring_plan is None:
            raise ValueError('Assigned Topstitch requires a current rotation ring plan')
        if ring_plan is not None:
            required = {"frame_request_id", "roi_px", "start", "activation", "end"}
            scaled_keys = {'gizmo_scale_steps', 'expanded_ring_support_sha256'}
            if (type(ring_plan) is not dict or not required <= ring_plan.keys() <= required | {'ring_color'} | scaled_keys
                    or ring_plan.get('ring_color', 'blue') not in ('red', 'green', 'blue')
                    or scaled_keys & ring_plan.keys() and (not scaled_keys <= ring_plan.keys() or ring_plan['gizmo_scale_steps'] != 2
                        or ring_plan.get('ring_color') != 'red' or abs(params['angle_degrees']) > 5 or include_assigned_topstitch
                        or type(ring_plan['expanded_ring_support_sha256']) is not str
                        or re.fullmatch('[0-9a-f]{64}', ring_plan['expanded_ring_support_sha256']) is None)
                    or type(scene_token) is not str or re.fullmatch("[0-9a-f]{64}", scene_token) is None):
                raise ValueError("Use a complete ring plan and the captured current scene_token")
            params.update(ring_plan=dict(ring_plan), scene_token=scene_token)
        if verification_profile != 'strict':
            params['verification_profile'] = verification_profile
        if include_assigned_topstitch:
            params['include_assigned_topstitch'] = True
        return context.call(
            "transforms.rotate", params, mutation=True,
            request_id=request_id, scene_token=scene_token,
        )

