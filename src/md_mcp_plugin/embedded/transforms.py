"""Whole-pattern WORLD/mm controller; only stdlib is imported at load time.

No native generic writer is presently bundled. A trusted embedded initializer
may call install_world_backend() after supplying a real native adapter. Tool
params cannot install adapters, choose DLLs, execute code, or inject snapshots.

Adapter v1:
  describe() -> dict with schema="md-world-transform-backend-v1", space="world",
    unit="mm", no_system_input=True, no_focus_change=True, no_project_io=True,
    no_simulation_change=True.
  read_scene() -> {scene_identity: nonempty dict,
    scene_invariants_fingerprint: nonblank str,
    patterns: {UUID: {positions_mm: nonempty [[x,y,z],...],
      normals: nonempty [[x,y,z],...], topology_fingerprint: nonblank str,
      invariants_fingerprint: nonblank str}}}.
  apply_transform(pattern_uuid, pattern_index, matrix_world_mm, *,
    expected_scene_identity, expected_topology_fingerprint) -> any.

read_scene must freshly read actual ordered vertices/normals, independent of
the requested matrix. Fingerprints cover ordered topology, 2D/rest data, UV,
materials/sewing/settings and non-target scene state. The adapter must resolve
the native target without assuming SDK runtime index == native mesh index.
It must reject scene/topology drift against the passed expectations immediately
at the native write boundary; it must not silently accept stale UUID mappings.
The index passed here is ONLY the freshly resolved SDK pattern-list index.
The root context serializes operations, owns scene tokens and journaling.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math

_BACKEND = None
_BACKEND_SCHEMA = "md-world-transform-backend-v1"
_GUARANTEES = (
    "no_system_input", "no_focus_change", "no_project_io",
    "no_simulation_change",
)


def install_world_backend(backend) -> None:
    """Trusted initialization hook, never registered as an operation/tool."""
    global _BACKEND
    if backend is not None and any(
        not callable(getattr(backend, name, None))
        for name in ("describe", "read_scene", "apply_transform")
    ):
        raise TypeError("World backend requires describe/read_scene/apply_transform")
    _BACKEND = backend


def _number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be numeric, excluding bool")
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be finite") from None
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _vector(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} requires three finite numbers")
    return [_number(v, label) for v in value]


def _text(value, label):
    if (
        not isinstance(value, str) or not value or value.strip() != value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"{label} must be a nonblank exact string")
    return value


def _params(params, required):
    if not isinstance(params, dict) or set(params) != set(required):
        raise ValueError("Expected parameters: " + ", ".join(required))
    target = _text(params["pattern_uuid"], "pattern_uuid")
    if len(target) > 256:
        raise ValueError("pattern_uuid is too long")
    tolerance = _number(params["tolerance_mm"], "tolerance_mm")
    if not 0 < tolerance <= 0.002:
        raise ValueError("tolerance_mm must be > 0 and <= 0.002 mm")
    return target, tolerance


def translation_matrix(delta_mm):
    delta = _vector(delta_mm, "delta_mm")
    return [
        [1.0, 0.0, 0.0, delta[0]],
        [0.0, 1.0, 0.0, delta[1]],
        [0.0, 0.0, 1.0, delta[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def rotation_matrix(axis_world, angle_degrees, pivot_world_mm):
    """Column-vector convention: p' = R p + (pivot - R pivot)."""
    axis = _vector(axis_world, "axis_world")
    angle = _number(angle_degrees, "angle_degrees")
    if not -360 <= angle <= 360:
        raise ValueError("angle_degrees must be in [-360, 360]")
    pivot = _vector(pivot_world_mm, "pivot_world_mm")
    scale = max(abs(v) for v in axis)
    if scale == 0:
        raise ValueError("axis_world must be nonzero")
    scaled = [v / scale for v in axis]
    length = math.hypot(*scaled)
    x, y, z = [v / length for v in scaled]
    # Exact multiples of 360 are semantic no-ops, without float sine residue.
    radians = math.radians(math.remainder(angle, 360.0))
    c, s = math.cos(radians), math.sin(radians)
    t = 1.0 - c
    rows = [
        [t*x*x+c, t*x*y-s*z, t*x*z+s*y],
        [t*x*y+s*z, t*y*y+c, t*y*z-s*x],
        [t*x*z-s*y, t*y*z+s*x, t*z*z+c],
    ]
    result = [row + [pivot[i] - math.fsum(
        row[j] * pivot[j] for j in range(3)
    )] for i, row in enumerate(rows)]
    result.append([0.0, 0.0, 0.0, 1.0])
    if any(not math.isfinite(v) for row in result for v in row):
        raise ValueError("rotation produces nonfinite matrix values")
    return result


