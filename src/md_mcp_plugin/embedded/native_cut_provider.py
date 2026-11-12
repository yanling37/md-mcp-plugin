"""Current-frame candidate and fresh verifier for the atomic Qt Cut route."""

from hashlib import sha256
import math
from pathlib import Path

from . import cut, cut_qt_atomic, curves, selection
from .common import digest
from .curve_projection import decode_png
from .cut_frame_projection import outer_endpoint_anchors, solve_anchor_only_projection
from .cut_partial_anchor_projection import solve_partial_anchor_projection
from .cut_qt import CutQtError
from .cut_qt_geometry import conditional_line_candidate


NAME = "current Mode1 native line projection"
EVIDENCE = ("Unique SDK-selected UUID and fresh native/PointInfo outline bijections; "
            "matching atomic/capture viewport observations; a unique complete or partial gold-anchor fit and "
            "same-frame outline and target stroke observations; conservative all-owner clearance.")
_TARGET = ("pattern_uuid", "shape_uuid", "line_uuid", "topology_fingerprint")
_LOCAL_VIEW_KEYS = {"view_ref", "native_view_ref", "view_fingerprint"}


def _reject(code, message):
    raise CutQtError(code, message, "UNSUPPORTED")


def _result(response, operation):
    result = response.get("result")
    if (response.get("status") != "SUCCEEDED" or response.get("effects_started") is not False
            or response.get("requires_recovery") is not False
            or type(result) is not dict or result.get("verified") is not True):
        error = response.get("error") or (result.get("error") if type(result) is dict else None) or {}
        _reject("CUT_FRAME_OBSERVATION_UNAVAILABLE", f"{operation} failed: {error.get('code', response.get('status'))}")
    return result


def _same_view(atomic, capture):
    # Both pinned bridges independently find the unique current QtView2D.
    # Their UUID registries and view hashes differ. Compare the rest in full.
    return ({k: v for k, v in atomic.items() if k not in _LOCAL_VIEW_KEYS}
            == {k: v for k, v in capture.items() if k not in _LOCAL_VIEW_KEYS})


def _frame(plan):
    from .dispatch import _module

    adapter = _module('native_setup').get_scene_adapter()
    before = _result(cut_qt_atomic.inspect_current(), "atomic Mode1 inspection")["after"]
    inspected = _result(adapter.inspect_2d({"budget_ms": 1500}), "2D capture inspection")
    view = inspected["after"]
    token = plan["scene_token"]
    if inspected.get("scene_token") != token or not _same_view(before, view):
        _reject("CUT_VIEW_BINDING_UNPROVEN", "Atomic and capture bridges must observe the same current 2D viewport")
    current = _result(adapter.capture_2d({
        "view_ref": view["view_ref"], "view_fingerprint": view["view_fingerprint"], "scene_token": token,
        "budget_ms": 1500,
    }), "2D frame capture")
    after = _result(cut_qt_atomic.inspect_current(), "post-capture atomic Mode1 inspection")["after"]
    tools = current.get("tools_after", {})
    if (current.get("scene_token_after") != token or before != after
            or current["before"] != view or current["after"] != view
            or tools.get("verified") is not True or type(tools.get("current_mode")) is not int
            or tools["current_mode"] != 1):
        _reject("CUT_FRAME_SCOPE_CHANGED", "Mode1, view and scene must be stable across this capture")
    image = current["image"]
    size, dpr = view["mapping"]["viewport_size"], view["mapping"]["device_pixel_ratio"]
    if (image["viewport_size"] != size or image["device_pixel_ratio"] != dpr
            or image["view_ref"] != view["view_ref"] or image["view_fingerprint"] != view["view_fingerprint"]
            or type(dpr) not in (int, float) or not 0 < dpr <= 8
            or any(abs(p - v*dpr) > .5 for p, v in zip((image["width"], image["height"]), size))):
        _reject("CUT_FRAME_DPR_MISMATCH", "The captured pixel dimensions must agree with the current logical viewport and DPR")
    path = Path(image["path"])
    if path.is_symlink() or not 24 <= path.stat().st_size <= 16 * 1024 * 1024:
        _reject("CUT_FRAME_PNG_INVALID", "The capture must be a bounded core-owned PNG")
    with path.open("rb") as file:
        data = file.read(16 * 1024 * 1024 + 1)
    if len(data) != image["byte_count"] or sha256(data).hexdigest() != image["sha256"]:
        _reject("CUT_FRAME_PNG_CHANGED", "Fresh capture bytes must match the bridge PNG receipt")
    scope = {k: before[k] for k in ("native_view_ref", "view_fingerprint", "mapping_fingerprint")}
    scope["scene_token"] = token
    return scope, data, size, dpr


