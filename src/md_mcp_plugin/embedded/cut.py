"""Fail-closed current-project native Cut. No GUI, fixture import or retries."""
from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
import re
import tempfile
import time

from md_mcp_plugin.errors import PluginError
from .common import digest, parsed, pattern_rows, resolve_pattern

_TOL_MM = 0.002
_SYMBOLS = (
    "GetPatternCount", "GetPatternInformation", "GetPatternInputInformation",
    "ExportPatternJSON", "CutPatternAlongInternalShapes", "GetPatternInternalShapePoints",
    "GetInnerShapeList",
)


class _CutError(ValueError):
    def __init__(self, code, message, status="unsupported", **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def _block(exc):
    return {
        "verified": False, "fulfilled": False, "outcome": getattr(exc, "status", "rejected"),
        "status": getattr(exc, "status", "rejected").upper(), "effects_started": False,
        "native_cut_calls": 0, "mutation_observed": False,
        "error": {"code": exc.code, "message": str(exc), **exc.details},
    }


def _text(value, field):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise _CutError("INVALID_REFERENCE", field + " must be a nonempty unpadded string", "rejected")
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise _CutError("INVALID_GEOMETRY", "All 2D coordinates must be finite JSON numbers", "rejected")
    return float(value)


def _index(value, field):
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        result = int(value)
    else:
        raise _CutError("UNPROVEN_RUNTIME_INDEX", field + " is not an actual nonnegative integer")
    if not 0 <= result <= 2147483647:
        raise _CutError("UNPROVEN_RUNTIME_INDEX", field + " is outside the API int range")
    return result


def _api():
    return importlib.import_module("pattern_api")


def _return_value(value):
    if type(value) is float and not math.isfinite(value):
        return {"type": "nonfinite_float", "repr": repr(value)}
    if type(value) in (bool, int, float, str, type(None)):
        return value
    return {"type": type(value).__name__, "repr": repr(value)[:512]}


def _metadata(api):
    return {
        name: {"available": callable(getattr(api, name, None)),
               "signature": str(getattr(getattr(api, name, None), "__doc__", "") or "")[:4096]}
        for name in _SYMBOLS
    }


def _index_contract(api):
    """A type-only int,int,bool signature does not establish index semantics."""
    cut = getattr(api, "CutPatternAlongInternalShapes", None)
    getter = getattr(api, "GetPatternInternalShapePoints", None)
    if not callable(cut) or not callable(getter):
        raise _CutError("NATIVE_CUT_API_MISSING", "Cut and its ordinal geometry getter must both be callable")
    doc = (getattr(cut, "__doc__", "") or "").lower()
    signature = re.sub(r"\s+", "", doc).replace("typing.", "")
    signature = re.sub(r"\b[a-z_]\w*:", "", signature)
    if "cutpatternalonginternalshapes(int,int,bool)->bool" not in signature:
        raise _CutError("CUT_SIGNATURE_UNVERIFIED", "Expected actual int,int,bool -> bool runtime contract")
    namespace = (
        "getpatterninternalshapepoints" in doc
        and ("0-based" in doc or "zero-based" in doc)
        and ("internal shape list" in doc or "internal-shape-list" in doc)
        and ("not the child" in doc or "not child" in doc or "m_children" in doc)
    )
    if not namespace:
        raise _CutError(
            "MD_CUT_ORDINAL_CONTRACT_UNVERIFIED",
            "Runtime doc only establishes types, not the MD internal-list ordinal namespace. A CLO contract is not an MD implementation guarantee.",
            required_bridge="Current model internal-list ordinal to shape/line UUID binding, not a creation return or runtime child index",
        )
    getter_doc = (getattr(getter, "__doc__", "") or "").lower()
    if not (("millimeter" in getter_doc or "millimetre" in getter_doc)
            and ("downward" in getter_doc or "y-down" in getter_doc)):
        raise _CutError("GETTER_FRAME_UNVERIFIED", "The runtime getter's mm/Y-down frame must be explicit")
    return {"namespace": "internal_list_ordinal", "source": "explicit_runtime_semantic_doc",
            "product_execution_validation": "pending", "getter_frame": "mm_y_down_up_to_common_origin"}


def _xy(point):
    position = point.get("Position") if isinstance(point, dict) else None
    if not isinstance(position, dict):
        raise _CutError("INVALID_GEOMETRY", "Native point Position is missing", "rejected")
    return [_number(position.get("x")), _number(position.get("y"))]


def _shape_geometry(shape, *, include_ids=True):
    lines = shape.get("LineList", [])
    if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
        raise _CutError("INVALID_NATIVE_SNAPSHOT", "Expected native LineList", "rejected")
    geometry = {
        "shape_type": shape.get("ShapeType"),
        "closed": shape.get("IsClosed"),
        "lines": [{"points": [[*_xy(q), q.get("PointType")] for q in line.get("PointList", [])]}
                  for line in lines],
    }
    if include_ids:
        geometry["shape_uuid"] = shape.get("ID")
        for entry, line in zip(geometry["lines"], lines):
            entry["line_uuid"] = line.get("ID")
    return geometry


def _geometry(paper, *, include_ids=True):
    return {
        "pattern_uuid": paper["ID"], "outline_closed": paper.get("IsClosed"),
        "outline": _shape_geometry(paper.get("ShapeInfo", {}), include_ids=include_ids),
        "internal": [_shape_geometry(shape, include_ids=include_ids)
                     for shape in paper.get("InternalLineList", [])
                     if shape.get("ShapeType") != 2],
    }


def _capture(api):
    rows = pattern_rows()
    if len(rows) > 4096:
        raise _CutError("SCENE_TOO_LARGE", "Cut snapshot is bounded to 4096 patterns")
    exporter = getattr(api, "ExportPatternJSON", None)
    if not callable(exporter):
        raise _CutError("NATIVE_SNAPSHOT_UNAVAILABLE", "No public native JSON reader is callable; CPX is not decoded or reimported")
    with tempfile.TemporaryDirectory(prefix="md_mcp_cut_read_") as directory:
        path = Path(directory) / "current.json"
        returned = exporter(str(path))
        if returned is not True or not path.is_file():
            raise _CutError(
                "NATIVE_SNAPSHOT_UNAVAILABLE",
                "ExportPatternJSON did not produce a fresh native snapshot. MD documents deprecation; never replace the current scene through JSON/CPX conversion.",
                native_return=_return_value(returned),
            )
        if path.stat().st_size > 32 * 1024 * 1024:
            raise _CutError("SNAPSHOT_TOO_LARGE", "Native JSON snapshot exceeds 32 MiB")
        native = json.loads(path.read_text(encoding="utf-8-sig"))
    papers = native.get("PatternList") if isinstance(native, dict) else None
    if not isinstance(papers, list) or any(not isinstance(q, dict) for q in papers):
        raise _CutError("INVALID_NATIVE_SNAPSHOT", "A native PatternList is required")
    identities = [q.get("ID") for q in papers]
    if (any(not isinstance(q, str) or not q for q in identities)
            or len(set(identities)) != len(identities)
            or set(identities) != {q["pattern_uuid"] for q in rows}):
        raise _CutError("SNAPSHOT_UUID_MISMATCH", "Native snapshot and fresh runtime UUID set disagree", "rejected")
    after = pattern_rows()
    if rows != after:
        raise _CutError("SCENE_CHANGED_DURING_READ", "Runtime changed while reading the native snapshot", "rejected")
    by_id = {q["ID"]: q for q in papers}
    fingerprints = {identity: "sha256:" + digest(_geometry(paper)) for identity, paper in by_id.items()}
    return {"native": native, "rows": rows, "papers": by_id, "fingerprints": fingerprints,
            "scene_fingerprint": digest(fingerprints)}


def _owner(capture, pattern_uuid):
    matches = [row for row in capture["rows"] if row["pattern_uuid"] == pattern_uuid]
    if len(matches) != 1:
        raise _CutError("STALE_PATTERN_REFERENCE", "Pattern UUID must resolve uniquely in the current project", "rejected")
    row = matches[0]
    fingerprint = "sha256:" + digest({
        "native": capture["fingerprints"][pattern_uuid],
        "runtime": row.get("topology_fingerprint"),
    })
    return row, capture["papers"][pattern_uuid], fingerprint


def _curves(shape):
    result = []
    for line in shape.get("LineList", []):
        points = line.get("PointList", [])
        xy = [_xy(q) for q in points]
        kinds = {q.get("PointType") for q in points}
        if not ((len(xy) == 2 and kinds <= {"Straight", "Polyline"})
                or (len(xy) == 4 and "Bezier Curve" in kinds and kinds <= {"Bezier Curve", "Straight"})):
            raise _CutError("CURVE_REPRESENTATION_UNSUPPORTED", "Only native two-point straight or four-control cubic Bezier lines are verified")
        result.append({"line_uuid": _text(line.get("ID"), "line_uuid"), "points_mm": xy})
    if not result:
        raise _CutError("EMPTY_BOUNDARY", "Native shape has no boundary lines")
    if len({q["line_uuid"] for q in result}) != len(result):
        raise _CutError("AMBIGUOUS_LINE_UUID", "Native line UUIDs are duplicated", "rejected")
    return result


def _near(a, b):
    return math.dist(a, b) <= _TOL_MM


def _equal_curve(a, b):
    return len(a) == len(b) and (
        all(_near(x, y) for x, y in zip(a, b))
        or all(_near(x, y) for x, y in zip(a, reversed(b)))
    )


def _runtime_binding(row, points):
    raw = row.get("input_information", {})
    roots = raw.get("Pattern InputInformation") if isinstance(raw, dict) else None
    if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict):
        raise _CutError("RUNTIME_BINDING_UNAVAILABLE", "Expected one fresh Pattern InputInformation root")
    root = roots[0]
    # GetPatternInputInformation is queried at the UUID-resolved SDK index.
    # Its one returned paper can have a local index of zero even for SDK index 1.
    name = row.get("information", {}).get("name")
    if (not isinstance(name, str) or not name or root.get("Pattern name") != name
            or _index(root.get("Pattern index"), "Pattern index") not in (0, row["index"])):
        raise _CutError("RUNTIME_OWNER_MISMATCH", "Input information does not corroborate the UUID-resolved pattern", "rejected")
    children = root.get("Children Information")
    if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
        raise _CutError("RUNTIME_BINDING_UNAVAILABLE", "Missing complete runtime children list")
    indices = [_index(child["Shape index"], "Shape index") for child in children if "Shape index" in child]
    if len(set(indices)) != len(indices):
        raise _CutError("AMBIGUOUS_RUNTIME_CHILD", "Runtime child indices are duplicated", "rejected")
    matches = []
    for child in children:
        if child.get("Shape type") != "InnerShape":
            continue
        point_rows = [q for q in child.get("PointList", []) if isinstance(q, dict) and "Point index" in q]
        line_rows = [q for q in child.get("LineList", []) if isinstance(q, dict) and "Line index" in q]
        if len(point_rows) != 2 or len(line_rows) != 1:
            continue
        ends = [[_number(q.get("Point positionX")), _number(q.get("Point positionY"))] for q in point_rows]
        if not _equal_curve([points[0], points[-1]], ends):
            continue
        length = _number(line_rows[0].get("Line length"))
        if length <= 0 or length + _TOL_MM < math.dist(points[0], points[-1]):
            continue
        matches.append({
            "runtime_child_index": _index(child.get("Shape index"), "Shape index"),
            "runtime_line_index": _index(line_rows[0].get("Line index"), "Line index"),
            "native_length_mm": length, "absolute_endpoints_mm": ends,
            "is_cut_ordinal": False,
        })
    if len(matches) != 1:
        raise _CutError("AMBIGUOUS_RUNTIME_BINDING", "Shape UUID geometry must match exactly one current runtime child/line")
    return matches[0]