def _apply(matrix, point, normal=False):
    point = _vector(point, "normal" if normal else "position_mm")
    result = [
        math.fsum(row[j] * point[j] for j in range(3))
        + (0.0 if normal else row[3])
        for row in matrix[:3]
    ]
    if any(not math.isfinite(v) for v in result):
        raise ValueError("transform produces nonfinite coordinates")
    return result


def _runtime_patterns():
    p = importlib.import_module("pattern_api")
    count = p.GetPatternCount()
    if type(count) is not int or not 0 <= count <= 10000:
        raise ValueError("Invalid current pattern count")
    result = {}
    for index in range(count):
        info = p.GetPatternInformation(index)
        if isinstance(info, str):
            info = json.loads(info)
        if not isinstance(info, dict):
            raise ValueError("Pattern information is not an object")
        target = _text(info.get("uuid"), "runtime pattern uuid")
        if target in result:
            raise ValueError("Ambiguous duplicate pattern UUID in current project")
        result[target] = index
    return result


def _resolve(target):
    current = _runtime_patterns()
    if target not in current:
        raise ValueError("pattern_uuid is absent from the current project")
    return current[target], current


def _scene(value, expected_uuids):
    if not isinstance(value, dict):
        raise ValueError("World readback must be an object")
    if not isinstance(value.get("scene_identity"), dict) or not value["scene_identity"]:
        raise ValueError("World readback requires a bound scene_identity")
    _text(value.get("scene_invariants_fingerprint"), "scene invariants fingerprint")
    patterns = value.get("patterns")
    if not isinstance(patterns, dict) or set(patterns) != set(expected_uuids):
        raise ValueError("World readback UUID set differs from current project")
    # Detach arrays so an adapter cannot overwrite the before snapshot in place.
    result = {
        "scene_identity": json.loads(json.dumps(value["scene_identity"], allow_nan=False)),
        "scene_invariants_fingerprint": value["scene_invariants_fingerprint"],
        "patterns": {},
    }
    for target, row in patterns.items():
        if not isinstance(row, dict):
            raise ValueError("Each pattern readback must be an object")
        clean = {}
        for key in ("topology_fingerprint", "invariants_fingerprint"):
            clean[key] = _text(row.get(key), key)
        for key in ("positions_mm", "normals"):
            values = row.get(key)
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError(f"{key} requires a complete nonempty ordered array")
            clean[key] = [_vector(v, key) for v in values]
        if any(abs(math.hypot(*v) - 1) > 1e-5 for v in clean["normals"]):
            raise ValueError("Readback normals must be unit world vectors")
        result["patterns"][target] = clean
    return result


