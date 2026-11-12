"""Opt-in, bounded partial gold-anchor registration for Cut.

This only fits the observed anchors. The caller still needs independently
verified current-frame owner outlines, target stroke and geometric clearance.
"""

from fractions import Fraction
from hashlib import sha256
from itertools import combinations, permutations, product

from .curve_partial_projection import _gold_markers
from .curve_projection import _markers, decode_png
from .cut_frame_projection import _apply, _canonical, _distance2, _fit, _id, _q, _xy


_HYPOTHESIS_LIMIT = 20_000
_SCHEMA = "md-cut-partial-anchor-projection/1"


def _area2(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _solve(png_bytes, anchors, device_pixel_ratio, residual_px, result):
    dpr, tolerance = _q(device_pixel_ratio), _q(residual_px)
    if not 0 < dpr <= 8 or not 0 < tolerance <= Fraction(3, 2):
        raise ValueError("Bounded DPR and residual required")
    if type(anchors) is not list or not 4 <= len(anchors) <= 16 or any(type(a) is not dict for a in anchors):
        raise ValueError("Complete set of 4..16 native outline anchors required")
    anchors = sorted(anchors, key=lambda a: _id(a.get("id")))
    identities = [a["id"] for a in anchors]
    source = [_xy(a.get("xy_mm")) for a in anchors]
    if len(set(identities)) != len(source) or len(set(source)) != len(source):
        raise ValueError("Distinct native anchor identities and positions required")

    image = decode_png(png_bytes)
    markers = _gold_markers(image) if dpr == 1 else _markers(image, "yellow", marker_scale=float(dpr))
    markers.sort(key=lambda marker: (marker["xy"], marker["bbox"]))
    result.update(frame_sha256=sha256(png_bytes).hexdigest(),
                  anchors_sha256=sha256(_canonical(anchors).encode()).hexdigest(),
                  device_pixel_ratio=float(dpr), image_size_px=[image["width"], image["height"]],
                  anchor_count=len(source), gold_marker_count=len(markers), gold_markers=markers)
    if not 3 <= len(markers) < len(source):
        return dict(result, reason="PARTIAL_GOLD_COUNT_UNSUPPORTED")

    target = [_xy(marker["xy"]) for marker in markers]
    epsilon2 = (tolerance * dpr) ** 2
    target_pairs = list(combinations(range(len(target)), 2))
    if any(_distance2(target[i], target[j]) <= 100 * epsilon2 for i, j in target_pairs):
        return dict(result, reason="PARTIAL_GOLD_NEIGHBOURHOODS_OVERLAP")
    left, right = max(target_pairs, key=lambda pair: _distance2(target[pair[0]], target[pair[1]]))
    a, b = target[left], target[right]
    if all(_area2(a, b, p) ** 2 <= 16 * epsilon2 * _distance2(a, b) for p in target):
        return dict(result, reason="PARTIAL_GOLD_NEAR_COLLINEAR")

    # With longest observed chord D > 10*epsilon, a valid source-to-marker
    # correspondence lies within 2*epsilon + 2*epsilon*(D+2*epsilon)/
    # (D-2*epsilon) < 5*epsilon of each corresponding pair-seeded prediction.
    # Enumerate every such assignment, including crowded candidate buckets.
    contender = None
    assignment_count = 0
    seed_count = 0
    for i, j in permutations(range(len(source)), 2):
        for reflected in (False, True):
            seed_count += 1
            seed = _fit((source[i], source[j]), (a, b), reflected)
            predicted = [_apply(seed, p) for p in source]
            options = []
            for index, marker in enumerate(target):
                if index == left:
                    options.append([i])
                elif index == right:
                    options.append([j])
                else:
                    options.append([k for k, p in enumerate(predicted) if k not in (i, j)
                                    and _distance2(p, marker) <= 25 * epsilon2])
            for assignment in product(*options):
                if assignment_count >= _HYPOTHESIS_LIMIT:
                    return dict(result, reason="PARTIAL_GOLD_HYPOTHESIS_BUDGET",
                                seed_count_examined=seed_count, assignments_examined=assignment_count)
                assignment_count += 1
                if len(set(assignment)) != len(target):
                    continue
                assigned = [source[k] for k in assignment]
                if all(_area2(source[i], source[j], p) == 0 for p in assigned):
                    continue
                fitted = _fit(assigned, target, reflected)
                errors = [_distance2(_apply(fitted, p), q) for p, q in zip(assigned, target)]
                # If every error can be <= epsilon, the minimum sum of
                # squared errors must be <= count*epsilon^2. Keep alternatives
                # that cannot be excluded even if their least-squares max fails.
                if sum(errors) <= len(target) * epsilon2:
                    if contender is not None:
                        return dict(result, reason="AMBIGUOUS_PARTIAL_GOLD_SIMILARITY",
                                    seed_count_examined=seed_count, assignments_examined=assignment_count)
                    contender = reflected, assignment, fitted, errors
    counts = {"seed_count_examined": seed_count, "assignments_examined": assignment_count}
    if contender is None or max(contender[3]) > epsilon2:
        return dict(result, **counts, reason="NO_COMPLETE_OBSERVED_GOLD_ASSIGNMENT")
    reflected, assignment, fitted, errors = contender
    logical = [v / dpr for v in fitted]
    return dict(result, **counts, reason="UNIQUE_PARTIAL_GOLD_GEOMETRY_ONLY",
                projection_geometry_verified=True, reflected=reflected, all_observed_gold_accounted=True,
                matrix_native_mm_to_logical=[float(v) for v in logical],
                matrix_native_mm_to_logical_exact=[str(v) for v in logical],
                maximum_residual_squared_logical_px=str(max(errors) / (dpr * dpr)),
                unobserved_anchor_ids=[ident for i, ident in enumerate(identities) if i not in assignment],
                anchor_pairs=[{"id": identities[k], "native_xy_mm": anchors[k]["xy_mm"],
                               "gold_index": i, "gold_xy_px": markers[i]["xy"]}
                              for i, k in enumerate(assignment)])


def solve_partial_anchor_projection(png_bytes, anchors, *, device_pixel_ratio=1.0, residual_px=.75):
    """Fit all detected gold dots against the whole current owner anchor set."""
    result = {"schema": _SCHEMA, "projection_geometry_verified": False,
              "semantic_line_binding_verified": False, "native_mm_method_contract_verified": False,
              "dispatch_authorized": False, "blue_used": False, "full_bijection": False}
    try:
        return _solve(png_bytes, anchors, device_pixel_ratio, residual_px, result)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return dict(result, reason="INVALID_PARTIAL_GOLD_SCOPE", error=str(exc))