def _target(api, row, paper, shape_uuid, line_uuid=None):
    shapes = paper.get("InternalLineList", [])
    targets = [q for q in shapes if q.get("ID") == shape_uuid]
    if len(targets) != 1:
        raise _CutError("STALE_SHAPE_REFERENCE", "Internal shape UUID is missing or duplicated", "rejected")
    shape = targets[0]
    if shape.get("ShapeType") != 1 or shape.get("IsClosed") is not False:
        raise _CutError("CUT_SHAPE_UNSUPPORTED", "Cut requires an explicitly open genuine ShapeType1 shape")
    if any(q.get("ShapeType") not in (1, 2) or q.get("IsHole") or q.get("IsBinding")
           or q.get("bIsHole") or q.get("bIsBinding") for q in shapes):
        raise _CutError("INTERNAL_NAMESPACE_UNVERIFIED", "Holes/bindings/unknown shapes need a proven internal-list bridge")
    if len([q for q in shapes if q.get("ShapeType") == 1]) != 1:
        raise _CutError("MULTISHAPE_ORDINAL_UNVERIFIED", "Multiple internal shapes need explicit ordinal/UUID mapping; no candidate enumeration")
    curves = _curves(shape)
    if len(curves) != 1:
        raise _CutError("MULTILINE_CUT_UNSUPPORTED", "This native Cut adapter verifies one complete internal line only")
    curve = curves[0]
    if line_uuid is not None and curve["line_uuid"] != line_uuid:
        raise _CutError("STALE_LINE_REFERENCE", "Line UUID does not belong to the requested shape", "rejected")
    binding = _runtime_binding(row, curve["points_mm"])
    # The sole genuine internal shape has ordinal zero regardless of export order.
    # A child index, ShapeType enum, or creation return is never used here.
    contract = _index_contract(api)
    observed = api.GetPatternInternalShapePoints(row["index"], 0)
    if not isinstance(observed, (list, tuple)) or len(observed) != len(curve["points_mm"]):
        raise _CutError("ORDINAL_GETTER_UNVERIFIED", "The sole ordinal getter is empty/sparse/malformed; no Cut dispatched")
    values = []
    for point in observed:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise _CutError("ORDINAL_GETTER_UNVERIFIED", "Getter must return full two-coordinate points")
        values.append([_number(point[0]), _number(point[1])])
    fits = []
    for seq in (curve["points_mm"], list(reversed(curve["points_mm"]))):
        expected = [[q[0], -q[1]] for q in seq]
        delta = [values[0][k] - expected[0][k] for k in (0, 1)]
        fits.append(max(math.dist([q[0] + delta[0], q[1] + delta[1]], v) for q, v in zip(expected, values)))
    if min(fits) > _TOL_MM:
        raise _CutError("ORDINAL_GEOMETRY_MISMATCH", "Getter geometry does not match the UUID-bound native controls")
    return {**binding, "cut_internal_ordinal": 0, "contract": contract,
            "shape_uuid": shape_uuid, "line_uuid": curve["line_uuid"],
            "curve": curve, "getter_control_error_mm": min(fits)}


