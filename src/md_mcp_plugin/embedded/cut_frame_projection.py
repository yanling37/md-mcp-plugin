"""Anchor-only, full-bijection similarity math. No SDK/UI/input operations.

An unlabelled symmetric frame is ambiguous. Blue handles are never consulted.
Geometry fit is not native line selection or an authorization to dispatch Cut.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import itertools
import json
import math

from . import curve_projection as _png
from .curve_partial_projection import _gold_markers


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _q(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10_000_000:
        raise ValueError("Finite bounded JSON number required")
    return Fraction(str(value))


def _id(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 512:
        raise ValueError("Fresh bounded native endpoint ID required")
    return value


def _xy(value):
    if type(value) is not list or len(value) != 2:
        raise ValueError("Complete XY pair required")
    return tuple(_q(x) for x in value)


def _distance2(a, b):
    return (a[0]-b[0])**2 + (a[1]-b[1])**2


def outer_endpoint_anchors(native, pattern_uuids=None):
    """Collect every whole-outline endpoint, retaining all native declarations.

    At each closed degree-2 join, two declarations at identical coordinates
    form one geometric anchor. Different PointIDs are retained as aliases of
    position only, never asserted to be the same native object. Nonincident
    coincident vertices, open/disconnected outlines or ID/position conflict fail.
    """
    papers = native.get("PatternList") if type(native) is dict else None
    if type(papers) is not list or not 1 <= len(papers) <= 4096:
        raise ValueError("Complete bounded native PatternList required")
    by_id = {}
    for paper in papers:
        if type(paper) is not dict:
            raise ValueError("Native paper required")
        key = _id(paper.get("ID"))
        if key in by_id:
            raise ValueError("Ambiguous owner")
        by_id[key] = paper
    requested = set(by_id) if pattern_uuids is None else set(pattern_uuids)
    if (pattern_uuids is not None and (type(pattern_uuids) is not list or len(pattern_uuids) != len(requested))) or not requested or not requested <= set(by_id):
        raise ValueError("Explicit current owner set required")
    result = []
    for owner in sorted(requested):
        shape = by_id[owner].get("ShapeInfo")
        if type(shape) is not dict or shape.get("IsClosed") is not True:
            raise ValueError("Explicitly closed whole outline required")
        shape_id = _id(shape.get("ID")); lines = shape.get("LineList")
        if type(lines) is not list or not 3 <= len(lines) <= 4096:
            raise ValueError("Bounded complete outer LineList required")
        nodes, edges, point_positions, line_ids = {}, [], {}, set()
        for line in lines:
            line_id = _id(line.get("ID"))
            if line_id in line_ids:
                raise ValueError("Ambiguous outer line")
            line_ids.add(line_id); points = line.get("PointList")
            if type(points) is not list or len(points) < 2:
                raise ValueError("Complete native line endpoints required")
            ends = []
            for side, point in (("start", points[0]), ("end", points[-1])):
                point_id = _id(point.get("ID")); position = point.get("Position")
                if type(position) is not dict:
                    raise ValueError("Native endpoint Position required")
                xy = (_q(position.get("x")), _q(position.get("y")))
                if point_id in point_positions and point_positions[point_id] != xy:
                    raise ValueError("Same native PointID has different coordinates")
                point_positions[point_id] = xy; ends.append(xy)
                nodes.setdefault(xy, []).append({"line_uuid": line_id, "point_uuid": point_id, "endpoint": side})
            if ends[0] == ends[1]:
                raise ValueError("Self-loop outer edge is not a unique anchor graph")
            edges.append(tuple(ends))
        if any(len(refs) != 2 or refs[0]["line_uuid"] == refs[1]["line_uuid"] for refs in nodes.values()):
            raise ValueError("Outline must have exactly two distinct incident endpoints per geometric anchor")
        reached = {min(nodes)}
        while True:
            expanded = reached | {b for a, b in edges if a in reached} | {a for a, b in edges if b in reached}
            if expanded == reached:
                break
            reached = expanded
        if reached != set(nodes):
            raise ValueError("Disconnected whole outline")
        for xy, refs in sorted(nodes.items()):
            refs = sorted(refs, key=lambda r: (r["line_uuid"], r["endpoint"], r["point_uuid"]))
            identity = owner + ":" + hashlib.sha256(_canonical(refs).encode()).hexdigest()[:24]
            result.append({"id": identity, "pattern_uuid": owner, "shape_uuid": shape_id,
                           "xy_mm": [float(x) for x in xy], "endpoint_refs": refs,
                           "same_native_PointID": len({r["point_uuid"] for r in refs}) == 1})
    return result


def _fit(source, target, reflected):
    n = len(source)
    sx = sum((p[0] for p in source), Fraction()) / n; sy = sum((p[1] for p in source), Fraction()) / n
    tx = sum((p[0] for p in target), Fraction()) / n; ty = sum((p[1] for p in target), Fraction()) / n
    centered = [(x-sx, y-sy, u-tx, v-ty) for (x, y), (u, v) in zip(source, target)]
    denominator = sum((x*x+y*y for x, y, _, _ in centered), Fraction())
    if not denominator:
        raise ValueError("Degenerate anchors")
    if reflected:
        a = sum((x*u-y*v for x, y, u, v in centered), Fraction()) / denominator
        b = sum((x*v+y*u for x, y, u, v in centered), Fraction()) / denominator
        matrix = (a, b, b, -a, tx-a*sx-b*sy, ty-b*sx+a*sy)
    else:
        a = sum((x*u+y*v for x, y, u, v in centered), Fraction()) / denominator
        b = sum((x*v-y*u for x, y, u, v in centered), Fraction()) / denominator
        matrix = (a, -b, b, a, tx-a*sx+b*sy, ty-b*sx-a*sy)
    if not a*a+b*b:
        raise ValueError("Singular similarity")
    return matrix


def _apply(matrix, p):
    a, b, c, d, x, y = matrix
    return a*p[0]+b*p[1]+x, c*p[0]+d*p[1]+y


def solve_anchor_only_projection(png_bytes, anchors, *, device_pixel_ratio=1.0, residual_px=.75):
    """Exhaustive ordered-pair/parity seeds, full point fit, no subset/blue.

    Both direct and reflected similarities are candidates. A unique reflection
    is explicitly reported (native Y-up→screen Y-down can require it); multiple
    assignments/parities are rejected. Inputs must be bound fresh by the core.
    """
    try:
        dpr, tolerance = _q(device_pixel_ratio), _q(residual_px)
        if not 0 < dpr <= 8 or not 0 < tolerance <= Fraction(3, 2):
            raise ValueError("Bounded DPR and residual required")
        if type(anchors) is not list or not 3 <= len(anchors) <= 16 or any(type(a) is not dict for a in anchors):
            raise ValueError("All 3..16 current whole-outline anchors required")
        anchors = sorted(anchors, key=lambda a: _id(a.get("id")))
        if len({_id(a.get("id")) for a in anchors}) != len(anchors):
            raise ValueError("Duplicate anchor identity")
        source = [_xy(a.get("xy_mm")) for a in anchors]
        if len(set(source)) != len(source):
            raise ValueError("Unresolved coincident geometric anchors")
        if all((b[0]-source[0][0])*(c[1]-source[0][1]) == (b[1]-source[0][1])*(c[0]-source[0][0])
               for b, c in itertools.combinations(source[1:], 2)):
            raise ValueError("Collinear anchors cannot establish an unambiguous 2D parity")
        image = _png.decode_png(png_bytes)
        # At DPR 1 the full 3x3 core separates the real MD dots from thin
        # gold guide strokes that can connect the less dense components.
        markers = _gold_markers(image) if dpr == 1 else _png._markers(image, "yellow", marker_scale=float(dpr))
        markers.sort(key=lambda m: (m["xy"], m["bbox"]))
        result = {"schema": "md-cut-anchor-only-projection/1", "projection_geometry_verified": False,
                  "semantic_line_binding_verified": False, "native_mm_method_contract_verified": False,
                  "dispatch_authorized": False, "blue_used": False,
                  "frame_sha256": hashlib.sha256(png_bytes).hexdigest(),
                  "anchors_sha256": hashlib.sha256(_canonical(anchors).encode()).hexdigest(),
                  "anchor_count": len(source), "gold_marker_count": len(markers),
                  "image_size_px": [image["width"], image["height"]], "device_pixel_ratio": float(dpr),
                  "gold_markers": markers}
        if len(markers) != len(source):
            return dict(result, reason="INCOMPLETE_OR_EXTRA_GOLD_MARKERS")
        target = [_xy(m["xy"]) for m in markers]
        # A fitted endpoint may be epsilon from its marker. A two-endpoint
        # seed on the longest source chord differs by at most 3*epsilon over
        # every source point; include the point's own epsilon as well. Require
        # disjoint 4*epsilon neighborhoods so no valid alternate assignment
        # can disappear through the conservative nearest-marker step.
        bound2 = (tolerance*dpr)**2
        if any(_distance2(a, b) <= 64*bound2 for a, b in itertools.combinations(target, 2)):
            return dict(result, reason="GOLD_CORRESPONDENCE_NEIGHBOURHOODS_OVERLAP")
        pair = max(itertools.combinations(range(len(source)), 2), key=lambda ij: (_distance2(source[ij[0]], source[ij[1]]), ij))
        solutions = {}; seed_count = 0
        for i, j in itertools.permutations(range(len(target)), 2):
            for reflected in (False, True):
                seed_count += 1
                matrix = _fit([source[k] for k in pair], [target[i], target[j]], reflected)
                neighbours = [[k for k, q in enumerate(target) if _distance2(_apply(matrix, p), q) <= 16*bound2] for p in source]
                if any(len(row) != 1 for row in neighbours):
                    continue  # Conservative; never resolve a crowded correspondence by a guess.
                assignment = tuple(row[0] for row in neighbours)
                if len(set(assignment)) != len(source):
                    continue
                matrix = _fit(source, [target[k] for k in assignment], reflected)
                errors = [_distance2(_apply(matrix, p), target[k]) for p, k in zip(source, assignment)]
                if max(errors) > bound2:
                    continue
                solutions[(reflected, assignment)] = matrix, errors
                if len(solutions) > 1:
                    return dict(result, reason="AMBIGUOUS_ANCHOR_SIMILARITY", candidate_count_lower_bound=2,
                                seed_count_examined=seed_count,
                                ambiguity_examples=[{"reflected": k[0], "gold_assignment": list(k[1])} for k in sorted(solutions)])
        if not solutions:
            return dict(result, reason="NO_COMPLETE_SIMILARITY", seed_count_examined=seed_count)
        (reflected, assignment), (matrix, errors) = next(iter(solutions.items()))
        logical = tuple(v/dpr for v in matrix)
        return dict(result, projection_geometry_verified=True, reason="UNIQUE_COMPLETE_ANCHOR_GEOMETRY_ONLY",
                    reflected=reflected, full_bijection=True, seed_count_examined=seed_count,
                    matrix_native_mm_to_logical=[float(v) for v in logical],
                    matrix_native_mm_to_logical_exact=[str(v) for v in logical],
                    scale_squared_logical_per_mm2=str(logical[0]**2+logical[2]**2),
                    maximum_residual_squared_logical_px=str(max(errors)/(dpr*dpr)),
                    anchor_pairs=[{"id": a["id"], "native_xy_mm": a["xy_mm"], "gold_index": k,
                                   "gold_xy_px": markers[k]["xy"], "residual_squared_logical_px": str(e/(dpr*dpr))}
                                  for a, k, e in zip(anchors, assignment, errors)])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"schema": "md-cut-anchor-only-projection/1", "projection_geometry_verified": False,
                "semantic_line_binding_verified": False, "dispatch_authorized": False, "blue_used": False,
                "reason": "INVALID_OR_DEGENERATE_ANCHOR_SCOPE", "error": str(exc)}
