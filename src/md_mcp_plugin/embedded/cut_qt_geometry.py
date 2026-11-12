"""Pure conditional native-line candidate; never a semantic hit certificate.

The caller must independently establish every current owner native-mm→logical
projection. A Qt viewportTransform is not such evidence. No OPERATIONS or UI.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import math


class CandidateError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _q(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10_000_000:
        raise CandidateError("Finite bounded JSON numbers required")
    return Fraction(str(value))


def _id(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 512:
        raise CandidateError("Current native ID required")
    return value


def _point(raw):
    pos = raw.get("Position") if type(raw) is dict else None
    if type(pos) is not dict:
        raise CandidateError("Complete native point Position required")
    return _q(pos.get("x")), _q(pos.get("y"))


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _norm2(a, b):
    return sum((a[i] - b[i]) ** 2 for i in (0, 1))


def _segment_distance2(p, a, b):
    denominator = _norm2(a, b)
    if not denominator:
        return _norm2(p, a)
    t = sum((p[i] - a[i]) * (b[i] - a[i]) for i in (0, 1)) / denominator
    t = max(Fraction(0), min(Fraction(1), t))
    return _norm2(p, tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)))


def _hull_distance2(p, points):
    """Exact lower bound to a line/cubic via its control convex hull."""
    values = sorted(set(points))
    if len(values) == 1:
        return _norm2(p, values[0])
    lower, upper = [], []
    for sequence, output in ((values, lower), (list(reversed(values)), upper)):
        for value in sequence:
            while len(output) >= 2 and _cross(output[-2], output[-1], value) <= 0:
                output.pop()
            output.append(value)
    hull = lower[:-1] + upper[:-1]
    if len(hull) == 2:
        return _segment_distance2(p, *hull)
    if all(_cross(a, b, p) >= 0 for a, b in zip(hull, hull[1:] + hull[:1])):
        return Fraction(0)
    return min(_segment_distance2(p, a, b) for a, b in zip(hull, hull[1:] + hull[:1]))


def _midpoint(points):
    if len(points) == 2:
        return tuple((points[0][i] + points[1][i]) / 2 for i in (0, 1))
    return tuple((points[0][i] + 3 * points[1][i] + 3 * points[2][i] + points[3][i]) / 8 for i in (0, 1))


def _round_qt_nonnegative(value):
    if value < 0:
        return -_round_qt_nonnegative(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _curves(native):
    papers = native.get("PatternList") if type(native) is dict else None
    if type(papers) is not list or not 1 <= len(papers) <= 4096:
        raise CandidateError("Complete bounded native PatternList required")
    owners, output = set(), []
    for paper in papers:
        if type(paper) is not dict:
            raise CandidateError("Native paper object required")
        owner = _id(paper.get("ID"))
        if owner in owners:
            raise CandidateError("Ambiguous native owner")
        owners.add(owner)
        internals = paper.get("InternalLineList", [])
        if type(internals) is not list:
            raise CandidateError("Complete InternalLineList required")
        shapes = [("outline", paper.get("ShapeInfo"))] + [("internal", q) for q in internals]
        shape_keys = set()
        for kind, shape in shapes:
            if type(shape) is not dict:
                raise CandidateError("Complete shape object required")
            shape_id = _id(shape.get("ID"))
            key = kind, shape_id
            if key in shape_keys:
                raise CandidateError("Ambiguous native shape")
            shape_keys.add(key)
            lines = shape.get("LineList")
            if type(lines) is not list:
                raise CandidateError("Complete LineList required")
            seen = set()
            for line in lines:
                if type(line) is not dict:
                    raise CandidateError("Native line object required")
                line_id = _id(line.get("ID"))
                if line_id in seen:
                    raise CandidateError("Ambiguous native line")
                seen.add(line_id)
                points = line.get("PointList")
                if type(points) is not list or len(points) not in (2, 4):
                    raise CandidateError("Only complete straight/cubic controls supported")
                kinds = {q.get("PointType") for q in points if type(q) is dict}
                if not ((len(points) == 2 and kinds <= {"Straight", "Polyline"})
                        or (len(points) == 4 and "Bezier Curve" in kinds and kinds <= {"Straight", "Bezier Curve"})):
                    raise CandidateError("Unsupported point representation")
                output.append({"owner": owner, "kind": kind, "shape": shape_id, "line": line_id,
                               "points": [_point(q) for q in points], "shape_type": shape.get("ShapeType"),
                               "closed": shape.get("IsClosed")})
                if len(output) > 100_000:
                    raise CandidateError("Native line limit")
    return owners, output


def conditional_line_candidate(native, pattern_uuid, shape_uuid, line_uuid, projections_by_owner,
                               viewport_size, *, clearance_px=4, endpoint_margin_px=8):
    """Choose fixed t=.5, conditional on complete externally established affine maps.

    projections_by_owner[actual_native_owner] is [a,b,c,d,tx,ty], mapping native
    2D mm (x,y) to logical (a*x+b*y+tx,c*x+d*y+ty). No map is inferred or marked
    verified here. All native curves, including baseline aliases, are obstacles;
    conservative false rejection is preferable to ignoring an unknown drawable.
    """
    target_key = (_id(pattern_uuid), _id(shape_uuid), _id(line_uuid))
    if (type(viewport_size) is not list or len(viewport_size) != 2
            or any(type(v) is not int or not 1 <= v <= 16384 for v in viewport_size)
            or type(clearance_px) is not int or not 2 <= clearance_px <= 32
            or type(endpoint_margin_px) is not int or not clearance_px <= endpoint_margin_px <= 128):
        raise CandidateError("Bounded logical viewport and conservative integer margins required")
    owners, curves = _curves(native)
    result = {"schema": "md-cut-qt-conditional-line-candidate/1", "candidate_available": False,
              "semantic_line_binding_verified": False, "native_mm_projection_verified": False,
              "MD_picker_radius_verified": False, "dispatch_authorized": False,
              "target": dict(zip(("pattern_uuid", "shape_uuid", "line_uuid"), target_key)),
              "native_json_sha256": hashlib.sha256(_canonical(native).encode()).hexdigest(),
              "viewport_size": viewport_size, "condition": "All supplied current per-owner native-mm→logical affine maps must be independently established",
              "clearance_px": clearance_px, "endpoint_margin_px": endpoint_margin_px}
    if type(projections_by_owner) is not dict or set(projections_by_owner) != owners:
        return dict(result, reason="INCOMPLETE_OWNER_PROJECTION_COVERAGE")
    maps = {}
    for owner, matrix in projections_by_owner.items():
        if type(matrix) is not list or len(matrix) != 6:
            raise CandidateError("Each affine map needs exactly six finite coefficients")
        values = [_q(v) for v in matrix]
        if values[0] * values[3] - values[1] * values[2] == 0:
            return dict(result, reason="SINGULAR_OWNER_PROJECTION")
        maps[owner] = values
    projected = []
    for curve in curves:
        a, b, c, d, tx, ty = maps[curve["owner"]]
        projected.append({**curve, "pixels": [(a*x+b*y+tx, c*x+d*y+ty) for x, y in curve["points"]]})
    target = [q for q in projected if (q["owner"], q["shape"], q["line"]) == target_key and q["kind"] == "internal"]
    if len(target) != 1:
        return dict(result, reason="TARGET_DECLARATION_NOT_UNIQUE")
    target = target[0]
    if type(target["shape_type"]) is not int or target["shape_type"] != 1 or target["closed"] is not False:
        return dict(result, reason="TARGET_NOT_OPEN_GENUINE_TYPE1")
    sample = _midpoint(target["pixels"])
    pixel = tuple(Fraction(_round_qt_nonnegative(v)) for v in sample)
    result.update(viewport_point=[int(v) for v in pixel], target_parameter="1/2",
                  projected_sample=[str(v) for v in sample], sample_rounding_error_squared_px=str(_norm2(pixel, sample)),
                  projection_matrices={owner: [str(v) for v in matrix] for owner, matrix in sorted(maps.items())})
    if any(not clearance_px <= pixel[i] <= viewport_size[i] - 1 - clearance_px for i in (0, 1)):
        return dict(result, reason="CANDIDATE_OUTSIDE_SAFE_VIEWPORT")
    endpoint2 = min(_norm2(pixel, target["pixels"][i]) for i in (0, -1))
    result["minimum_target_endpoint_distance_squared_px"] = str(endpoint2)
    if endpoint2 < endpoint_margin_px ** 2:
        return dict(result, reason="CANDIDATE_TOO_NEAR_ENDPOINT")
    obstacles = []
    for curve in projected:
        if curve is target:
            continue
        distance2 = _hull_distance2(pixel, curve["pixels"])
        obstacles.append({"pattern_uuid": curve["owner"], "shape_kind": curve["kind"], "shape_uuid": curve["shape"],
                          "line_uuid": curve["line"], "curve_distance_squared_lower_bound_px": str(distance2),
                          "bound_method": "exact_control_convex_hull"})
    result["obstacle_bounds"] = obstacles
    if any(Fraction(q["curve_distance_squared_lower_bound_px"]) < clearance_px ** 2 for q in obstacles):
        return dict(result, reason="PROJECTED_OTHER_LINE_NOT_SEPARATED")
    return dict(result, candidate_available=True, reason="CONDITIONAL_GEOMETRIC_CANDIDATE_ONLY")