def _runtime_refs(row):
    raw = row.get("input_information", {})
    roots = raw.get("Pattern InputInformation", []) if isinstance(raw, dict) else []
    if len(roots) != 1 or not isinstance(roots[0], dict):
        return []
    refs = []
    for child in roots[0].get("Children Information", []):
        if not isinstance(child, dict) or "Shape index" not in child:
            continue
        refs.append({
            "kind": "runtime_child_not_cut_ordinal", "pattern_uuid": row["pattern_uuid"],
            "topology_fingerprint": row.get("topology_fingerprint"),
            "child_index": child["Shape index"], "shape_type": child.get("Shape type"),
            "line_indices": [q["Line index"] for q in child.get("LineList", []) if isinstance(q, dict) and "Line index" in q],
            "shape_uuid": None,
        })
    return refs


def _probe(params):
    if params:
        raise _CutError("INVALID_PARAMETERS", "Runtime probe takes no parameters", "rejected")
    api = _api()
    try:
        contract = _index_contract(api)
        gate = {"status": "documented_runtime_namespace", "contract": contract}
    except _CutError as exc:
        gate = {"status": "unsupported", "error": {"code": exc.code, "message": str(exc), **exc.details}}
    return {
        "verified": True, "observation": "metadata_only_not_execution",
        "symbols": _metadata(api), "index_contract": gate, "native_cut_calls": 0,
        "surface_preservation": "unsupported", "live_validation": "pending",
    }