def _selected(plan):
    current, rows, names = selection._read()
    selected = [row for row in rows if row["selected"]]
    if (current["scene_token"] != plan["scene_token"] or len(selected) != 1
            or selected[0]["pattern_uuid"] != plan["pattern_uuid"]
            or selected[0]["index"] != plan["current_pattern_index"]
            or names != [selected[0]["name"]]):
        _reject("CUT_OWNER_SELECTION_REQUIRED", "Select exactly the target SDK pattern UUID and use a current scene token")
    return current


def _projection_geometry(native):
    # The export declares the main outline's identity/closure on the paper.
    # ShapeType2 baselines may be sampled polylines, and every segment remains
    # an obstacle. This projection-only view leaves the native snapshot intact.
    papers = []
    for paper in native["PatternList"]:
        outline = paper["ShapeInfo"]
        if outline.get("IsSlashed") is True:
            _reject("CUT_SLASHED_OUTLINE_UNPROVEN", "A slashed outline needs a separate placement contract")
        outline = {"ID": paper["ID"], "IsClosed": paper.get("IsClosed"), **outline}
        shapes = []
        for shape in paper.get("InternalLineList", []):
            if shape.get("ShapeType") != 2:
                shapes.append(shape)
                continue
            lines = []
            for line in shape["LineList"]:
                points = line["PointList"]
                if (len(points) > 2 and all(p.get("PointType") == "Polyline" for p in points[1:-1])
                        and points[0].get("PointType") == points[-1].get("PointType") == "Straight"):
                    lines.extend({"ID": "baseline-segment:" + digest([line["ID"], i]), "PointList": [a, b]}
                                 for i, (a, b) in enumerate(zip(points, points[1:])))
                else:
                    lines.append(line)
            shapes.append({**shape, "LineList": lines})
        papers.append({**paper, "ShapeInfo": outline, "InternalLineList": shapes})
    return {**native, "PatternList": papers}


def _board(plan, native, current):
    if native.get("Unit") != "mm":
        _reject("CUT_BOARD_UNITS_UNPROVEN", "Native Pattern JSON must declare millimetres")
    projected_native = _projection_geometry(native)
    anchors = outer_endpoint_anchors(projected_native)
    papers = {paper["ID"]: paper for paper in native["PatternList"]}
    groups = {owner: [] for owner in papers}
    for anchor in anchors:
        groups[anchor["pattern_uuid"]].append(anchor)
    rows = {row["pattern_uuid"]: row for row in current["patterns"]}
    if rows.keys() != papers.keys():
        _reject("CUT_BOARD_OWNERS_CHANGED", "All native owner UUIDs must biject with fresh SDK rows")
    for owner, row in rows.items():
        paper = papers[owner]
        name = row["information"].get("name")
        if type(name) is not str or paper.get("Name") != name:
            _reject("CUT_BOARD_OWNER_NAME_MISMATCH", f"Native/SDK owner identity mismatch for {owner}")
        points = {anchor["id"]: anchor["xy_mm"] for anchor in groups[owner]}
        binding = curves._endpoint_binding(row.get("input_information"), points, name)
        if binding["verified"] is not True:
            _reject("CUT_BOARD_PI_UNPROVEN", f"Complete native/PI outer endpoint correspondence required for {owner}")
    # Re-resolve against this observation's UUID rows and PI, including the
    # unique runtime child, target line, endpoints, and topology fingerprint.
    from .cut_qt import _capture_geometry, _target
    capture = _capture_geometry(native, list(rows.values()))
    observed = _target(capture, *(plan[k] for k in _TARGET[:3]))
    if any(plan[k] != value for k, value in observed.items()):
        _reject("CUT_NATIVE_TARGET_CHANGED", "Fresh native/PI target does not match the core plan")
    return groups[plan["pattern_uuid"]], papers, projected_native


