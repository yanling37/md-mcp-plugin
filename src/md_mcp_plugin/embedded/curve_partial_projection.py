"""Partial-visible anchor projection. Pure stdlib; no native API or dispatch.

Every observed gold dot must bind once to a native anchor. Missing native anchors
are reported explicitly. A unique visible blue target remains mandatory.
"""
import hashlib
import itertools
import math
import struct
import zlib

from .curve_projection import ProjectionError, _apply, _fit, _markers, _near, _points, decode_png
from .curve_projection import solve_projection as solve_full_projection


_NEIGHBOURS = tuple(itertools.product((-1, 0, 1), repeat=2))
_WIDE_NEIGHBOURS = tuple(itertools.product(range(-2, 3), repeat=2))


def _connected_cores(seeds, width, height):
    while seeds:
        first = seeds.pop()
        pending, component = [first], [first]
        while pending:
            i = pending.pop()
            x, y = i % width, i // width
            for dy, dx in _NEIGHBOURS:
                j = i + dy * width + dx
                if 0 <= x + dx < width and 0 <= y + dy < height and j in seeds:
                    seeds.remove(j)
                    pending.append(j)
                    component.append(j)
        yield component


def _filled_dot(component, image_width):
    xs, ys = [i % image_width for i in component], [i // image_width for i in component]
    width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    if len(component) >= 9 and max(width, height) <= 9 and min(width, height) / max(width, height) >= .5:
        return dict(xy=[sum(xs) / len(xs), sum(ys) / len(ys)], dense_core_count=len(component),
                    bbox=[min(xs), min(ys), max(xs), max(ys)])
    return None


def _gold_markers(image):
    """Full 3x3 gold cores detach filled dots from narrow anti-aliased guides.

    A thick outline can connect its own 3x3 core to an otherwise visible dot.
    Recover bounded filled dots from overlong cores using 23 of 25 occupied
    pixels. The resulting dots still need the complete observed bijection.
    """
    w, h, channels, data = [image[k] for k in ('width', 'height', 'channels', 'pixels')]
    mask = set()
    for i in range(w*h):
        at = i*channels
        r, g, b = data[at:at+3]
        if r >= 220 and g >= 200 and b <= 110 and (channels == 3 or data[at+3] >= 240):
            mask.add(i)
    if len(mask) > 200000:
        raise ValueError('Gold color occupancy exceeds bounded scope')
    seeds = {i for i in mask if 0 < i%w < w-1 and 0 < i//w < h-1 and
             all(i+dy*w+dx in mask for dy, dx in _NEIGHBOURS)}
    markers = []
    for component in _connected_cores(seeds, w, h):
        marker = _filled_dot(component, w)
        if marker is not None:
            markers.append(marker)
            continue
        xs, ys = [i % w for i in component], [i // w for i in component]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < 9:
            continue
        detached = {i for i in component if 2 <= i % w < w - 2 and 2 <= i // w < h - 2 and
                    sum(i + dy * w + dx in mask for dy, dx in _WIDE_NEIGHBOURS) >= 23}
        for core in _connected_cores(detached, w, h):
            marker = _filled_dot(core, w)
            if marker is not None:
                markers.append(dict(marker, detached_outline=True))
    if len(markers) > 32:
        raise ValueError('Too many full-density gold dots')
    return sorted(markers, key=lambda row: row['xy'])


def _noncollinear(points):
    i, j = max(itertools.combinations(range(len(points)), 2), key=lambda pair: math.dist(points[pair[0]], points[pair[1]]))
    diameter = math.dist(points[i], points[j])
    return diameter > 1e-8 and max(abs((p[0]-points[i][0])*(points[j][1]-points[i][1])
        -(p[1]-points[i][1])*(points[j][0]-points[i][0])) for p in points) > diameter*diameter*1e-8


def _solve(png_bytes, anchors, handles, target_handle_id, *, device_pixel_ratio=1.,
           residual_px=1.5, marker_radius_px=2., max_hypotheses=20000, _image=None, _blue=None):
    source, controls = _points(anchors, 'anchors', 4), _points(handles, 'handles', 1)
    if type(max_hypotheses) is not int or not 1 <= max_hypotheses <= 20000:
        raise ValueError('Hypothesis budget must be an integer in [1,20000]')
    if (type(device_pixel_ratio) not in (int, float) or not math.isfinite(device_pixel_ratio)
            or not 0 < device_pixel_ratio <= 8):
        raise ValueError('Actual capture DPR in (0,8] required')
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 3
           for v in (residual_px, marker_radius_px)):
        raise ValueError('Pixel qualification thresholds must be in (0,3]')
    target_indices = [i for i, row in enumerate(handles) if row['id'] == target_handle_id]
    if len(target_indices) != 1 or not _noncollinear(source):
        raise ValueError('Unique current target ID and noncollinear native anchors required')
    target_index = target_indices[0]
    image = decode_png(png_bytes) if _image is None else _image
    gold = _gold_markers(image)
    blue = _markers(image, 'blue') if _blue is None else _blue
    diagnostics = dict(yellow_candidates=gold, blue_candidates=blue,
                       image_size_px=[image['width'], image['height']], png_sha256=hashlib.sha256(png_bytes).hexdigest())
    def reject(code, **extra):
        raise ProjectionError(code, 'No unique complete observed-gold/visible-blue-target binding; zero input', dict(diagnostics, **extra))
    if not 4 <= len(gold) <= len(source) or not blue:
        reject('VISIBLE_MARKERS_INCOMPLETE', native_anchor_count=len(source), observed_gold_count=len(gold))
    observed = [row['xy'] for row in gold]
    if not _noncollinear(observed):
        reject('VISIBLE_ANCHORS_COLLINEAR')
    # The most distant OBSERVED pair is necessarily visible. Enumerating every
    # ordered native source pair covers every subset correspondence, even when
    # the most distant native anchor was occluded. <=32*31*2=1984 hypotheses.
    left, right = max(itertools.combinations(range(len(gold)), 2), key=lambda ij: math.dist(observed[ij[0]], observed[ij[1]]))
    solutions, tested = {}, 0
    for i, j in itertools.permutations(range(len(source)), 2):
        if math.dist(source[i], source[j]) <= 1e-8:
            continue
        for mirror in (1, -1):
            if tested >= max_hypotheses:
                reject('PROJECTION_HYPOTHESIS_BUDGET', hypotheses_tested=tested, hypothesis_budget=max_hypotheses)
            tested += 1
            model = _fit([source[i], source[j]], [observed[left], observed[right]], mirror)
            projected = [{'xy': _apply(model, p)} for p in source]
            neighbours = [_near(p, projected, residual_px*2) for p in observed]
            if any(len(row) != 1 for row in neighbours):
                continue
            assignment = tuple(row[0] for row in neighbours)
            if len(set(assignment)) != len(gold):
                continue
            subset = [source[k] for k in assignment]
            if not _noncollinear(subset):
                continue
            model = _fit(subset, observed, mirror)
            projected = [{'xy': _apply(model, p)} for p in source]
            if any(_near(point, projected, residual_px) != [k] for point, k in zip(observed, assignment)):
                continue
            errors = [math.dist(_apply(model, p), q) for p, q in zip(subset, observed)]
            visible = []
            for index, xy in enumerate(controls):
                prediction = _apply(model, xy)
                matched = _near(prediction, blue, marker_radius_px)
                if len(matched) == 1:
                    visible.append((index, matched[0], prediction))
            hit = [row for row in visible if row[0] == target_index]
            if len(hit) != 1 or sum(row[1] == hit[0][1] for row in visible) != 1:
                continue
            solutions[(assignment, mirror)] = (model, errors, visible, hit[0])
            if len(solutions) > 1:
                reject('NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION', solution_count=len(solutions), hypotheses_tested=tested)
    if len(solutions) != 1:
        reject('NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION', solution_count=len(solutions), hypotheses_tested=tested)
    (assignment, mirror), (model, errors, visible, target) = next(iter(solutions.items()))
    a, b, tx, ty, _ = model
    actual, dpr = blue[target[1]]['xy'], float(device_pixel_ratio)
    pairs = [dict(id=anchors[k]['id'], native_xy_mm=anchors[k]['xy_mm'],
                  marker_xy_px=observed[q], residual_px=error) for q, (k, error) in enumerate(zip(assignment, errors))]
    missing = [row['id'] for i, row in enumerate(anchors) if i not in assignment]
    return dict(verified=True, unique=True, input_calls=0, native_dispatch_authorized=False,
        linear=[[a/dpr, -b*mirror/dpr], [b/dpr, a*mirror/dpr]], translation=[tx/dpr, ty/dpr],
        target_xy_px=[v/dpr for v in actual], target_handle_id=target_handle_id,
        anchor_pairs=pairs, anchor_bijection=pairs, anchor_max_error_px=max(errors)/dpr,
        target_marker_error_px=math.dist(target[2], actual)/dpr,
        matrix_native_mm_to_png_px=[[a, -b*mirror, tx], [b, a*mirror, ty]],
        scale_px_per_mm=math.hypot(a, b), rotation_degrees=math.degrees(math.atan2(b, a)), y_reflected=mirror == -1,
        maximum_anchor_residual_px=max(errors), rms_anchor_residual_px=math.sqrt(sum(e*e for e in errors)/len(errors)),
        visible_handle_bijection=[dict(id=handles[i]['id'], predicted_xy_px=p, marker_xy_px=blue[k]['xy']) for i, k, p in visible],
        target_predicted_xy_px=target[2], target_blue_marker_xy_px=actual,
        target_marker_prediction_error_px=math.dist(target[2], actual), current_logical_xy=[v/dpr for v in actual],
        device_pixel_ratio_used=dpr, missing_anchor_ids=missing, all_native_anchors_visible=not missing,
        observed_gold_bijection_complete=True, visible_anchor_count=len(pairs), native_anchor_count=len(source),
        gold_classifier=('full_3x3_density_with_detached_5x5_23of25_core' if any(
            marker.get('detached_outline') for marker in gold) else 'full_3x3_density_9of9_bounded_filled_dot'),
        hypotheses_tested=tested, hypothesis_budget=max_hypotheses, absolute_0p002_mm_accuracy_verified=False,
        native_handle_hit_verified=False, **diagnostics)


def solve_projection(png_bytes, anchors, handles, target_handle_id, **options):
    """Root's logical-pixel similarity contract, with explicit missing anchors."""
    try:
        return _solve(png_bytes, anchors, handles, target_handle_id, **options)
    except ProjectionError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, struct.error, zlib.error) as exc:
        raise ProjectionError('INVALID_PARTIAL_PROJECTION_INPUT', str(exc)) from exc


def solve_with_fallback(png_bytes, anchors, handles, target_handle_id, **options):
    """Try the strict solve and reuse this capture in the partial solve."""
    try:
        image = decode_png(png_bytes)
    except (ValueError, TypeError, KeyError, OverflowError, struct.error, zlib.error) as exc:
        raise ProjectionError('INVALID_PROJECTION_INPUT', str(exc)) from exc
    try:
        return solve_full_projection(png_bytes, anchors, handles, target_handle_id, _image=image, **options)
    except ProjectionError as exc:
        if exc.code not in ('VISIBLE_MARKERS_INCOMPLETE', 'NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION'):
            raise
        return solve_projection(png_bytes, anchors, handles, target_handle_id,
                                _image=image, _blue=exc.diagnostics['blue_candidates'], **options)