def _inspect(params):
    if set(params) != {"pattern_uuid"}:
        raise _CutError("INVALID_PARAMETERS", "Inspect requires only pattern_uuid", "rejected")
    identity = _text(params["pattern_uuid"], "pattern_uuid")
    api = _api()
    row = resolve_pattern(identity)
    result = {"verified": True, "pattern_uuid": identity, "current_pattern_index": row["index"],
              "runtime_child_refs": _runtime_refs(row), "native_cut_calls": 0,
              "surface_preservation": "unsupported", "live_validation": "pending"}
    try:
        capture = _capture(api)
        row, paper, fingerprint = _owner(capture, identity)
    except (_CutError, PluginError) as exc:
        result.update(native_snapshot_available=False, topology_fingerprint=None, shape_refs=[],
                      cut_readiness={"status": "unsupported", "error": {"code": exc.code, "message": str(exc), **exc.details}})
        return result
    result.update(native_snapshot_available=True, topology_fingerprint=fingerprint, shape_refs=[])
    for shape in paper.get("InternalLineList", []):
        if shape.get("ShapeType") == 2:
            continue
        ref = {"pattern_uuid": identity, "shape_uuid": shape.get("ID"), "topology_fingerprint": fingerprint,
               "line_uuids": [q.get("ID") for q in shape.get("LineList", [])], "shape_type": shape.get("ShapeType"),
               "closed": shape.get("IsClosed")}
        try:
            mapping = _target(api, row, paper, shape.get("ID"))
            ref["mapping"] = {k: v for k, v in mapping.items() if k != "curve"}
            ref["cut_readiness"] = "native_2d_gate_passed_not_live_validation"
        except _CutError as exc:
            ref["cut_readiness"] = "unsupported"
            ref["mapping_error"] = {"code": exc.code, "message": str(exc), **exc.details}
        result["shape_refs"].append(ref)
    return result