def _project(matrix, point, dpr):
    a, b, c, d, tx, ty = matrix
    x, y = point
    return ((a*x+b*y+tx)*dpr, (c*x+d*y+ty)*dpr)


def _raster_point(points, t):
    # De Casteljau also works for the straight-line case. Its penultimate
    # segment gives the tangent without estimating it from raster pixels.
    while len(points) > 2:
        points = [tuple(a + (b-a)*t for a, b in zip(p, q)) for p, q in zip(points, points[1:])]
    a, b = points
    dx, dy = b[0]-a[0], b[1]-a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        _reject("CUT_RASTER_TANGENT_UNKNOWN", "A projected stroke sample has no measurable direction")
    return (a[0]+dx*t, a[1]+dy*t), (-dy/length, dx/length)


def _rgb(image, center, normal, offset):
    x, y = [math.floor(a + b*offset + .5) for a, b in zip(center, normal)]
    width, height, channels = image["width"], image["height"], image["channels"]
    if not (0 <= x < width and 0 <= y < height):
        _reject("CUT_OWNER_FRAME_COVERAGE_UNPROVEN", "Current frame must contain the tested outline and line profiles")
    index = (y*width+x)*channels
    data = image["pixels"]
    if channels == 4 and data[index+3] < 240:
        _reject("CUT_RASTER_PROFILE_UNPROVEN", "Transparent pixels cannot establish a stroke profile")
    return data[index:index+3]


def _contrast(left, right):
    return max(abs(a-b) for a, b in zip(left, right))


def _has_profile(image, center, normal, dpr, *, outline, exact_center=False):
    left, right = [_rgb(image, center, normal, sign*3*dpr) for sign in (-1, 1)]
    # An outline can show a fill edge. The internal line needs a narrow
    # contrasting stroke within comparable fill on both sides.
    edge = _contrast(left, right)
    if outline and edge >= 24:
        near_left, near_right = [_rgb(image, center, normal, sign*1.5*dpr) for sign in (-1, 1)]
        if _contrast(near_left, near_right) >= 24:
            return True
    if not outline and edge > 18:
        return False
    for shift in ((0,) if exact_center else (0, -.5, .5, -1, 1)):
        middle = _rgb(image, center, normal, shift*dpr)
        if min(_contrast(left, middle), _contrast(right, middle)) >= 30:
            return True
    return False


def _check_raster(plan, papers, image, matrix, dpr, point):
    # Corroborate the same board placement for each owner, without borrowing a
    # selected owner's pixel map for an unobserved/occluded piece.
    hit = [v*dpr for v in point]
    count = 0
    for owner, paper in papers.items():
        projected = [[_project(matrix, p, dpr) for p in curve["points_mm"]]
                     for curve in cut._curves(paper["ShapeInfo"])]
        controls = [p for line in projected for p in line]
        if owner != plan["pattern_uuid"] and all(
                min(p[i] for p in controls) - 3*dpr <= hit[i] <= max(p[i] for p in controls) + 3*dpr
                for i in (0, 1)):
            _reject("CUT_OWNER_OCCLUSION_UNPROVEN", "Another owner's projected outline envelope covers the candidate")
        for line in projected:
            for t in (.25, .5, .75):
                center, normal = _raster_point(line, t)
                if not _has_profile(image, center, normal, dpr, outline=True):
                    _reject("CUT_BOARD_RASTER_UNPROVEN", f"Fresh frame did not corroborate the shared outline placement of {owner}")
                count += 1
    line = [_project(matrix, p, dpr) for p in plan["curve"]["points_mm"]]
    samples = [_raster_point(line, t) for t in (.4, .45, .5, .55, .6)]
    if any(math.dist(a[0], b[0]) < 2*dpr for a, b in zip(samples, samples[1:])):
        _reject("CUT_TARGET_STROKE_TOO_SHORT", "The central target stroke cannot be resolved at this zoom")
    for i, (center, normal) in enumerate(samples):
        if not _has_profile(image, hit if i == 2 else center, normal, dpr, outline=False, exact_center=(i == 2)):
            _reject("CUT_TARGET_STROKE_UNPROVEN", "Fresh frame must show a separated stroke through the projected target pixel")
    return count


