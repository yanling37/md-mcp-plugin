"""Embedded cloth diagnostics and reusable math; no generic native writer yet.

Snapshot helpers consume supplied data. They neither acquire MD snapshots nor
certify an OBJ/global-array index as a native simulation-vertex index.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import re
from fractions import Fraction

from md_mcp_plugin.errors import PluginError


_REF_KEYS = {"kind", "pattern_uuid", "scene_token", "topology_fingerprint", "snapshot_id", "vertex_index"}


def _text(value, label):
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 512:
        raise ValueError(label + " must be a nonempty, unpadded string of at most 512 characters")
    return value


def _number(value, label):
    if type(value) not in (int, float):
        raise ValueError(label + " must be finite")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(label + " must be finite") from None
    if not math.isfinite(result):
        raise ValueError(label + " must be finite")
    return result


def _vector(value, dimension, label):
    if not isinstance(value, (list, tuple)) or len(value) != dimension:
        raise ValueError(label + " has the wrong dimension")
    return [_number(v, label) for v in value]


def _hash(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def make_mesh_snapshot(pattern_uuid, scene_token, positions_mm, faces):
    """Fingerprint supplied owner-local mesh order, not any native setter order.

    Core must separately establish world-mm units, live scene identity, owner
    mapping and capture freshness before using these calculations as evidence.
    """
    _text(pattern_uuid, "pattern_uuid")
    _text(scene_token, "scene_token")
    if not isinstance(positions_mm, list) or not positions_mm:
        raise ValueError("A nonempty position list is required")
    xyz = [_vector(v, 3, "position_mm") for v in positions_mm]
    if not isinstance(faces, list) or not faces:
        raise ValueError("Nonempty face connectivity is required; vertex count alone is not topology")
    topology = []
    for face in faces:
        if not isinstance(face, (list, tuple)) or len(face) < 3:
            raise ValueError("Each face needs at least three indices")
        if any(type(i) is not int or not 0 <= i < len(xyz) for i in face) or len(set(face)) != len(face):
            raise ValueError("Face indices must be distinct and within the supplied vertex list")
        topology.append(list(face))
    fingerprint = _hash({"pattern_uuid": pattern_uuid, "vertex_count": len(xyz), "faces": topology})
    result = {
        "pattern_uuid": pattern_uuid, "scene_token": scene_token,
        "positions_mm": xyz, "faces": topology, "topology_fingerprint": fingerprint,
        "evidence_kind": "supplied_mesh_data", "native_vertex_mapping_verified": False,
    }
    result["snapshot_id"] = _hash(result)
    return result


def _snapshot(value):
    if not isinstance(value, dict):
        raise ValueError("A complete mesh snapshot is required")
    try:
        rebuilt = make_mesh_snapshot(value["pattern_uuid"], value["scene_token"], value["positions_mm"], value["faces"])
    except KeyError as exc:
        raise ValueError("Incomplete mesh snapshot") from exc
    if rebuilt != value:
        raise ReferenceError("STALE_REFERENCE: snapshot contents/fingerprint differ")
    return rebuilt


def _reference(ref, pattern_uuid, scene_token):
    if not isinstance(ref, dict) or set(ref) != _REF_KEYS or ref["kind"] != "cloth_vertex_snapshot_v1":
        raise ValueError("A complete cloth snapshot vertex reference is required")
    if ref["pattern_uuid"] != pattern_uuid or ref["scene_token"] != scene_token:
        raise ReferenceError("STALE_REFERENCE: owner or scene changed")
    for key in ("topology_fingerprint", "snapshot_id"):
        if not isinstance(ref[key], str) or re.fullmatch(r"sha256:[0-9a-f]{64}", ref[key]) is None:
            raise ValueError(key + " must be a SHA-256 fingerprint")
    if type(ref["vertex_index"]) is not int or ref["vertex_index"] < 0:
        raise ValueError("vertex_index must be a nonnegative snapshot index")
    return ref


def vertex_reference(snapshot, vertex_index):
    """Issue an owner/scene/topology/pose-bound supplied-data reference."""
    snapshot = _snapshot(snapshot)
    if type(vertex_index) is not int or not 0 <= vertex_index < len(snapshot["positions_mm"]):
        raise ValueError("vertex_index is outside the supplied mesh")
    return {
        "kind": "cloth_vertex_snapshot_v1", "pattern_uuid": snapshot["pattern_uuid"],
        "scene_token": snapshot["scene_token"], "topology_fingerprint": snapshot["topology_fingerprint"],
        "snapshot_id": snapshot["snapshot_id"], "vertex_index": vertex_index,
    }


def resolve_snapshot_vertex(snapshot, ref):
    snapshot = _snapshot(snapshot)
    _reference(ref, snapshot["pattern_uuid"], snapshot["scene_token"])
    index = ref["vertex_index"]
    if (ref["topology_fingerprint"] != snapshot["topology_fingerprint"]
            or ref["snapshot_id"] != snapshot["snapshot_id"] or index >= len(snapshot["positions_mm"])):
        raise ReferenceError("STALE_REFERENCE: topology, pose or snapshot index changed")
    return index


def remaining_delta(original_xyz_mm, measured_xyz_mm, goal_delta_xyz_mm):
    """Recompute the next-plane residual from measured XYZ, never the intended first-plane delta."""
    original = _vector(original_xyz_mm, 3, "original_xyz_mm")
    measured = _vector(measured_xyz_mm, 3, "measured_xyz_mm")
    delta = _vector(goal_delta_xyz_mm, 3, "goal_delta_xyz_mm")
    absolute = [Fraction(str(a)) + Fraction(str(b)) for a, b in zip(original, delta)]
    residual = [a - Fraction(str(b)) for a, b in zip(absolute, measured)]
    return {"absolute_goal_xyz_mm": [float(v) for v in absolute],
            "remaining_delta_xyz_mm": [float(v) for v in residual],
            "remaining_delta_exact": [str(v) for v in residual], "native_execution_verified": False}


def _camera(camera):
    if not isinstance(camera, dict):
        raise ValueError("A supplied calibrated camera is required")
    right, up, forward = [_vector(camera.get(k), 3, k) for k in ("right", "up", "forward")]
    axes = (right, up, forward)
    if any(abs(_dot(a, b) - float(i == j)) > 1e-6 for i, a in enumerate(axes) for j, b in enumerate(axes)):
        raise ValueError("Camera axes must be orthonormal, with nonzero unit axes")
    if abs(_dot(_cross(right, up), forward) - 1.0) > 1e-6:
        raise ValueError("Camera basis must be right-handed")
    center = _vector(camera.get("camera_center_world_mm"), 3, "camera_center_world_mm")
    principal = _vector(camera.get("center_px"), 2, "center_px")
    focal = _number(camera.get("focal_px"), "focal_px")
    dimensions = camera.get("viewport_size_px")
    if focal <= 0 or not isinstance(dimensions, (list, tuple)) or len(dimensions) != 2:
        raise ValueError("Positive focal_px and two viewport dimensions are required")
    if any(type(v) is not int or v <= 0 for v in dimensions):
        raise ValueError("Viewport dimensions must be positive integers")
    return right, up, forward, center, principal, focal, dimensions


def _project(camera_values, xyz):
    right, up, forward, center, principal, focal, _ = camera_values
    offset = [a - b for a, b in zip(xyz, center)]
    depth = -_dot(offset, forward)
    if not math.isfinite(depth) or depth <= 0:
        raise ValueError("Point lies at or behind the camera")
    projected = [principal[0] + focal * _dot(offset, right) / depth,
                 principal[1] - focal * _dot(offset, up) / depth]
    return _vector(projected, 2, "projected pixel")


def plan_projected_delta(snapshot, ref, goal_delta_xyz_mm, camera, click_anchor_px, activation_delta_px=(-12.0, 0.0), margin_px=8.0):
    """Relative projection with an independently qualified selection/gizmo anchor.

    The supplied anchor is never asserted to equal the physical vertex pixel.
    A generic native adapter still needs fresh frame/selection/path evidence.
    """
    snapshot = _snapshot(snapshot)
    index = resolve_snapshot_vertex(snapshot, ref)
    delta = _vector(goal_delta_xyz_mm, 3, "goal_delta_xyz_mm")
    values = _camera(camera)
    anchor = _vector(click_anchor_px, 2, "click_anchor_px")
    activation = _vector(activation_delta_px, 2, "activation_delta_px")
    margin = _number(margin_px, "margin_px")
    width, height = values[-1]
    if margin < 0 or 2 * margin >= min(width, height):
        raise ValueError("margin_px does not fit the viewport")
    source = snapshot["positions_mm"][index]
    endpoint = _vector([a + b for a, b in zip(source, delta)], 3, "target_xyz_mm")
    start_projection, end_projection = _project(values, source), _project(values, endpoint)
    screen_delta = [b - a for a, b in zip(start_projection, end_projection)]
    points = {"start": anchor, "activation": [a + b for a, b in zip(anchor, activation)],
              "end": [a + b for a, b in zip(anchor, screen_delta)]}
    points = {key: _vector(value, 2, key) for key, value in points.items()}
    inside = all(margin <= p[0] <= width - 1 - margin and margin <= p[1] <= height - 1 - margin for p in points.values())
    in_plane = abs(_dot(delta, values[2])) <= 1e-6
    return {"vertex_ref": dict(ref), "target_xyz_mm": endpoint, "viewport_points": points,
            "projected_source_px": start_projection, "projected_target_px": end_projection,
            "relative_delta_px": screen_delta, "all_points_inside_viewport": inside,
            "goal_in_camera_plane": in_plane, "math_plan_valid": inside and in_plane,
            "dispatch_qualified": False, "selection_verified": False, "camera_registration_verified": False,
            "native_vertex_mapping_verified": False, "native_execution_verified": False}


def _targets(pattern_uuid, scene_token, targets, tolerance_mm):
    _text(pattern_uuid, "pattern_uuid")
    _text(scene_token, "scene_token")
    if _number(tolerance_mm, "tolerance_mm") <= 0:
        raise ValueError("tolerance_mm must be positive")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 4096:
        raise ValueError("targets must contain 1..4096 vertices")
    seen, snapshots = set(), set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"vertex_ref", "position_mm"}:
            raise ValueError("Each target requires vertex_ref and position_mm")
        ref = _reference(target["vertex_ref"], pattern_uuid, scene_token)
        if ref["vertex_index"] in seen:
            raise ValueError("Duplicate target vertex")
        seen.add(ref["vertex_index"])
        snapshots.add((ref["snapshot_id"], ref["topology_fingerprint"]))
        _vector(target["position_mm"], 3, "position_mm")
    if len(snapshots) != 1:
        raise ValueError("All targets must belong to one mesh snapshot")


def measure_position_readback(before, after, targets, tolerance_mm=0.002):
    """Compare complete supplied meshes; success is math-only, never a live MD receipt."""
    before, after = _snapshot(before), _snapshot(after)
    _targets(before["pattern_uuid"], before["scene_token"], targets, tolerance_mm)
    if any(before[k] != after[k] for k in ("pattern_uuid", "scene_token", "topology_fingerprint")):
        raise ReferenceError("STALE_REFERENCE: readback owner, scene or topology differs")
    selected, measurements = set(), []
    for target in targets:
        index = resolve_snapshot_vertex(before, target["vertex_ref"])
        selected.add(index)
        expected = _vector(target["position_mm"], 3, "position_mm")
        actual = after["positions_mm"][index]
        residual = [Fraction(str(b)) - Fraction(str(a)) for a, b in zip(actual, expected)]
        measurements.append({"vertex_index": index, "actual_xyz_mm": actual,
                             "actual_delta_xyz_mm": [a - b for a, b in zip(actual, before["positions_mm"][index])],
                             "remaining_delta_xyz_mm": [float(v) for v in residual],
                             "maximum_component_error_mm": float(max(abs(v) for v in residual))})
    untouched = all(a == b for i, (a, b) in enumerate(zip(before["positions_mm"], after["positions_mm"])) if i not in selected)
    maximum = max(row["maximum_component_error_mm"] for row in measurements)
    return {"mathematical_pass": maximum <= tolerance_mm and untouched,
            "topology_unchanged": True, "unselected_positions_exact": untouched,
            "target_positions_within_tolerance": maximum <= tolerance_mm,
            "maximum_component_error_mm": maximum, "measurements": measurements,
            "evidence_kind": "supplied_mesh_comparison", "native_execution_verified": False,
            "other_patterns_checked": False, "solver_rest_verified": False, "persistence_verified": False}


def _reply(status, result=None, code=None, message=None):
    # The shared dispatcher supplies the outer result/error/operation_id envelope.
    reply = dict(result or {})
    reply.update(status=status.upper(), supported=status != "unsupported", verified=status == "succeeded")
    if code is not None:
        reply["error"] = {"code": code, "message": message}
    return reply


def _unsupported(code, message, **details):
    return _reply("unsupported", {"mutation_attempted": False, "native_writer_verified": False, **details}, code, message)


def _keys(params, required=(), optional=()):
    if not isinstance(params, dict) or set(params) - set(required) - set(optional) or set(required) - set(params):
        raise ValueError("Unexpected or missing operation parameters")


def _pattern_identities():
    api = importlib.import_module("pattern_api")
    count = api.GetPatternCount()
    if type(count) is not int or count < 0:
        raise ValueError("GetPatternCount did not return a nonnegative integer")
    identities = []
    for index in range(count):
        info = json.loads(api.GetPatternInformation(index))
        if not isinstance(info, dict):
            raise ValueError("GetPatternInformation did not return an object")
        identities.append(_text(info.get("uuid"), "runtime pattern UUID"))
    return identities


def _resolve_pattern(pattern_uuid):
    _text(pattern_uuid, "pattern_uuid")
    identities = _pattern_identities()
    matches = [i for i, value in enumerate(identities) if value == pattern_uuid]
    if not matches:
        raise ReferenceError("PATTERN_NOT_FOUND: UUID is absent from the current project")
    if len(matches) != 1:
        raise ReferenceError("AMBIGUOUS_PATTERN: UUID occurs more than once")
    return matches[0]


def _probe_runtime(params):
    _keys(params)
    modules = {}
    for name in ("pattern_api", "utility_api"):
        try:
            module = importlib.import_module(name)
        except ImportError:
            modules[name] = {"available": False, "symbols": []}
            continue
        rows = []
        for symbol in sorted(dir(module)):
            if symbol.startswith("_") or re.search(
                r"cloth|pinch|solidify|rest_?(?:shape|positions?|lengths?|constraints?|state|pose|mesh|vertices|vertex|override)",
                symbol, re.I,
            ) is None:
                continue
            value = getattr(module, symbol)
            try:
                signature = str(inspect.signature(value))
            except (ValueError, TypeError):
                signature = None
            doc = getattr(value, "__doc__", None)
            rows.append({"name": symbol, "callable": callable(value), "signature": signature,
                         "docstring": doc[:4096] if isinstance(doc, str) else None,
                         "docstring_truncated": isinstance(doc, str) and len(doc) > 4096})
        modules[name] = {"available": True, "symbols": rows}
    usd_metadata = importlib.import_module('md_mcp_plugin.embedded.cloth_usd_export_metadata')
    return _reply("succeeded", {"modules": modules, "metadata_only": True,
                               "native_XYZ_writer_verified": False, "solver_rest_writer_verified": False,
                               "usd_export_metadata": usd_metadata.probe()})


def _native_positions_once():
    getter = getattr(importlib.import_module("utility_api"), "GetClothPositions", None)
    if not callable(getter):
        return None, _unsupported("CLOTH_GETTER_UNAVAILABLE", "utility_api.GetClothPositions is unavailable", getter_calls=0)
    positions = []
    try:
        returned = getter(positions)
        if returned is not None:
            return None, _unsupported("GETTER_BINDING_CONTRACT_DIFFERENT", "Expected GetClothPositions(output_list) -> None", getter_calls=1)
        if len(positions) % 3:
            raise ValueError("GetClothPositions output length is not a multiple of three")
        values = [_number(v, "GetClothPositions output") for v in positions]
    except (TypeError, ValueError, OverflowError, RuntimeError) as exc:
        return None, _reply("rejected", {"getter_calls": 1}, "CLOTH_GETTER_INVALID_OUTPUT", str(exc))
    if not values:
        return None, _unsupported("EMPTY_OR_UNEXPOSED_GETTER_OUTPUT", "Python output list is empty; empty cloth and unavailable list copy-back cannot be distinguished", getter_calls=1)
    return values, None


def _read_scene_positions(params):
    _keys(params, optional=("sample_count",))
    samples = params.get("sample_count", 4)
    if type(samples) is not int or not 0 <= samples <= 32:
        raise ValueError("sample_count must be an integer in [0, 32]")
    before = _pattern_identities()
    values, failure = _native_positions_once()
    if _pattern_identities() != before:
        raise ReferenceError("SCENE_CHANGED: pattern identity/order changed during the getter")
    if failure is not None:
        return failure
    return _reply("succeeded", {
        "source": "utility_api.GetClothPositions(output_list)", "scope": "scene-wide diagnostic",
        "getter_calls": 1,
        "vertex_count": len(values) // 3, "array_fingerprint": _hash(values),
        "positions_sample_native_units": [values[i:i + 3] for i in range(0, min(len(values), samples * 3), 3)],
        "sample_count": min(len(values) // 3, samples), "units": "native_unverified",
        "pattern_uuids": before, "owner_vertex_mapping_verified": False,
        "editable_references_issued": False, "solver_rest_read": False,
    })


def _compare_native_getter(mesh, before, certificate):
    values, failure = _native_positions_once()
    if failure is not None:
        return {"status": failure["status"], "error": failure["error"], "getter_calls": failure["getter_calls"],
                "export_bracket_complete": False}
    report = {"getter_calls": 1, "getter_vertex_count": len(values) // 3, "getter_array_fingerprint": _hash(values),
              "export_bracket_complete": False, "coordinate_agreement_observed": False,
              "native_vertex_mapping_verified": False, "solver_rest_value_read": False}
    try:
        reply = mesh.OPERATIONS["mesh.read_scene"]({})
    except PluginError as exc:
        return {**report, "status": "REJECTED", "reason": "SECOND_WORLD_CAPTURE_UNAVAILABLE", "export_error": exc.as_dict()}
    if reply.get("status") != "SUCCEEDED" or reply.get("verified") is not True:
        return {**report, "status": "REJECTED", "reason": "SECOND_WORLD_CAPTURE_UNAVAILABLE", "export_error": reply.get("error")}
    after = reply["snapshot"]
    fields = ("scene_identity", "scene_token", "scope", "space", "unit", "axis_order",
              "topology_fingerprint", "face_labels", "uv", "positions_mm")
    report.update(before_capture_id=before["capture_id"], after_capture_id=after["capture_id"],
                  after_obj_asset=after["export"]["obj_asset"])
    changed = [field for field in fields if before[field] != after[field]]
    if before["capture_id"] == after["capture_id"] or changed:
        return {**report, "status": "REJECTED", "reason": "WORLD_CAPTURE_BRACKET_CHANGED",
                "changed_export_fields": changed}
    pattern_names = {pair["group_name"] for pair in certificate["pairs"]}
    pattern_indices = sorted({index for face, label in zip(before["faces"], before["face_labels"], strict=True)
                              if label["groups"][0] in pattern_names for index in face})
    positions = before["positions_mm"]
    report.update(export_bracket_complete=True, full_export_vertex_count=len(positions),
                  exact_label_pattern_vertex_count=len(pattern_indices))
    if len(values) == len(positions) * 3:
        expected = positions
        scope = "scene_OBJ_export_order"
    elif len(values) == len(pattern_indices) * 3:
        expected = [positions[index] for index in pattern_indices]
        scope = "exact_pattern_groups_in_scene_OBJ_order"
    else:
        return {**report, "status": "REJECTED", "reason": "GETTER_AND_EXPORT_VERTEX_COUNTS_DIFFER"}
    native_rows = zip(values[0::3], values[1::3], values[2::3], strict=True)
    max_error = max(math.dist(native, obj) for native, obj in zip(native_rows, expected, strict=True))
    return {**report, "status": "SUCCEEDED", "comparison_scope": scope,
            "max_coordinate_distance_mm_if_same_units": max_error if math.isfinite(max_error) else None,
            "comparison_tolerance_mm": 0.002, "coordinate_agreement_observed": max_error <= 0.002,
            "coincident_export_xyz_rows": len(expected) - len({tuple(row) for row in expected})}


def _get_positions(params):
    from md_mcp_plugin.embedded import common, mesh, world_adapter

    _keys(params, required=("pattern_uuid",), optional=("scene_token", "vertex_indices", "probe_native_getter", "include_vertex_refs"))
    pattern_uuid = _text(params["pattern_uuid"], "pattern_uuid")
    indices = params.get("vertex_indices")
    if indices is not None and (not isinstance(indices, list) or len(indices) > 4096
                                or any(type(i) is not int or i < 0 for i in indices) or len(set(indices)) != len(indices)):
        raise ValueError("vertex_indices requires at most 4096 unique nonnegative compact OBJ indices")
    probe = params.get("probe_native_getter", False)
    if type(probe) is not bool:
        raise ValueError("probe_native_getter must be boolean")
    include_refs = params.get("include_vertex_refs", False)
    if type(include_refs) is not bool:
        raise ValueError("include_vertex_refs must be boolean")
    current = common.scene()
    if params.get("scene_token") is not None and params["scene_token"] != current["scene_token"]:
        raise ReferenceError("STALE_SCENE: the requested scene changed before the cloth read")
    matches = [row for row in current["patterns"] if row["pattern_uuid"] == pattern_uuid]
    if not matches:
        raise ReferenceError("PATTERN_NOT_FOUND: UUID is absent from the current project")
    if len(matches) != 1:
        raise ReferenceError("AMBIGUOUS_PATTERN: UUID occurs more than once")
    target = matches[0]
    group_name = _text(target["information"].get("name"), "current pattern name")
    capture = mesh.OPERATIONS["mesh.read_scene"]({})
    if capture.get("status") != "SUCCEEDED" or capture.get("verified") is not True:
        return _unsupported("CLOTH_OBJ_REFERENCE_UNAVAILABLE", "A fresh scene OBJ export is required", export_error=capture.get("error"))
    exported = capture["snapshot"]
    try:
        certificate = world_adapter.bind_export_labels(current, exported, pattern_uuid, group_name)
    except world_adapter.AdapterError as exc:
        return _unsupported("CLOTH_EXPORT_LABEL_BINDING_UNAVAILABLE", str(exc), binding_error=exc.code,
                            obj_asset=exported["export"]["obj_asset"])
    group = mesh.extract_group(exported, group_name)
    positions = group["positions_mm"]
    if indices is not None and any(index >= len(positions) for index in indices):
        raise ValueError("Requested vertex index is outside the current compact OBJ group")
    export_indices = group["group_index_maps"]["positions_mm"]["compact_to_export"]
    getter_observation = _compare_native_getter(mesh, exported, certificate) if probe else None
    api_symbols = _probe_runtime({})["modules"] if probe else None
    if common.scene() != current:
        raise ReferenceError("SCENE_CHANGED: project, paper data or quality changed during the cloth read")
    asset = exported["export"]["obj_asset"]
    result = _reply("succeeded", {
        "pattern_uuid": pattern_uuid, "group_name": group_name, "space": "world", "unit": "mm",
        "index_space": "zero_based_compact_OBJ_group_arrays", "vertex_count": len(positions),
        "positions_mm": positions if indices is None else [positions[index] for index in indices],
        "vertex_indices": indices, "source_export_vertex_indices": export_indices if indices is None else [export_indices[index] for index in indices],
        "group_topology_fingerprint": group["topology_fingerprint"],
        "paper_input_fingerprint": target.get("topology_fingerprint"),
        "capture_id": exported["capture_id"], "snapshot_id": exported["snapshot_id"],
        "scene_token": current["scene_token"], "obj_asset": asset, "label_certificate": certificate,
        "native_getter_observation": getter_observation, "runtime_symbol_metadata": api_symbols,
        "native_owner_mapping_verified": False,
        "native_vertex_mapping_verified": False, "editable_references_issued": False,
        "solver_rest_value_read": False, "effects_started": False,
    })
    if include_refs:
        numeric = importlib.import_module('md_mcp_plugin.embedded.cloth_numeric_adapter')
        references = numeric.make_references(current, exported, pattern_uuid, group_name, indices)
        result['vertex_refs'] = references
    return mesh._bounded_reply(result, asset)


def _set_positions(params, *, request_id=None, fingerprint=None):
    _keys(params, required=("pattern_uuid", "targets", "scene_token"), optional=("tolerance_mm", "method"))
    tolerance = params.get("tolerance_mm", 0.002)
    _targets(params["pattern_uuid"], params["scene_token"], params["targets"], tolerance)
    if 'method' in params:
        from .common import check_scene_token
        from .dispatch import _module

        if params['method'] != 'meshbox_plane' or len(params['targets']) != 1:
            raise ValueError('meshbox_plane requires exactly one current vertex reference')
        return _module('cloth_numeric_runtime').run(check_scene_token(params['scene_token']), params['targets'][0], tolerance,
                                                    request_id=request_id, fingerprint=fingerprint)
    index = _resolve_pattern(params["pattern_uuid"])
    return _unsupported("NATIVE_CLOTH_XYZ_WRITER_UNAVAILABLE", "Request the explicit meshbox_plane method for a single calibrated vertex",
                        pattern_uuid=params["pattern_uuid"], resolved_pattern_index=index,
                        reference_freshness_verified=False, XYZ_readback_verified=False,
                        missing_contracts=["native mesh-vertex identity", "exclusive vertex selection", "calibrated background native drag or documented XYZ setter", "full mesh/untargeted-owner readback"])


def _rest_shape(params):
    _keys(params, required=("pattern_uuid", "action", "scene_token"), optional=('method',))
    _text(params["scene_token"], "scene_token")
    if params["action"] not in ("update_from_current", "reset_to_pattern"):
        raise ValueError("Unknown Rest Shape action")
    if 'method' in params:
        if params['method'] != 'native_archive':
            raise ValueError('Unknown Cloth Rest method')
        from .common import check_scene_token
        from .dispatch import _module

        _text(params['pattern_uuid'], 'pattern_uuid')
        action = 'update' if params['action'] == 'update_from_current' else 'reset'
        return _module('cloth_rest_runtime').run(check_scene_token(params['scene_token']), params['pattern_uuid'], action)
    index = _resolve_pattern(params["pattern_uuid"])
    return _unsupported("NATIVE_SOLVER_REST_ENTRY_UNAVAILABLE", "A scoped Update/Reset writer and independent solver-rest readback bound to this pattern and topology are unavailable",
                        pattern_uuid=params["pattern_uuid"], resolved_pattern_index=index, action=params["action"],
                        semantics="current 3D state to solver reference shape" if params["action"] == "update_from_current" else "restore original pattern-based solver reference shape",
                        automatic_preparation_available=False, manual_preparation_requested=False,
                        solver_rest_write_verified=False, solidify_substitution=False, sculpt_pinch_substitution=False,
                        official_contract="https://support.marvelousdesigner.com/hc/en-us/articles/59782132043289-Pinching-Simulation-Override")


def _operation(function):
    def run(params, **request):
        try:
            return function(params, **request)
        except ReferenceError as exc:
            message = str(exc)
            raise PluginError(message.split(":", 1)[0], message, effects_started=False) from exc
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise PluginError("INVALID_CLOTH_INPUT_OR_READBACK", str(exc), effects_started=False) from exc
        except (ImportError, AttributeError) as exc:
            return _unsupported("MD_API_UNAVAILABLE", str(exc))
    return run


def _reconcile_rest_archive(params):
    from .dispatch import _module

    _keys(params, required=('blocked_request_id', 'scene_token'))
    return _module('cloth_rest_runtime').reconcile(params)


def _recover_positions(params):
    from .dispatch import _module

    _keys(params, required=('blocked_request_id', 'scene_token'))
    return _module('cloth_numeric_runtime').recover(params)


OPERATIONS = {
    "cloth.probe_runtime": _operation(_probe_runtime),
    "cloth.read_scene_positions": _operation(_read_scene_positions),
    "cloth.get_positions": _operation(_get_positions),
    "cloth.set_positions": _operation(_set_positions),
    'cloth.recover_positions': _operation(_recover_positions),
    "cloth.rest_shape": _operation(_rest_shape),
    'cloth.reconcile_rest_archive': _operation(_reconcile_rest_archive),
}