def _boundary_chain(curves):
    remaining = [q["points_mm"] for q in curves]
    chain = [remaining.pop(0)]
    while remaining:
        candidates = []
        for i, curve in enumerate(remaining):
            if _near(chain[-1][-1], curve[0]):
                candidates.append((i, curve))
            elif _near(chain[-1][-1], curve[-1]):
                candidates.append((i, list(reversed(curve))))
        if len(candidates) != 1:
            raise _CutError("BOUNDARY_ORDER_UNVERIFIED", "Boundary is not a unique connected loop")
        i, curve = candidates[0]
        chain.append(curve)
        remaining.pop(i)
    if not _near(chain[-1][-1], chain[0][0]):
        raise _CutError("BOUNDARY_OPEN", "Native boundary is not closed")
    return chain


def _chain_area(chain):
    origin = chain[0][0]
    integral = 0.0
    for pts in chain:
        axes = []
        for axis in (0, 1):
            p = [q[axis] - origin[axis] for q in pts]
            axes.append([p[0], p[1] - p[0]] if len(p) == 2 else [
                p[0], 3 * (p[1] - p[0]), 3 * (p[0] - 2*p[1] + p[2]), -p[0] + 3*p[1] - 3*p[2] + p[3]])
        x, y = axes
        integral += sum((x[i] * j * y[j] - y[i] * j * x[j]) / (i+j)
                        for i in range(len(x)) for j in range(1, len(y)))
    return abs(integral) * 0.5


def _source_chains(outline, cut_points):
    endpoints = [[i for i, edge in enumerate(outline) if _near(edge[0], point)]
                 for point in (cut_points[0], cut_points[-1])]
    if any(len(indices) != 1 for indices in endpoints) or endpoints[0] == endpoints[1]:
        raise _CutError("CUT_ENDPOINTS_UNVERIFIED", "Two distinct and unique outline anchors are required")
    start, end = (indices[0] for indices in endpoints)
    if start > end:
        start, end = end, start
        cut_points = list(reversed(cut_points))
    return [outline[start:end] + [list(reversed(cut_points))],
            outline[end:] + outline[:start] + [cut_points]]


def _child_placement(boundary, source_chains):
    """Match a whole child loop under one uniform 2D translation."""
    matches = []
    reversed_boundary = [list(reversed(edge)) for edge in reversed(boundary)]
    for chain_index, expected in enumerate(source_chains):
        if len(boundary) != len(expected):
            continue
        for traversal in (boundary, reversed_boundary):
            for cut_index, edge in enumerate(traversal):
                if len(edge) != len(expected[-1]):
                    continue
                offset = [a - b for a, b in zip(edge[0], expected[-1][0])]
                if any(math.dist(a, [b[0] + offset[0], b[1] + offset[1]]) > _TOL_MM
                       for a, b in zip(edge, expected[-1])):
                    continue
                rotated = traversal[cut_index + 1:] + traversal[:cut_index + 1]
                if any(len(a) != len(b) for a, b in zip(rotated, expected)):
                    continue
                residual = max(math.dist(a, [b[0] + offset[0], b[1] + offset[1]])
                               for actual_edge, source_edge in zip(rotated, expected)
                               for a, b in zip(actual_edge, source_edge))
                if residual <= _TOL_MM:
                    matches.append({"source_chain": chain_index, "translation_mm": offset,
                                    "maximum_control_residual_mm": residual})
                    if len(matches) > 1:
                        return None
    return matches[0] if matches else None