def _observe(plan, native):
    current = _selected(plan)
    anchors, papers, projected_native = _board(plan, native, current)
    scope, data, viewport_size, dpr = _frame(plan)
    if _selected(plan) != current:
        _reject("CUT_SDK_FRAME_CHANGED", "SDK owner selection or all-owner PI changed across the frame")
    projection = solve_anchor_only_projection(data, anchors, device_pixel_ratio=dpr)
    if projection.get('reason') == 'INCOMPLETE_OR_EXTRA_GOLD_MARKERS':
        projection = solve_partial_anchor_projection(data, anchors, device_pixel_ratio=dpr)
    if projection.get("projection_geometry_verified") is not True:
        _reject("CUT_ANCHOR_PROJECTION_UNPROVEN", projection["reason"])
    matrix = projection["matrix_native_mm_to_logical"]
    geometric = conditional_line_candidate(
        projected_native, *(plan[k] for k in _TARGET[:3]), {owner: matrix for owner in papers},
        viewport_size, clearance_px=8, endpoint_margin_px=12,
    )
    if geometric["candidate_available"] is not True:
        _reject("CUT_LINE_CANDIDATE_UNPROVEN", geometric["reason"])
    point = geometric["viewport_point"]
    profiles = _check_raster(plan, papers, decode_png(data), matrix, dpr, point)
    # ExportPatternJSON can regenerate ShapeType2 baseline IDs. Bind every
    # obstacle's exact projected distance as a multiset, with stable native
    # identities for the non-baseline shapes bound by the core scene hash.
    distances = sorted((r["pattern_uuid"], r["shape_kind"], r["curve_distance_squared_lower_bound_px"])
                       for r in geometric["obstacle_bounds"])
    # Each observation checks coherent PointID aliases. The replay binding uses
    # the stable outline line/end roles and positions rather than export-local IDs.
    anchor_positions = sorted((a["xy_mm"], [(r["line_uuid"], r["endpoint"]) for r in a["endpoint_refs"]])
                              for a in anchors)
    return scope, {
        "binding": {"native_view_ref": scope["native_view_ref"],
                    "view_fingerprint": scope["view_fingerprint"], "point": point},
        "evidence": {
            "target": {k: plan[k] for k in _TARGET},
            "scene_token": scope["scene_token"],
            "mapping_fingerprint": scope["mapping_fingerprint"],
            "frame_sha256": projection["frame_sha256"],
            "selected_anchor_sha256": digest(anchor_positions),
            "gold_projection": {key: projection[key] for key in ("schema", "anchor_count", "gold_marker_count", "full_bijection")},
            "shared_board_mm_to_logical": projection["matrix_native_mm_to_logical_exact"],
            "maximum_anchor_residual_squared_logical_px": projection["maximum_residual_squared_logical_px"],
            "owner_count": len(papers), "outline_profile_count": profiles,
            "all_owner_obstacle_bounds_sha256": digest(distances),
            "MD_picker_radius_verified": False,
        },
    }


def candidate(plan, native):
    return _observe(plan, native)[1]


def verify(plan, receipt, inspection, native):
    current, observed = _observe(plan, native)
    # _observe checks each PNG's own size, hash, view, anchors and raster
    # profiles. A new PNG can encode the same target/view evidence differently.
    candidate_evidence = {key: value for key, value in receipt["evidence"].items() if key != "frame_sha256"}
    fresh_evidence = {key: value for key, value in observed["evidence"].items() if key != "frame_sha256"}
    if current != inspection or observed["binding"] != receipt["binding"] or fresh_evidence != candidate_evidence:
        _reject("STALE_CUT_FRAME_CANDIDATE", "The recaptured frame must reproduce the current line, projection and candidate pixel")
    return {**{k: plan[k] for k in _TARGET}, **current,
            "selection_nonce": plan["selection_nonce"], "candidate_sha256": digest(receipt)}