def _distance(a, b):
    return math.dist(a, b)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def compare_world_readback(before, after, pattern_uuid, matrix, tolerance_mm):
    """Compare independent, validated snapshots; never fit a different transform."""
    checks = {
        "scene_identity_unchanged": before["scene_identity"] == after["scene_identity"],
        "scene_invariants_unchanged": (
            before["scene_invariants_fingerprint"] == after["scene_invariants_fingerprint"]
        ),
        "same_pattern_uuids": set(before["patterns"]) == set(after["patterns"]),
        "topology_and_pattern_invariants_unchanged": True,
        "other_patterns_positions_and_normals_exact": True,
        "target_array_counts_unchanged": True,
    }
    for target, row in before["patterns"].items():
        current = after["patterns"].get(target)
        if current is None:
            checks["topology_and_pattern_invariants_unchanged"] = False
            continue
        if any(row[k] != current[k] for k in ("topology_fingerprint", "invariants_fingerprint")):
            checks["topology_and_pattern_invariants_unchanged"] = False
        if target != pattern_uuid and any(
            row[k] != current[k] for k in ("positions_mm", "normals")
        ):
            checks["other_patterns_positions_and_normals_exact"] = False
    source = before["patterns"][pattern_uuid]
    current = after["patterns"].get(pattern_uuid)
    same_counts = current is not None and all(
        len(source[k]) == len(current[k]) for k in ("positions_mm", "normals")
    )
    checks["target_array_counts_unchanged"] = same_counts
    maximum_error = maximum_normal_error = maximum_motion = None
    delta_min = delta_max = None
    if same_counts:
        expected = [_apply(matrix, v) for v in source["positions_mm"]]
        maximum_error = max(_distance(a, b) for a, b in zip(expected, current["positions_mm"]))
        maximum_normal_error = max(
            _distance(_apply(matrix, a, normal=True), b)
            for a, b in zip(source["normals"], current["normals"])
        )
        deltas = [
            [b[i] - a[i] for i in range(3)]
            for a, b in zip(source["positions_mm"], current["positions_mm"])
        ]
        delta_min = [min(row[i] for row in deltas) for i in range(3)]
        delta_max = [max(row[i] for row in deltas) for i in range(3)]
        maximum_motion = max(math.hypot(*row) for row in deltas)
    checks.update({
        "every_target_vertex_matches_requested_matrix": (
            maximum_error is not None and maximum_error <= tolerance_mm
        ),
        "every_target_normal_matches_requested_rotation": (
            maximum_normal_error is not None and maximum_normal_error <= 1e-5
        ),
        "observed_motion_exceeds_tolerance": (
            maximum_motion is not None and maximum_motion > tolerance_mm
        ),
    })
    return {
        "verified": all(checks.values()),
        "checks": checks,
        "maximum_position_error_mm": maximum_error,
        "maximum_normal_error": maximum_normal_error,
        "maximum_observed_displacement_mm": maximum_motion,
        "actual_delta_min_mm": delta_min,
        "actual_delta_max_mm": delta_max,
        "target_vertex_count": len(source["positions_mm"]),
        "before_target_sha256": _digest(source),
        "after_target_sha256": _digest(current) if current is not None else None,
    }


def _backend_description():
    if _BACKEND is None:
        return None
    description = _BACKEND.describe()
    if (
        not isinstance(description, dict)
        or description.get("schema") != _BACKEND_SCHEMA
        or description.get("space") != "world" or description.get("unit") != "mm"
        or any(description.get(k) is not True for k in _GUARANTEES)
    ):
        raise ValueError("Backend lacks the explicit world/mm/no-input/no-project-I/O contract")
    return json.loads(json.dumps(description, allow_nan=False))


def _execute(target, matrix, tolerance):
    index, uuids = _resolve(target)
    base = {
        "pattern_uuid": target,
        "sdk_pattern_index": index,
        "space": "world", "unit": "mm",
        "matrix_world_mm": matrix,
        "tolerance_mm": tolerance,
        "write_attempted": False,
        "verified": False,
        "live_validation": "pending",
    }
    if _BACKEND is None:
        return dict(base, status="unsupported", error={
            "code": "WORLD_TRANSFORM_BACKEND_UNAVAILABLE",
            "message": (
                "Missing native UUID-bound world/mm writer and independent ordered "
                "world-mesh readback. Arrangement offsets and 2D position are not substitutes."
            ),
        }, requires_recovery=False, retry_safe=True)
    description = _backend_description()
    before = _scene(_BACKEND.read_scene(), uuids)
    is_identity = matrix == translation_matrix([0, 0, 0])
    if is_identity:
        return dict(base, status="no_op", backend=description, retry_safe=True,
                    requires_recovery=False, reason="Identity world transform; no setter called.")
    expected_motion = max(
        _distance(v, _apply(matrix, v))
        for v in before["patterns"][target]["positions_mm"]
    )
    if expected_motion <= 2 * tolerance:
        raise ValueError("Expected motion is too small to distinguish from tolerance; reduce tolerance_mm")
    # Resolve afresh immediately before the single write; never cache indices.
    index, current = _resolve(target)
    if set(current) != set(uuids):
        raise ValueError("Current UUID set changed before mutation")
    if _scene(_BACKEND.read_scene(), current) != before:
        raise ValueError("World scene changed between preflight and write; nothing written")
    base["sdk_pattern_index"] = index
    base["write_attempted"] = True
    base["retry_safe"] = False
    write_error = None
    try:
        returned = _BACKEND.apply_transform(
            target, index, matrix,
            expected_scene_identity=before["scene_identity"],
            expected_topology_fingerprint=before["patterns"][target]["topology_fingerprint"],
        )
        base["native_return_repr"] = repr(returned)[:512]
        if returned is False:
            write_error = "Native backend returned False"
    except Exception as exc:
        write_error = f"{type(exc).__name__}: {exc}"
    # Even a False/exception can occur after mutation: always attempt readback.
    try:
        after = _scene(_BACKEND.read_scene(), _runtime_patterns())
        proof = compare_world_readback(before, after, target, matrix, tolerance)
    except Exception as exc:
        return dict(base, status="error", requires_recovery=True, error={
            "code": "WORLD_TRANSFORM_READBACK_UNAVAILABLE",
            "message": f"{type(exc).__name__}: {exc}",
            "native_error": write_error,
        })
    if proof["verified"] and write_error is None:
        return dict(base, status="succeeded", verified=True, readback=proof,
                    backend=description, requires_recovery=False)
    return dict(base, status="error", readback=proof, requires_recovery=True, error={
        "code": "NATIVE_WORLD_WRITE_FAILED" if write_error else "WORLD_TRANSFORM_POSTCONDITION_FAILED",
        "message": write_error or "Actual readback does not match the requested world transform/invariants.",
    })