def _partition(before, after, identity, cut_curve):
    old_ids, new_ids = set(before["papers"]), set(after["papers"])
    created, removed = new_ids - old_ids, old_ids - new_ids
    retained = identity in new_ids and (
        _shape_geometry(before["papers"][identity]["ShapeInfo"], include_ids=False)
        != _shape_geometry(after["papers"][identity]["ShapeInfo"], include_ids=False))
    part_ids = sorted(created | ({identity} if retained else set()))
    checks = {
        "pattern_count_increased_by_one": len(new_ids) == len(old_ids) + 1,
        "only_source_removed": removed <= {identity},
        "two_resulting_parts": len(part_ids) == 2,
        "unaffected_nonbaseline_2d_geometry_preserved": all(
            uid in after["papers"] and _geometry(before["papers"][uid], include_ids=False)
            == _geometry(after["papers"][uid], include_ids=False)
            for uid in old_ids - {identity}),
    }
    errors = []
    checks["boundary_partition_verified"] = False
    try:
        if checks["two_resulting_parts"]:
            source = _boundary_chain(_curves(before["papers"][identity]["ShapeInfo"]))
            expected = _source_chains(source, cut_curve["points_mm"])
            output = {uid: _boundary_chain(_curves(after["papers"][uid]["ShapeInfo"])) for uid in part_ids}
            placements = {uid: _child_placement(boundary, expected) for uid, boundary in output.items()}
            checks["child_boundary_placements"] = placements
            checks["boundary_control_tolerance_mm"] = _TOL_MM
            source_area = _chain_area(source)
            areas = [_chain_area(boundary) for boundary in output.values()]
            area_error = abs(sum(areas) - source_area)
            checks.update(source_area_mm2=source_area, part_areas_mm2=areas, area_error_mm2=area_error,
                          area_tolerance_mm2=max(0.01, source_area * 1e-5))
            checks["boundary_partition_verified"] = (
                all(placements.values()) and {p["source_chain"] for p in placements.values()} == {0, 1}
                and source_area > 0 and all(q > 0 for q in areas)
                and area_error <= checks["area_tolerance_mm2"])
    except _CutError as exc:
        errors.append({"code": exc.code, "message": str(exc)})
    return {"native_partition_observed": all(checks[key] for key in (
                "pattern_count_increased_by_one", "only_source_removed", "two_resulting_parts",
                "unaffected_nonbaseline_2d_geometry_preserved", "boundary_partition_verified")),
            "checks": checks, "readback_errors": errors, "created_pattern_uuids": sorted(created),
            "removed_pattern_uuids": sorted(removed), "resulting_pattern_uuids": part_ids,
            "count_before": len(old_ids), "count_after": len(new_ids)}


