"""Native Bezier identity/readback guards; no fixture or GUI route is built in.

Only stdlib dependencies are used. The installed writer hook is trusted embedded
code, never an MCP parameter; no unverified SDK setter or old pixel macro is called.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import json
import math
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path

_PREFIX = "mdcurve2:"
_REF_KEYS = {"version", "pattern_uuid", "shape_kind", "shape_key", "line_key", "handle_key",
             "role", "topology_fingerprint", "geometry_fingerprint", "scene_fingerprint", "scope"}
NATIVE_FINGERPRINT_FIELDS = ('scene_fingerprint', 'geometry_fingerprint', 'topology_fingerprint')
_ID_REFERENCE_FIELDS = {"ID", "FabricUUID", "CurrentFabricUUID", "OriginPatternID", "ShapeID", "LineID", "PointID",
                        "parentShapeUUID", "parentShapeUUIDUTF8", "startLineUUID", "startLineUUIDUTF8",
                        "endLineUUID", "endLineUUIDUTF8"}


class CurveError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _id(value, label: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 256 or "\x00" in value:
        raise CurveError("INVALID_INPUT", f"{label} must be a nonempty ID string")
    return value


def _xy(value, label: str) -> list[float]:
    if type(value) not in (list, tuple) or len(value) != 2:
        raise CurveError("INVALID_INPUT", f"{label} must contain exactly two XY coordinates in mm")
    if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 10_000_000 for v in value):
        raise CurveError("INVALID_INPUT", f"{label} must be finite and within +/-10000000 mm")
    return [float(v) for v in value]


def _tolerance(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= .002:
        raise CurveError("INVALID_INPUT", "tolerance_mm must be in (0, 0.002] mm")
    return float(value)


def _pairs(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise CurveError("INVALID_NATIVE_DATA", f"Duplicate JSON key: {key}")
        output[key] = value
    return output


def _loads(text):
    return json.loads(text, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(CurveError("INVALID_NATIVE_DATA", "Nonfinite JSON")))


def _state() -> dict:
    state = getattr(sys, "_md_mcp_plugin_curves_state", None)
    if state is None:
        state = {"epoch": uuid.uuid4().hex, "writer": None}
        sys._md_mcp_plugin_curves_state = state
    return state


def install_handle_writer(writer=None, *, name: str = "", evidence: str = "") -> None:
    """Internal integration hook, deliberately absent from OPERATIONS.

    A provider accepts one structured plan and must resolve its handle ID in the
    bound current project, without OS input, focus or replacement/import/save.
    A Qt provider also needs a proven fresh native-mm -> scene-coordinate mapping
    and exact handle selection; viewportTransform alone does not provide that.
    This hook supplies no provider and certifies none. Caller return values never
    substitute for the module's independent native readback.
    """
    if writer is None:
        _state()["writer"] = None
        return
    if not callable(writer):
        raise TypeError("writer must be callable")
    _id(name, "writer name")
    if type(evidence) is not str or not evidence.strip():
        raise ValueError("A trusted provider requires an explicit evidence description")
    _state()["writer"] = {"call": writer, "name": name, "evidence": evidence}


def _paths_for_value(value, wanted, path=()):
    if type(value) is dict:
        return [p for k, v in value.items() for p in _paths_for_value(v, wanted, path + (k,))]
    if type(value) is list:
        return [p for i, v in enumerate(value) for p in _paths_for_value(v, wanted, path + (i,))]
    return [path] if type(value) is str and value == wanted else []


def _identity_graph(native: dict) -> tuple[dict, dict]:
    """Canonicalize declaration/reference bijections, never numeric properties.

    Some native JSON IDs are export-local. A declaration's structural path is
    its alias; repeated declarations and every known reference must retain the
    same equivalence class. Splits, merges, reordered owners and reference
    retargeting therefore remain observable. SDK pattern UUIDs stay literal.
    """
    stable = {p.get("ID") for p in native.get("PatternList", []) if type(p) is dict and type(p.get("ID")) is str}
    declarations = {}
    def collect(value, path=()):
        if type(value) is dict:
            for key, child in value.items():
                declaration = key == "ID" or (key == "FabricUUID" and len(path) == 2 and path[0] == "FabricList")
                if declaration and type(child) is str and child not in stable:
                    declarations.setdefault(child, []).append(path + (key,))
                collect(child, path + (key,))
        elif type(value) is list:
            for i, child in enumerate(value):
                collect(child, path + (i,))
    collect(native)
    aliases = {ident: "@native-id:" + _digest(sorted(_canonical(path) for path in paths))
               for ident, paths in declarations.items()}
    def normalize(value, path=()):
        if type(value) is dict:
            return {key: normalize(child, path + (key,)) for key, child in value.items()}
        if type(value) is list:
            return [normalize(child, path + (i,)) for i, child in enumerate(value)]
        if type(value) is str and value in aliases:
            if not path or path[-1] not in _ID_REFERENCE_FIELDS:
                raise CurveError("UNVERIFIED_ID_REFERENCE", "Native ID occurs in an unverified reference field: " + _canonical(path))
            return aliases[value]
        return value
    return normalize(native), aliases


def _shapes(pattern: dict, path: tuple):
    outer = pattern.get("ShapeInfo")
    if type(outer) is not dict:
        raise CurveError("INVALID_NATIVE_DATA", "Pattern has no ShapeInfo")
    yield "outer", None, outer, path + ("ShapeInfo",)
    children = pattern.get("InternalLineList", [])
    if type(children) is not list:
        raise CurveError("INVALID_NATIVE_DATA", "InternalLineList must be a list")
    seen = set()
    for i, child in enumerate(children):
        if type(child) is not dict:
            raise CurveError("INVALID_NATIVE_DATA", "Invalid internal shape")
        shape_id = _id(child.get("ID"), "shape ID")
        if shape_id in seen:
            raise CurveError("AMBIGUOUS_REFERENCE", "Duplicate internal shape ID")
        seen.add(shape_id)
        yield "internal", shape_id, child, path + ("InternalLineList", i)


def _analyse(native: dict, pattern_uuid: str, scope=None) -> dict:
    """Pure parser. Native IDs are used; handle indices are never inferred."""
    _id(pattern_uuid, "pattern_uuid")
    if type(native) is not dict or native.get("Unit") != "mm":
        raise CurveError("UNSUPPORTED_UNIT", "A native Pattern JSON document with Unit=mm is required")
    try:
        _canonical(native)
    except (TypeError, ValueError) as exc:
        raise CurveError("INVALID_NATIVE_DATA", "Native data must contain finite JSON values") from exc
    patterns = native.get("PatternList")
    if type(patterns) is not list:
        raise CurveError("INVALID_NATIVE_DATA", "PatternList must be a list")
    matches = [(i, p) for i, p in enumerate(patterns) if type(p) is dict and p.get("ID") == pattern_uuid]
    if len(matches) != 1:
        raise CurveError("PATTERN_NOT_UNIQUE", "pattern_uuid must identify exactly one current pattern")
    pattern_index, pattern = matches[0]
    normalized, aliases = _identity_graph(native)
    scene_fingerprint = _digest(normalized)
    geometries, topology, occurrences, unsupported, endpoint_positions = [], [], {}, [], {}
    for kind, shape_id, shape, shape_path in _shapes(pattern, ("PatternList", pattern_index)):
        lines = shape.get("LineList")
        if type(lines) is not list:
            raise CurveError("INVALID_NATIVE_DATA", "Shape LineList must be a list")
        seen_lines = set()
        structure = {"kind": kind, "shape_key": aliases.get(shape_id, shape_id), "closed": shape.get("IsClosed"), "lines": []}
        for i, line in enumerate(lines):
            if type(line) is not dict:
                raise CurveError("INVALID_NATIVE_DATA", "Invalid line")
            line_id = _id(line.get("ID"), "line ID")
            if line_id in seen_lines:
                raise CurveError("AMBIGUOUS_REFERENCE", "Duplicate line ID within its shape")
            seen_lines.add(line_id)
            points = line.get("PointList")
            if type(points) is not list or len(points) < 2:
                raise CurveError("INVALID_NATIVE_DATA", "A native line needs at least two points")
            clean = []
            for j, point in enumerate(points):
                if type(point) is not dict or type(point.get("Position")) is not dict or set(point["Position"]) != {"x", "y"}:
                    raise CurveError("INVALID_NATIVE_DATA", "Point Position must be exactly {x, y}")
                point_id = _id(point.get("ID"), "point ID")
                point_type = _id(point.get("PointType"), "PointType")
                position = _xy([point["Position"]["x"], point["Position"]["y"]], "native point")
                path = shape_path + ("LineList", i, "PointList", j)
                previous = occurrences.setdefault(point_id, [])
                if previous and (previous[0]["xy_mm"] != position or previous[0]["type"] != point_type):
                    raise CurveError("AMBIGUOUS_REFERENCE", "One point ID has conflicting positions or types")
                previous.append({"path": path, "xy_mm": position, "type": point_type})
                clean.append({"id": point_id, "type": point_type, "xy_mm": position, "path": path})
                if kind == "outer" and j in (0, len(points) - 1):
                    endpoint_positions[point_id] = position
            structure["lines"].append({"id": aliases.get(line_id, line_id), "points": [[aliases.get(p["id"], p["id"]), p["type"]] for p in clean]})
            base = {"shape_kind": kind, "shape_uuid": shape_id, "line_uuid": line_id}
            cubic = len(clean) == 4 and [p["type"] for p in clean] == ["Straight", "Bezier Curve", "Bezier Curve", "Straight"]
            if cubic and len({p["id"] for p in clean}) == 4:
                geometries.append({**base, "points": clean})
            elif not (len(clean) == 2 and all(p["type"] == "Straight" for p in clean)):
                unsupported.append({**base, "reason": "Only an unambiguous four-point native cubic is editable",
                                    "point_types": [p["type"] for p in clean]})
        topology.append(structure)
    fingerprints = {"topology_fingerprint": _digest(topology), "geometry_fingerprint": _digest(normalized["PatternList"][pattern_index]),
                    "scene_fingerprint": scene_fingerprint, "scope": _digest(scope)}
    curves, targets = [], {}
    for item in geometries:
        points = item["points"]
        curve = {k: item[k] for k in ("shape_kind", "shape_uuid", "line_uuid")}
        curve.update(endpoints=[{"point_uuid": points[j]["id"], "xy_mm": points[j]["xy_mm"]} for j in (0, 3)], handles=[])
        for j, role in ((1, "start"), (2, "end")):
            point = points[j]
            identity = {"version": 2, "pattern_uuid": pattern_uuid, "shape_kind": item["shape_kind"],
                        "shape_key": aliases.get(item["shape_uuid"], item["shape_uuid"]),
                        "line_key": aliases.get(item["line_uuid"], item["line_uuid"]),
                        "handle_key": aliases.get(point["id"], point["id"]), "role": role, **fingerprints}
            token = _PREFIX + base64.urlsafe_b64encode(_canonical(identity).encode("utf-8")).decode("ascii").rstrip("=")
            independent = len(occurrences[point["id"]]) == 1 and _paths_for_value(native, point["id"]) == [point["path"] + ("ID",)]
            handle = {"handle_ref": token, "handle_uuid": point["id"], "role": role,
                      "xy_mm": point["xy_mm"], "independently_addressable": independent}
            curve["handles"].append(handle)
            targets[token] = {"identity": identity, "native_ids": {"shape_uuid": item["shape_uuid"],
                              "line_uuid": item["line_uuid"], "handle_uuid": point["id"]},
                              "point_path": point["path"], "current_xy_mm": point["xy_mm"],
                              "fixed_endpoints": copy.deepcopy(curve["endpoints"]), "independent": independent}
        curves.append(curve)
    return {"public": {"pattern_uuid": pattern_uuid, "pattern_name": pattern.get("Name"), "unit": "mm",
                       **fingerprints, "curves": curves, "unsupported_lines": unsupported},
            "targets": targets, "outer_endpoints": endpoint_positions}


def describe_native(native: dict, pattern_uuid: str, *, scene_scope=None) -> dict:
    return _analyse(native, pattern_uuid, scene_scope)["public"]


def prepare_edit(native: dict, pattern_uuid: str, handle_ref: str, target_xy_mm,
                 *, tolerance_mm: float = .002, scene_scope=None) -> dict:
    target_xy = _xy(target_xy_mm, "target_xy_mm")
    tolerance = _tolerance(tolerance_mm)
    if type(handle_ref) is not str or not handle_ref.startswith(_PREFIX) or len(handle_ref) > 4096:
        raise CurveError("INVALID_REFERENCE", "Use a handle_ref returned by curves.inspect")
    try:
        token = handle_ref[len(_PREFIX):]
        identity = _loads(base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CurveError("INVALID_REFERENCE", "Malformed handle_ref") from exc
    if type(identity) is not dict or set(identity) != _REF_KEYS or identity.get("version") != 2:
        raise CurveError("INVALID_REFERENCE", "Unexpected handle_ref schema")
    if identity.get("pattern_uuid") != pattern_uuid:
        raise CurveError("WRONG_OWNER", "The handle reference belongs to another pattern")
    analysis = _analyse(native, pattern_uuid, scene_scope)
    selected = analysis["targets"].get(handle_ref)
    if selected is None:
        raise CurveError("STALE_REFERENCE", "Scene, geometry or topology changed; read curves.inspect again")
    if not selected["independent"]:
        raise CurveError("SHARED_HANDLE", "The handle ID is shared or referenced elsewhere; independent editing is unproven")
    return {**copy.deepcopy(selected), "target_xy_mm": target_xy, "tolerance_mm": tolerance,
            "before_scene_fingerprint": analysis["public"]["scene_fingerprint"]}


def _at(value, path):
    for part in path:
        value = value[part]
    return value


def verify_edit(before: dict, after: dict, plan: dict) -> dict:
    """Actual native data decides acceptance, irrespective of writer return values."""
    try:
        a, _ = _identity_graph(before)
        b, _ = _identity_graph(after)
        old, new = _at(a, plan["point_path"]), _at(b, plan["point_path"])
        identity_preserved = old["ID"] == new["ID"] == plan["identity"]["handle_key"]
        actual = _xy([new["Position"]["x"], new["Position"]["y"]], "readback")
        error = math.dist(actual, plan["target_xy_mm"])
        old_position, new_position = copy.deepcopy(old["Position"]), copy.deepcopy(new["Position"])
        for point in (old, new):
            point["Position"]["x"] = "<edited-x>"
            point["Position"]["y"] = "<edited-y>"
        all_other_data = _digest(a) == _digest(b)
        semantics_preserved = all_other_data
        derived = None
        if not all_other_data:
            from md_mcp_plugin.embedded.curve_derived import normalize
            old["Position"], new["Position"] = old_position, new_position
            normalized, derived = normalize(a, b, plan["point_path"], tolerance_mm=plan["tolerance_mm"])
            for point in (old, _at(normalized, plan["point_path"])):
                point["Position"]["x"] = "<edited-x>"
                point["Position"]["y"] = "<edited-y>"
            semantics_preserved = derived["verified"] and _digest(a) == _digest(normalized)
        return {"verified": identity_preserved and semantics_preserved and error <= plan["tolerance_mm"],
                "handle_identity_preserved": identity_preserved, "target_reached": error <= plan["tolerance_mm"],
                "actual_xy_mm": actual, "target_error_mm": error,
                "fixed_endpoints_and_other_native_data_unchanged": all_other_data,
                "fixed_endpoints_and_other_semantics_preserved": semantics_preserved,
                "derived_native_data_verification": derived,
                "scene_unchanged": _digest(_identity_graph(before)[0]) == _digest(_identity_graph(after)[0])}
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return {"verified": False, "handle_identity_preserved": False, "target_reached": False,
                "fixed_endpoints_and_other_native_data_unchanged": False, "scene_unchanged": False,
                "reason": "Readback has missing, malformed or reordered geometry"}


def _identities(api) -> list[dict]:
    count = api.GetPatternCount()
    if type(count) is not int or count < 0:
        raise CurveError("INVALID_NATIVE_DATA", "Invalid native pattern count")
    rows = [_loads(api.GetPatternInformation(i)) for i in range(count)]
    if any(type(row) is not dict for row in rows):
        raise CurveError("INVALID_NATIVE_DATA", "Invalid pattern identity data")
    return rows


def _endpoint_binding(raw, expected: dict, name) -> dict:
    rows = raw.get("Pattern InputInformation") if type(raw) is dict else None
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict or rows[0].get("Pattern name") != name:
        raise CurveError("OWNER_MISMATCH", "Current queried PointInfo does not corroborate the pattern name")
    # Native Pattern index may report local zero. Only the fresh UUID-resolved
    # query index, name and unique endpoint positions bind this observation.
    points = rows[0].get("PointList", [])
    if type(points) is not list:
        raise CurveError("INVALID_NATIVE_DATA", "Native PointInfo has no PointList")
    points = [q for q in points if type(q) is dict and "Point index" in q]
    mapping = {}
    for point_id, xy in expected.items():
        matches = []
        for point in points:
            coords = _xy([point.get("Point positionX"), point.get("Point positionY")], "native endpoint")
            if math.dist(coords, xy) <= .0002:
                index = point["Point index"]
                if type(index) not in (str, int) or not str(index).isdigit():
                    raise CurveError("INVALID_NATIVE_DATA", "Native endpoint index is not an integer")
                matches.append(int(index))
        if len(matches) != 1:
            return {"verified": False, "reason": "Native endpoints do not have a unique position bijection"}
        mapping[point_id] = matches[0]
    verified = len(points) == len(mapping) == len(set(mapping.values()))
    return {"verified": verified, "endpoint_count": len(mapping), "handle_native_indices_inferred": False}


def _read_current(pattern_uuid: str) -> dict:
    import pattern_api as p
    import utility_api as u
    _id(pattern_uuid, "pattern_uuid")
    before = _identities(p)
    matches = [i for i, row in enumerate(before) if row.get("uuid") == pattern_uuid]
    if len(matches) != 1:
        raise CurveError("PATTERN_NOT_UNIQUE", "pattern_uuid must resolve once in the current project")
    index = matches[0]
    project = u.GetProjectFilePath()
    if type(project) is not str:
        raise CurveError("INVALID_NATIVE_DATA", "Current project path must be a string")
    scope = {"pid": os.getpid(), "epoch": _state()["epoch"], "project": project}
    with tempfile.TemporaryDirectory(prefix="md_mcp_curve_read_") as folder:
        path = Path(folder) / "patterns.json"
        accepted = p.ExportPatternJSON(str(path))
        if accepted is not True or not path.is_file() or not path.stat().st_size:
            raise CurveError("NATIVE_READ_FAILED", "ExportPatternJSON did not produce readable native data")
        native = _loads(path.read_text(encoding="utf-8-sig"))
    analysis = _analyse(native, pattern_uuid, scope)
    if analysis["public"]["pattern_name"] != before[index].get("name"):
        raise CurveError("OWNER_MISMATCH", "Native UUID and runtime pattern names differ")
    pointinfo = _loads(p.GetPatternInputInformation(index))
    binding = _endpoint_binding(pointinfo, analysis["outer_endpoints"], before[index].get("name"))
    if before != _identities(p) or u.GetProjectFilePath() != project:
        raise CurveError("SCENE_CHANGED", "Project or pattern identities changed during native read")
    return {"native": native, "scope": scope, "runtime_index": index, "identities": before,
            "public": {**analysis["public"], "native_endpoint_binding": binding}}


def _response(status: str, result=None, code=None, message=None) -> dict:
    return {"status": status, "result": result,
            "error": {"code": code, "message": message} if code else None}


def _error(exc: Exception, *, result=None) -> dict:
    code = exc.code if isinstance(exc, CurveError) else "NATIVE_READ_FAILED"
    return _response("failed", result, code, str(exc))


def _params(params, required: set, optional: set = frozenset()) -> None:
    if type(params) is not dict or not required <= params.keys() or params.keys() - required - optional:
        raise CurveError("INVALID_INPUT", "Unexpected or missing operation parameters")


def inspect_handles(params: dict) -> dict:
    try:
        _params(params, {"pattern_uuid"})
        return _response("succeeded", _read_current(params["pattern_uuid"])["public"])
    except Exception as exc:
        return _error(exc)


def _metadata(fn) -> dict:
    signature = None
    if callable(fn):
        try:
            signature = str(inspect.signature(fn))
        except (TypeError, ValueError):
            pass
    return {"available": callable(fn), "signature": signature, "doc": (getattr(fn, "__doc__", None) or "")[:2000]}


def runtime_capabilities(params: dict) -> dict:
    try:
        _params(params, set())
        import pattern_api as p
        names = {"GetPatternCount", "GetPatternInformation", "GetPatternInputInformation", "ExportPatternJSON", "MovePatternPoint"}
        candidates = [n for n in dir(p) if re.search(r"Bezier|ControlPoint|CurveHandle|Tangent", n, re.I)]
        symbols = {name: _metadata(getattr(p, name, None)) for name in sorted(names | set(candidates))}
        writer = _state()["writer"]
        return _response("succeeded", {"symbols": symbols,
            "writer": {"name": writer["name"], "evidence": writer["evidence"], "status": "unverified"} if writer else None,
            "independent_handle_write": "unverified" if writer else "unsupported",
            "method_presence_is_execution_proof": False, "candidate_setters_called": 0,
            "live_validation": "pending"})
    except Exception as exc:
        return _error(exc)


def set_handles(params: dict) -> dict:
    """One independent handle per transaction; no retry, import or save fallback."""
    try:
        _params(params, {"pattern_uuid", "handle_ref", "target_xy_mm"}, {"tolerance_mm"})
        _id(params["pattern_uuid"], "pattern_uuid")
        _xy(params["target_xy_mm"], "target_xy_mm")
        _tolerance(params.get("tolerance_mm", .002))
        before = _read_current(params["pattern_uuid"])
        plan = prepare_edit(before["native"], params["pattern_uuid"], params["handle_ref"], params["target_xy_mm"],
                            tolerance_mm=params.get("tolerance_mm", .002), scene_scope=before["scope"])
        if not before["public"]["native_endpoint_binding"]["verified"]:
            raise CurveError("AMBIGUOUS_REFERENCE", "Native endpoint binding is ambiguous; no writer called")
        if plan["current_xy_mm"] == plan["target_xy_mm"]:
            return _response("succeeded", {"write_attempted": False, "changed": False,
                             "verification": verify_edit(before["native"], before["native"], plan),
                             "readback": before["public"]})
        writer = _state()["writer"]
        if writer is None:
            return _response("unsupported", {"write_attempted": False, "changed": False, "readback": before["public"]},
                             "NO_HANDLE_WRITER", "No verified generic handle-ID/native-XY writer is installed; fixed viewport macros are not used")
    except Exception as exc:
        return _error(exc, result={"write_attempted": False, "automatic_retry_allowed": False})
    provider_error = None
    provider_receipt = None
    try:
        # Neither the internal JSON path nor user code is part of the provider API.
        provider_receipt = writer["call"]({"pattern_uuid": params["pattern_uuid"], "pattern_runtime_index": before["runtime_index"],
                        "scene_scope": copy.deepcopy(before["scope"]), "reference": {**copy.deepcopy(plan["identity"]), **plan["native_ids"]},
                        "current_xy_mm": plan["current_xy_mm"][:], "target_xy_mm": plan["target_xy_mm"][:],
                        "fixed_endpoints": copy.deepcopy(plan["fixed_endpoints"]), "tolerance_mm": plan["tolerance_mm"]})
    except Exception as exc:
        provider_error = {"type": type(exc).__name__, "message": str(exc)}
    typed = (isinstance(provider_receipt, dict)
             and str(provider_receipt.get("status", "")).upper() in {"SUCCEEDED", "NO_OP", "REJECTED", "FAILED", "UNSUPPORTED", "PARTIAL", "UNKNOWN"}
             and type(provider_receipt.get("effects_started")) is bool
             and type(provider_receipt.get("requires_recovery")) is bool)
    effects_started = provider_receipt["effects_started"] if typed else True
    try:
        after = _read_current(params["pattern_uuid"])
        verification = verify_edit(before["native"], after["native"], plan)
        same_scope = before["scope"] == after["scope"] and before["identities"] == after["identities"]
        verification["scene_identity_preserved"] = same_scope
        verification["verified"] = verification["verified"] and same_scope
        result = {"provider_called": True, "write_attempted": effects_started, "provider": writer["name"], "provider_error": provider_error,
                  "verification": verification, "readback": after["public"],
                  "requires_recovery": (provider_error is not None or not same_scope
                      or not verification.get("fixed_endpoints_and_other_semantics_preserved", verification["fixed_endpoints_and_other_native_data_unchanged"])
                      or (not verification["target_reached"] and not verification["scene_unchanged"])),
                  "automatic_retry_allowed": False}
        if typed:
            result["provider_receipt"] = provider_receipt
            provider_status = provider_receipt["status"].upper()
            result["requires_recovery"] = result["requires_recovery"] or provider_receipt["requires_recovery"]
            derived = verification.get('derived_native_data_verification') or {}
            if (provider_status == 'PARTIAL' and (provider_receipt.get('error') or {}).get('code') == 'NON_TARGET_NATIVE_CHANGE'
                    and derived.get('reason') == 'Physical sewing endpoint moved beyond tolerance'):
                result['rollback_native_fingerprints'] = {
                    key: before['public'][key] for key in NATIVE_FINGERPRINT_FIELDS
                }
            if not effects_started and not verification["scene_unchanged"]:
                result.update(write_attempted=True, requires_recovery=True)
                reply = _response("unknown", result, "PROVIDER_EFFECT_MISMATCH", "Provider reported zero effects but independent native readback changed")
                reply.update(effects_started=True, requires_recovery=True)
                return reply
            if provider_status in {"PARTIAL", "UNKNOWN"} or provider_receipt["requires_recovery"]:
                reply = _response(provider_status.lower() if provider_status in {"PARTIAL", "UNKNOWN"} else "partial", result,
                                  "HANDLE_PROVIDER_RECOVERY_REQUIRED", "Provider batch did not complete; native target readback cannot certify cleanup")
                reply.update(effects_started=effects_started, requires_recovery=True)
                return reply
            if (provider_status in {'UNSUPPORTED','REJECTED','FAILED'}
                    and provider_receipt.get('geometry_effects_started') is False
                    and provider_receipt.get('closed_input_batch_verified') is True
                    and verification['scene_unchanged'] and same_scope):
                result['requires_recovery']=False
                error=provider_receipt.get('error') or {}
                reply=_response(provider_status.lower(),result,error.get('code','PREPARATION_REJECTED'),error.get('message','Closed UI preparation stopped before geometry input'))
                reply.update(effects_started=effects_started,requires_recovery=False)
                return reply
            if not effects_started and provider_status in {"UNSUPPORTED", "REJECTED", "FAILED"}:
                result["requires_recovery"] = False
                error = provider_receipt.get("error") or {}
                reply = _response(provider_status.lower(), result, error.get("code", "HANDLE_PROVIDER_REJECTED"), error.get("message", "Provider rejected before event dispatch"))
                reply.update(effects_started=False, requires_recovery=False)
                return reply
            if provider_status not in {"SUCCEEDED", "NO_OP"}:
                result["requires_recovery"] = effects_started
                reply = _response("failed", result, "HANDLE_PROVIDER_FAILED", "Native readback does not erase a failed provider batch")
                reply.update(effects_started=effects_started, requires_recovery=effects_started)
                return reply
            if provider_status == 'SUCCEEDED' and provider_receipt.get('closed_input_batch_verified') is True and verification['scene_unchanged'] and not verification['target_reached']:
                result['requires_recovery'] = False
                reply = _response('no_op', result, 'NO_NATIVE_HANDLE_RESPONSE', 'The complete input batch made no native 2D change; target was not reached')
                reply.update(effects_started=effects_started, requires_recovery=False)
                return reply
        if verification["verified"] and provider_error is None:
            return _response("succeeded", result)
        return _response("failed", result, "HANDLE_READBACK_REJECTED", "Handle target or preservation verification failed; inspect readback before further edits")
    except Exception as exc:
        uncertain = effects_started or provider_error is not None
        reply = _response("unknown" if uncertain else "failed", {"write_attempted": effects_started, "provider": writer["name"], "provider_error": provider_error,
                         "provider_receipt": provider_receipt, "requires_recovery": uncertain, "automatic_retry_allowed": False},
                         "HANDLE_READBACK_UNAVAILABLE", str(exc))
        reply.update(effects_started=effects_started, requires_recovery=uncertain)
        return reply


OPERATIONS = {"curves.inspect": inspect_handles, "curves.runtime_capabilities": runtime_capabilities,
              "curves.set_handles": set_handles}