def translate(params):
    target, tolerance = _params(params, ("pattern_uuid", "delta_mm", "tolerance_mm"))
    return _execute(target, translation_matrix(params["delta_mm"]), tolerance)


def rotate(params):
    required = ("pattern_uuid", "axis_world", "angle_degrees", "pivot_world_mm", "tolerance_mm")
    ring_keys = {*required, 'ring_plan', 'scene_token'}
    if isinstance(params, dict) and ring_keys <= params.keys() <= ring_keys | {'verification_profile', 'include_assigned_topstitch'}:
        from .dispatch import _module

        target, tolerance = _params({key: params[key] for key in required}, required)
        matrix = rotation_matrix(params['axis_world'], params['angle_degrees'], params['pivot_world_mm'])
        _module('world_adapter')
        _module('world_frame')
        return _module('world_rotation_adapter').rotate(target, matrix, tolerance, params['scene_token'], params['ring_plan'],
            verification_profile=params.get('verification_profile', 'strict'),
            include_assigned_topstitch=params.get('include_assigned_topstitch', False))
    target, tolerance = _params(
        params, required,
    )
    matrix = rotation_matrix(
        params["axis_world"], params["angle_degrees"], params["pivot_world_mm"]
    )
    return _execute(target, matrix, tolerance)


def probe(params):
    if not isinstance(params, dict) or set(params) not in (set(), {"pattern_uuid"}):
        raise ValueError("probe accepts only optional pattern_uuid")
    target = params.get("pattern_uuid")
    resolved = None
    if target is not None:
        target = _text(target, "pattern_uuid")
        if len(target) > 256:
            raise ValueError("pattern_uuid is too long")
        resolved, _ = _resolve(target)
    names = {
        "pattern_api": (
            "GetPatternCount", "GetPatternInformation", "GetPatternPiecePos",
            "SetPatternPieceMove", "SetPatternPiecePos",
            "GetArrangementOfPattern", "SetArrangementPosition", "SetArrangementOrientation",
            "GetMeshCountByType", "GetMeshVertexPositions", "SetMeshVertexPositions",
            "SetPattern3DTransform", "SetPatternWorldTransform",
        ),
        "utility_api": ("GetClothPositions", "SetClothPositions"),
    }
    symbols = {}
    for module_name, candidates in names.items():
        module = importlib.import_module(module_name)
        rows = {}
        # Metadata only: no candidate function, setter or getter is invoked.
        for name in candidates:
            value = getattr(module, name, None)
            signature = None
            if callable(value):
                try:
                    signature = str(inspect.signature(value))
                except (ValueError, TypeError):
                    pass
            rows[name] = {
                "available": callable(value),
                "signature": signature,
                "doc": (getattr(value, "__doc__", "") or "")[:2400] if callable(value) else None,
            }
        symbols[module_name] = rows
    return {
        "status": "succeeded", "write_attempted": False,
        "pattern_uuid": target, "sdk_pattern_index": resolved,
        "symbols": symbols, "backend": _backend_description(),
        "world_transform_status": "unsupported" if _BACKEND is None else "unverified",
        "symbol_presence_is_execution_proof": False,
        "live_validation": "pending",
        "missing_layer": (
            "A parameterized native world affine writer with UUID/native-index mapping, "
            "and independent per-pattern ordered world mesh/invariant readback."
        ) if _BACKEND is None else None,
    }


OPERATIONS = {
    "transforms.probe": probe,
    "transforms.translate": translate,
    "transforms.rotate": rotate,
}