def _execute(params):
    required = {"pattern_uuid", "shape_uuid", "topology_fingerprint"}
    allowed = required | {"line_uuid", "cut_and_sew", "require_surface_preservation"}
    if not required <= set(params) or set(params) - allowed:
        raise _CutError("INVALID_PARAMETERS", "Cut requires UUIDs and a topology fingerprint", "rejected")
    identity, shape_uuid = (_text(params[k], k) for k in ("pattern_uuid", "shape_uuid"))
    line_uuid = params.get("line_uuid")
    if line_uuid is not None:
        _text(line_uuid, "line_uuid")
    fingerprint = params["topology_fingerprint"]
    if not isinstance(fingerprint, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None:
        raise _CutError("INVALID_TOPOLOGY_FINGERPRINT", "Use the full fingerprint from cut_inspect", "rejected")
    sew, surface = params.get("cut_and_sew", False), params.get("require_surface_preservation", True)
    if type(sew) is not bool or type(surface) is not bool:
        raise _CutError("INVALID_BOOLEAN", "Cut flags must be actual JSON booleans", "rejected")
    if surface:
        raise _CutError("SURFACE_PRESERVATION_UNSUPPORTED", "Generic 3D/Rest/UV preservation is unverified; native 2D Cut cannot satisfy this requirement")
    api = _api()
    before = _capture(api)
    row, paper, actual_fingerprint = _owner(before, identity)
    if actual_fingerprint != fingerprint:
        raise _CutError("STALE_TOPOLOGY", "Current owner geometry/runtime differs from the captured reference", "rejected")
    mapping = _target(api, row, paper, shape_uuid, line_uuid)
    source_boundary = _boundary_chain(_curves(paper["ShapeInfo"]))
    _source_chains(source_boundary, mapping["curve"]["points_mm"])
    fresh = _capture(api)
    fresh_row, _, fresh_fingerprint = _owner(fresh, identity)
    if fresh["scene_fingerprint"] != before["scene_fingerprint"] or fresh_fingerprint != fingerprint or fresh_row["index"] != row["index"]:
        raise _CutError("SCENE_CHANGED_BEFORE_CUT", "Current scene changed during mapping reads", "rejected")
    if parsed(api.GetPatternInformation(row["index"])).get("uuid") != identity:
        raise _CutError("STALE_PATTERN_INDEX", "The current pattern index no longer matches the UUID", "rejected")
    args = [row["index"], mapping["cut_internal_ordinal"], sew]
    receipt = {"args": args, "calls": 1, "returned": None, "return_type": None, "exception": None}
    started = time.perf_counter()
    try:
        returned = api.CutPatternAlongInternalShapes(*args)
        receipt.update(returned=_return_value(returned),
                       return_type=type(returned).__name__)
    except Exception as exc:
        receipt["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    receipt["seconds"] = time.perf_counter() - started
    result = {
        "verified": False, "fulfilled": False, "native_cut_calls": 1, "invocation": receipt,
        "status": "REJECTED", "effects_started": True,
        "mapping": {k: v for k, v in mapping.items() if k != "curve"},
        "surface_preservation": {"status": "unverified", "required": False, "3d": False, "rest": False, "uv": False},
        "cut_and_sew_readback": "unverified" if sew else "not_requested",
        "do_not_retry": True, "live_validation": "pending",
    }
    try:
        after = _capture(api)
        readback = _partition(before, after, identity, mapping["curve"])
        result.update(readback=readback, mutation_observed=before["scene_fingerprint"] != after["scene_fingerprint"],
                      new_pattern_refs=[{
                          "pattern_uuid": uid,
                          "current_pattern_index": next(q["index"] for q in after["rows"] if q["pattern_uuid"] == uid),
                          "topology_fingerprint": _owner(after, uid)[2],
                          "outline_line_uuids": [q.get("ID") for q in after["papers"][uid]["ShapeInfo"]["LineList"]],
                      } for uid in readback["resulting_pattern_uuids"]])
    except Exception as exc:
        result.update(status="UNKNOWN", outcome="unknown", mutation_observed=None,
                      error={"code": "POST_CUT_READBACK_FAILED", "message": str(exc), "type": type(exc).__name__})
        return result
    if receipt["exception"] is not None:
        result.update(outcome="unknown", error={"code": "NATIVE_CUT_EXCEPTION", **receipt["exception"]})
    elif receipt["return_type"] != "bool":
        result.update(outcome="unknown", error={"code": "UNEXPECTED_NATIVE_RETURN", "message": "Expected actual bool; no truthiness conversion"})
    elif receipt["returned"] is False:
        result.update(outcome="partial" if result["mutation_observed"] else "rejected",
                      error={"code": "NATIVE_CUT_RETURNED_FALSE", "message": "One native call returned False; inspect its readback, never resend blindly"})
    elif readback["native_partition_observed"] and not sew:
        result.update(verified=True, fulfilled=True, outcome="native_2d_partition_verified")
    else:
        result.update(outcome="partial" if result["mutation_observed"] else "unfulfilled",
                      error={"code": "CUT_READBACK_INCOMPLETE", "message": "True alone does not verify partition/sewing; actual changes and new UUIDs are reported"})
    result["status"] = {
        "unknown": "UNKNOWN", "partial": "PARTIAL",
        "native_2d_partition_verified": "SUCCEEDED",
    }.get(result["outcome"], "REJECTED")
    return result


def _entry(function):
    def wrapped(params):
        try:
            if not isinstance(params, dict):
                raise _CutError("INVALID_PARAMETERS", "Parameters must be an object", "rejected")
            return function(params)
        except (_CutError, PluginError) as exc:
            return _block(exc)
        except (ImportError, ModuleNotFoundError) as exc:
            return _block(_CutError("MD_API_UNAVAILABLE", str(exc)))
    return wrapped


OPERATIONS = {
    "cut.probe_runtime": _entry(_probe),
    "cut.inspect": _entry(_inspect),
    "cut.along_internal_shape": _entry(_execute),
}
