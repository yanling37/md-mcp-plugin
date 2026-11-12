"""Offline CMT/wire registration and differential MeshBox green-marker evidence."""

from base64 import b64decode
from hashlib import sha256
import math

from .cloth_meshbox_uv_fit import _EdgeGrid, _Piece, _at, _dot, _inventory, _inverse_gram, _profile, _sub
from .cloth_vertex_mapping import _one_component
from .cloth import _cross
from .common import digest
from .curve_projection import decode_png
from .mesh import parse_obj
from .world_projection import _normalized_coordinates, calibrate_projection, camera_pose_from_matrix, project_world


_MESH_FIELDS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices',
                'face_labels', 'topology_fingerprint')
_GREEN_THRESHOLDS = (32, 80, 110)
_WIRE_ERROR_PX = .75
_POSE_ERROR_PX = .15


def _wire_partitions(witnesses):
    cells = {}
    for witness in witnesses:
        xy = witness[0]['pixel_xy']
        cells.setdefault(tuple(int(x // 40) for x in xy), []).append(witness)
    groups, pixels = ([], []), (set(), set())
    for level in range(max(map(len, cells.values()), default=0)):
        for cell in cells.values():
            if len(cell) <= level:
                continue
            witness = cell[level]
            side = int(len(groups[0]) > len(groups[1]))
            if len(groups[side]) < 120 and witness[1].isdisjoint(pixels[1 - side]):
                groups[side].append(witness)
                pixels[side].update(witness[1])
        if min(map(len, groups)) == 120:
            break
    return groups


def _flat_profile(image, point, normal, _style):
    """Measure a shaded wire trough against two independent flat cloth shoulders."""
    radius = 2.5
    width, height, channels = (image[k] for k in ('width', 'height', 'channels'))
    if not (radius + 1 < point[0] < width - radius - 1 and radius + 1 < point[1] < height - radius - 1):
        return None
    footprint, samples = set(), []
    for step in range(-10, 11):
        x, y = (p + step * d / 4 - .5 for p, d in zip(point, normal, strict=True))
        i, j = math.floor(x), math.floor(y)
        fx, fy = x - i, y - j
        pixels = ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1))
        weights = ((1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy)
        colors = [image['pixels'][(v * width + u) * channels:(v * width + u) * channels + channels]
                  for u, v in pixels]
        if channels == 4 and any(color[3] != 255 for color in colors):
            return None
        rgb = [_dot(axis, weights) for axis in zip(*(color[:3] for color in colors), strict=True)]
        samples.append((_dot(rgb, (.2126, .7152, .0722)), rgb[1] - max(rgb[0], rgb[2])))
        footprint.update(pixels)
    gray, green = zip(*samples, strict=True)
    shoulders = (*gray[:3], *gray[-3:])
    if min(shoulders) < 135 or max(shoulders) - min(shoulders) > 4:
        return None
    baseline = math.fsum(shoulders) / len(shoulders)
    trough = min(gray)
    peak_index = gray.index(trough)
    if baseline - trough < 6 or green[peak_index] < max(*green[:3], *green[-3:]) + 1.5:
        return None
    level = baseline - .55 * (baseline - trough)
    support = [i for i, value in enumerate(gray) if value <= level]
    lo, hi = support[0], support[-1]
    if lo == 0 or hi == len(gray) - 1 or support != list(range(lo, hi + 1)):
        return None
    left = lo - 1 + (level - gray[lo - 1]) / (gray[lo] - gray[lo - 1])
    right = hi + (level - gray[hi]) / (gray[hi + 1] - gray[hi])
    shift = (left + right) / 8 - radius
    if abs(shift) > 1.5 or (right - left) / 4 > 2.6:
        return None
    return tuple(p + shift * n for p, n in zip(point, normal, strict=True)), footprint, shift


def _measure_wire(image, grid, seed, positions, edge, target, dpr, *, profile=_profile):
    a, b = (positions[i] for i in edge)
    midpoint = _at(a, b, .5)
    middle = project_world(seed, midpoint)
    first, last = (project_world(seed, _at(a, b, t)) for t in (.26, .74))
    dx, dy = _sub(last, first)
    length = math.hypot(dx, dy)
    if (length < 6 or min(math.dist(q, target) for q in (first, middle, last)) < 19 * dpr
            or any(not grid.clear('wire', 0, edge, q, 1.65) for q in (first, last))):
        return None
    tangent = dx / length, dy / length
    normal = -tangent[1], tangent[0]
    left, right = (profile(image, q, normal, 'wire') for q in (first, last))
    if left is None or right is None or abs(right[2] - left[2]) > .9:
        return None
    dx, dy = _sub(right[0], left[0])
    length = math.hypot(dx, dy)
    observed_normal = -dy / length, dx / length
    shift = _dot(_sub(middle, left[0]), observed_normal)
    if abs(shift) > 1:
        return None
    pixel = [x - shift * n for x, n in zip(middle, observed_normal)]
    if not (0 <= pixel[0] < image['width'] and 0 <= pixel[1] < image['height']):
        return None
    if any(math.dist((x + .5, y + .5), target) <= 16 * dpr for x, y in left[1] | right[1]):
        return None
    sample = {'id': f'{edge[0]}-{edge[1]}', 'world_xyz_mm': midpoint,
              'pixel_xy': pixel, 'normal_xy': observed_normal}
    return sample, left[1] | right[1], tangent, (_dot(tangent, first), _dot(tangent, last))


def _projection_errors(pose, fitted, held_out, positions):
    """Absolute influence of bounded wire-normal errors on screen coordinates."""
    xy = [_normalized_coordinates(pose, s[0]['world_xyz_mm'])[0] for s in fitted]
    scales = [max(abs(x) for x in axis) for axis in zip(*xy)]
    if min(scales) <= 1e-10:
        raise ValueError('Wire observations do not support both focal axes')

    def row(uv, direction):
        x, y = (p / s for p, s in zip(uv, scales))
        nx, ny = direction
        return nx * x, ny * y, nx, ny

    normals = [sample[0]['normal_xy'] for sample in fitted]
    rows = [row(uv, n) for uv, n in zip(xy, normals)]
    inverse = _inverse_gram(rows)
    influence = [tuple(_dot(d, equation) for d in inverse) for equation in rows]
    measurement = [_WIRE_ERROR_PX + _POSE_ERROR_PX * sum(map(abs, n)) for n in normals]

    def bound(uv, direction):
        query = row(uv, direction)
        return (_POSE_ERROR_PX * sum(map(abs, direction))
                + math.fsum(abs(_dot(query, weight)) * err for weight, err in zip(influence, measurement)))

    for sample, _, tangent, (low, high) in fitted + held_out:
        point = sample['world_xyz_mm']
        nominal = _dot(tangent, project_world(pose, point))
        error = bound(_normalized_coordinates(pose, point)[0], tangent)
        if not low < nominal - error <= nominal + error < high:
            raise ValueError(f'Wire edge {sample["id"]} does not bracket its projected midpoint')
    return {i: tuple(bound(_normalized_coordinates(pose, point)[0], axis) for axis in ((1, 0), (0, 1)))
            for i, point in positions.items()}


def _calibrate(camera_pose, image, parsed, pieces, target, focal_seed_px, dpr, *, profile=_profile):
    if type(focal_seed_px) not in (int, float) or not 1 <= focal_seed_px <= 100_000:
        raise ValueError('Supply a positive physical-pixel focal seed for this viewport')
    if type(dpr) not in (int, float) or not .5 <= dpr <= 4:
        raise ValueError('Use the actual finite device pixel ratio in [.5,4]')
    size = [image['width'], image['height']]
    positions = {i: parsed['positions_mm'][i] for piece in pieces.values() for i in piece.uv}
    if target not in positions:
        raise ValueError('The calibration target must belong to the complete cloth inventory')
    seed = {**camera_pose, 'focal_px': focal_seed_px, 'center_px': [x / 2 for x in size]}
    seed_positions = {i: project_world(seed, p) for i, p in positions.items()}
    edges = tuple(sorted(e for piece in pieces.values() for e in piece.wire))
    outlines = tuple(sorted(e for piece in pieces.values() for e in piece.outline))
    grid = _EdgeGrid({0: _Piece({}, edges + outlines, ())}, {0: seed_positions})
    center = seed_positions[target]
    measurements = (_measure_wire(image, grid, seed, positions, edge, center, dpr, profile=profile)
                    for edge in edges if target not in edge)
    train, hold = _wire_partitions([value for value in measurements if value is not None])
    if min(len(train), len(hold)) < 16:
        raise ValueError(f'Insufficient resolved wire evidence (fit {len(train)}, held out {len(hold)})')
    partitions = [[item[0] for item in rows] for rows in (train, hold)]
    result = calibrate_projection(camera_pose, size, *partitions, max_error_px=.75)
    if not result['verified']:
        raise ValueError('Current CMT and wire reprojection do not pass disjoint fit and holdout')
    camera = result['camera']
    errors = _projection_errors(camera, train, hold, positions)
    projections = {i: project_world(camera, p) for i, p in positions.items()}
    return {**result, 'fit_edge_ids': [r['id'] for r in partitions[0]], 'holdout_edge_ids': [r['id'] for r in partitions[1]],
            'wire_normal_error_budget_px': _WIRE_ERROR_PX, 'pose_model_error_budget_px': _POSE_ERROR_PX,
            'pixel_convention': 'physical_pixel_centers_relative_to_top_left_edge',
            'target_projected_px': projections[target], 'target_uncertainty_px': errors[target],
            'all_projected_points': projections, 'all_projection_errors': errors}


def calibrate_zoom_wire(camera_pose, png_bytes, obj_bytes, *, group_by_uuid, excluded_groups, export_vertex_index,
                        focal_seed_px, device_pixel_ratio=1):
    """Read independent fit/holdout wires in an actual frame. Caller binds the CMT and OBJ."""
    parsed = parse_obj(obj_bytes)
    pieces = _inventory(parsed, group_by_uuid, tuple(excluded_groups))
    return _calibrate(camera_pose, decode_png(png_bytes), parsed, pieces, export_vertex_index,
                      focal_seed_px, device_pixel_ratio)


def _green_excess(pixel):
    r, g, b, a = pixel
    return g - max(r, b) if a == 255 else -256


def _green_difference(before, after, dpr, *, wide=False):
    width, height = after['width'], after['height']
    if (before['width'], before['height'], before['channels'], after['channels']) != (width, height, 4, 4):
        raise ValueError('Use two same-size RGBA frames')
    source, result = before['pixels'], after['pixels']
    changed = 0
    changed_bounds = [width, height, -1, -1]
    masks = [set() for _ in _GREEN_THRESHOLDS]
    changed_support = set()
    pixel_budget = (8192 if wide else 350) * dpr * dpr
    for offset in range(0, len(result), 4):
        old, new = source[offset:offset + 4], result[offset:offset + 4]
        if old == new:
            continue
        index = offset // 4
        x, y = index % width, index // width
        changed += 1
        if wide:
            if changed > pixel_budget:
                return {'reason': 'GREEN_PIXEL_BUDGET_EXCEEDED', 'observation_verified': False}
            changed_support.add((x, y))
        changed_bounds = [min(changed_bounds[0], x), min(changed_bounds[1], y),
                          max(changed_bounds[2], x), max(changed_bounds[3], y)]
        after_excess, before_excess = _green_excess(new), _green_excess(old)
        if after_excess - before_excess >= 12:
            for mask, threshold in zip(masks, _GREEN_THRESHOLDS):
                if after_excess >= threshold:
                    mask.add((x, y))
        if len(masks[0]) > pixel_budget:
            return {'reason': 'GREEN_PIXEL_BUDGET_EXCEEDED', 'observation_verified': False}
    report = {'changed_pixels': changed, 'green_counts_32_80_110': list(map(len, masks)),
              'core_clearance_logical_px': 0.0, 'observation_verified': False}
    if not all(_one_component(mask) for mask in masks):
        return dict(report, reason='NO_UNIQUE_GREEN_COMPONENT')
    xs, ys = zip(*masks[0])
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    report['green_bounds_sample_indices'] = [x0, y0, x1, y1]
    dx0, dy0, dx1, dy1 = changed_bounds
    if (x0 < 13 * dpr or y0 < 13 * dpr or x1 >= width - 13 * dpr or y1 >= height - 13 * dpr
            or not (x0 - 2 <= dx0 <= dx1 <= x1 + 2 and y0 - 2 <= dy0 <= dy1 <= y1 + 2)):
        return dict(report, reason='MARKER_OR_FRAME_DIFF_OUTSIDE_LOCAL_SCOPE')
    base = before['pixels']
    pad = math.ceil(13 * dpr)
    if any(_green_excess(base[4 * (y * width + x):4 * (y * width + x) + 4]) >= 80
           for y in range(max(0, y0 - pad), min(height, y1 + pad + 1))
           for x in range(max(0, x0 - pad), min(width, x1 + pad + 1))):
        return dict(report, reason='PREEXISTING_GREEN_CORE')
    core = masks[-1]
    if wide:
        loose = masks[0]
        # Every changed pixel must be inside the glyph or on its one-pixel
        # antialiased boundary. A second local redraw cannot enlarge its proof.
        outside = changed_support - loose
        if not _one_component(changed_support) or any(
                all((x + a, y + b) not in loose for a in (-1, 0, 1) for b in (-1, 0, 1))
                for x, y in outside):
            return dict(report, reason='NON_MARKER_PIXELS_CHANGED')
        return dict(report, observation_verified=True, reason='UNIQUE_NEW_GREEN_COMPONENT',
                    changed_bounds_sample_indices=changed_bounds, _strong_core=core)
    xs, ys = zip(*core)
    outside = [(x, y) for x in range(min(xs) - 1, max(xs) + 2) for y in range(min(ys) - 1, max(ys) + 2)
               if (x, y) not in core]
    clearance = {p: min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in outside) for p in core}
    best = max(clearance.values())
    report['core_clearance_logical_px'] = math.sqrt(best) / dpr
    if best < (4 * dpr) ** 2:
        return dict(report, reason='GREEN_CORE_TOO_SMALL')
    centers = [(x + .5, y + .5) for (x, y), margin in clearance.items() if margin == best]
    return dict(report, observation_verified=True, reason='UNIQUE_NEW_GREEN_COMPONENT', deepest_core_pixel_centers=centers)


def _bind_projection(marker, calibration, target, dpr):
    xy = calibration['all_projected_points']
    bounds = calibration['all_projection_errors']
    core = min(marker['deepest_core_pixel_centers'], key=lambda p: (math.dist(p, xy[target]), p))
    far = math.hypot(*(abs(x - p) + u for x, p, u in zip(xy[target], core, bounds[target])))
    distance, closest = min((math.hypot(*(max(0, abs(x - p) - u) for x, p, u in zip(q, core, bounds[i]))), i)
                            for i, q in xy.items() if i != target)
    left, top, right, bottom = marker['green_bounds_sample_indices']
    x, y = xy[target]
    local_error = math.hypot(max(0, left - x, x - right - 1), max(0, top - y, y - bottom - 1))
    margin = distance - far
    return {'visual_binding_verified': margin > 2.5 * dpr and local_error <= 2 * dpr,
            'target_projected_px': xy[target], 'target_uncertainty_px': bounds[target],
            'target_max_distance_from_core_px': far, 'nearest_other_min_distance_from_core_px': distance,
            'nearest_other_export_vertex_index': closest, 'separation_margin_px': margin,
            'projected_target_distance_to_green_bounds_px': local_error, 'competitor_count': len(xy) - 1}


def _flat_inventory(pose, parsed, pieces, target):
    if len(pieces) != 1 or sum(len(piece.uv) for piece in pieces.values()) != parsed['vertex_count']:
        return False
    positions = parsed['positions_mm']
    if any(_dot(_sub(pose['camera_center_world_mm'], point), pose['forward']) <= 0 for point in positions):
        return False
    # Fit the plane to the untouched vertices. A small depth edit at the target
    # may already have been applied from the first camera.
    untouched = [point for index, point in enumerate(positions) if index != target]
    if len(untouched) < 3:
        return False
    origin = untouched[0]
    vectors = [_sub(point, origin) for point in untouched[1:]]
    span = max(vectors, key=lambda vector: _dot(vector, vector))
    normal = max((_cross(span, vector) for vector in vectors), key=lambda vector: _dot(vector, vector))
    length = math.hypot(*normal)
    if length <= 1e-6:
        return False
    normal = [coordinate / length for coordinate in normal]
    return (all(abs(_dot(normal, vector)) <= 1e-5 for vector in vectors)
            and abs(_dot(normal, _sub(positions[target], origin))) <= .2)


def _bind_wide_projection(marker, calibration, target, dpr):
    """Bind the entire large glyph to one projected interval from the other frame."""
    core = marker.pop('_strong_core')
    points, errors = calibration['all_projected_points'], calibration['all_projection_errors']
    x, y = points[target]
    dx, dy = errors[target]
    cells = {(i, j) for i in range(math.floor(x - dx), math.floor(x + dx) + 1)
             for j in range(math.floor(y - dy), math.floor(y + dy) + 1)}
    left, top, right, bottom = marker['changed_bounds_sample_indices']
    margin = 2.5 * dpr

    def outside_distance(point, bound):
        u, v = point
        ex, ey = bound
        return math.hypot(max(0.0, left - (u + ex), (u - ex) - (right + 1)),
                          max(0.0, top - (v + ey), (v - ey) - (bottom + 1)))

    distance, nearest = min((outside_distance(point, errors[i]), i)
                            for i, point in points.items() if i != target)
    anchor = min(core, key=lambda p: (math.dist((p[0] + .5, p[1] + .5), (x, y)), p))
    xs, ys = zip(*core)
    clearance = min(math.dist(anchor, (i, j)) for i in range(min(xs) - 1, max(xs) + 2)
                    for j in range(min(ys) - 1, max(ys) + 2) if (i, j) not in core)
    verified = cells <= core and distance > margin and clearance >= 4 * dpr
    marker.update(core_clearance_logical_px=clearance / dpr,
                  deepest_core_pixel_centers=[(anchor[0] + .5, anchor[1] + .5)],
                  anchor_basis='closest_qualified_core_pixel_to_registered_target')
    return {'visual_binding_verified': verified, 'target_projected_px': points[target],
            'target_uncertainty_px': errors[target], 'target_interval_inside_strong_green': cells <= core,
            'closest_competitor_interval_clearance_to_entire_marker_px': distance,
            'nearest_other_export_vertex_index': nearest, 'separation_margin_px': distance,
            'minimum_competitor_clearance_px': margin, 'competitor_count': len(points) - 1,
            'large_component_exclusivity_verified': verified}


def bind_current_green_vertex(pose, before, after, parsed, pieces, target, focal_seed_px, dpr):
    """Use the original gate, with a separate photometric branch for one flat paper."""
    marker = _green_difference(before, after, dpr)
    flat = _flat_inventory(pose, parsed, pieces, target)
    wide = flat and marker.get('reason') == 'GREEN_PIXEL_BUDGET_EXCEEDED'
    if wide:
        marker = _green_difference(before, after, dpr, wide=True)
    if marker['observation_verified'] is not True:
        raise ValueError(f'Selection marker does not have a unique green core: {marker.get("reason")}')
    try:
        calibration = _calibrate(pose, before, parsed, pieces, target, focal_seed_px, dpr)
        basis = 'green_wire_profiles'
    except ValueError:
        if not flat:
            raise
        calibration = _calibrate(pose, before, parsed, pieces, target, focal_seed_px, dpr, profile=_flat_profile)
        basis = 'flat_cloth_independent_luminance_wire_profiles'
    calibration['registration_basis'] = basis
    if wide:
        binding = _bind_wide_projection(marker, calibration, target, dpr)
    else:
        binding = _bind_projection(marker, calibration, target, dpr)
    return marker, calibration, binding


def _frame_bytes(frame, png):
    if not (frame['verified'] is True and frame['view']['viewport']['object_name'] == 'widget3d'
            and frame['native']['verified'] is True and frame['native']['gl_state_restored'] is True
            and frame['native']['background_endpoints_verified'] is True and frame['native']['requires_recovery'] is False
            and all(frame['native'][k] == 0 for k in ('OS_input_calls', 'focus_calls', 'activation_calls', 'model_setter_calls'))):
        raise ValueError('Require a closed verified background widget3d capture')
    meta, view = frame['image'], frame['view']
    if (meta['sha256'] != sha256(png).hexdigest() or meta['byte_count'] != len(png)
            or meta['mapping_fingerprint'] != view['mapping_fingerprint']
            or any(meta[k] != view['mapping'][k] for k in ('viewport_size', 'device_pixel_ratio'))):
        raise ValueError('3D image bytes or viewport binding differ from capture receipt')
    image = decode_png(png)
    dpr = meta['device_pixel_ratio']
    if (type(dpr) not in (int, float) or not .5 <= dpr <= 4
            or any(image[k] != meta[k] for k in ('width', 'height'))
            or any(abs(image[k] - value * dpr) > .5 for k, value in zip(('width', 'height'), meta['viewport_size']))):
        raise ValueError('3D image extent or DPR differs')
    return image


def audit_zoomed_meshbox_sample(result, before_png, after_png, obj_bytes, *, group_by_uuid, excluded_groups,
                                focal_seed_px):
    """Use one current MeshBox 2D selection receipt and the exact paired 3D evidence."""
    a, b = (result[k] for k in ('selection_3d_before', 'selection_3d_after'))
    source, end = (result[k] for k in ('mesh_before', 'mesh_after'))
    selected = result['selection_readback']
    plan = result['selection_plan']
    if (result.get('entry_verified') is not True or result.get('exported_mesh_preserved') is not True
            or result.get('mode_restored') is not True or result.get('transaction_closed') is not True
            or result.get('scene_preserved') is not True or result.get('sdk_selection_preserved') is not True
            or result['mode_during']['mode'] != 'MeshBox' or result['mode_during']['world_coordinate_checked'] is not True
            or selected.get('verified') is not True or selected.get('new_blue_pixels', 0) < 1
            or selected['before_sha256'] != result['meshbox_frame']['image']['sha256']
            or selected['after_sha256'] != result['meshbox_after_selection']['image']['sha256']
            or a['image']['sha256'] == b['image']['sha256']
            or any(a[k] != b[k] for k in ('scene_token', 'scene_identity', 'view', 'camera', 'selection', 'selected_names'))
            or any(a[k] != result[k] for k in ('scene_token', 'scene_identity'))
            or any(source[k] != end[k] for k in ('arrays_sha256', 'topology_fingerprint'))):
        raise ValueError('Require paired frames, a unique blue marker, and a closed unchanged MeshBox trial')
    owner, target = (selected[key] for key in ('pattern_uuid', 'export_vertex_index'))
    if (type(target) is not int or (plan['target_pattern_uuid'], plan['target_vertex_index']) != (owner, target)
            or len(a['selection']) != 1 or a['selection'][0].get('pattern_uuid') != owner
            or a['selection'][0].get('selected') is not True or a['selected_names'] != [group_by_uuid.get(owner)]
            or a['selection'][0].get('name') != group_by_uuid.get(owner)
            or source['obj_asset']['sha256'] != sha256(obj_bytes).hexdigest()):
        raise ValueError('Target owner or supplied world OBJ differs from the current MeshBox receipt')
    parsed = parse_obj(obj_bytes)
    if (parsed['topology_fingerprint'] != source['topology_fingerprint']
            or digest({k: parsed[k] for k in _MESH_FIELDS}) != source['arrays_sha256']):
        raise ValueError('Parsed OBJ does not match the current whole-mesh readback')
    pieces = _inventory(parsed, group_by_uuid, tuple(excluded_groups))
    if target not in pieces[owner].uv:
        raise ValueError('The selected export vertex does not belong to its current owner')
    before, after = _frame_bytes(a, before_png), _frame_bytes(b, after_png)
    dpr = a['image']['device_pixel_ratio']
    marker = _green_difference(before, after, dpr)
    camera = a['camera']
    if sha256(b64decode(camera['cpt_base64'], validate=True)).hexdigest() != camera['cpt_sha256']:
        raise ValueError('Camera parameter bytes differ from the current frame receipt')
    pose = camera_pose_from_matrix(camera['matrix'])
    report = {'schema': 'md-meshbox-zoom-observation/1', 'scene_token': a['scene_token'], 'pattern_uuid': owner,
              'export_vertex_index': target, 'source_xyz_mm': parsed['positions_mm'][target],
              'before_sha256': a['image']['sha256'], 'after_sha256': b['image']['sha256'],
              'cmt_sha256': camera['cmt_sha256'], 'cpt_sha256': camera['cpt_sha256'],
              'world_arrays_sha256': source['arrays_sha256'], 'marker': marker, 'native_vertex_identity_verified': False,
              'visual_binding_verified': False, 'drag_authorized': False}
    try:
        calibration = _calibrate(pose, before, parsed, pieces, target, focal_seed_px, dpr)
    except ValueError as exc:
        return dict(report, calibration_verified=False, calibration_error=str(exc))
    report['calibration_verified'] = True
    if marker['observation_verified']:
        report.update(_bind_projection(marker, calibration, target, dpr))
    report['calibration'] = {k: v for k, v in calibration.items() if k not in ('all_projected_points', 'all_projection_errors')}
    return report
