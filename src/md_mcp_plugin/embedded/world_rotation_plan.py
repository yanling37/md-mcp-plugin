"""Offline projection of a signed rotation onto a current, observed gizmo ring.

The projected samples must come from a trusted reader of the same selected
world-space ring. The zero-degree sample is the observed start on that ring.
A camera CMT supplies a freshness binding, not a world-to-viewport projection.
The resulting waypoints are a candidate for the existing five-event provider.
Only its independent OBJ matrix acceptance can verify the applied transform.
"""
from __future__ import annotations

import math

from .transforms import rotation_matrix
from .world_adapter import AdapterError, _decode_ref, _hex, _params, _text, _validate_rigid_matrix

SCHEMA = 'md-world-rotation-plan-v1'
CARDINALS = ((1., 0.), (0., 1.), (-1., 0.), (0., -1.))
DRAG_LIMIT_PX = 128.
RING_SAMPLE_TOLERANCE_PX = .5


def _number(value, name):
    if type(value) not in (int, float):
        raise ValueError(f'{name} must be numeric')
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f'{name} must be finite')
    return converted


def _vector(value, length, name):
    if type(value) not in (list, tuple) or len(value) != length:
        raise ValueError(f'{name} requires {length} finite components')
    return tuple(_number(item, name) for item in value)


def _unit(value, name):
    vector = _vector(value, 3, name)
    scale = max(map(abs, vector))
    if scale == 0:
        raise ValueError(f'{name} must be nonzero')
    scaled = tuple(item / scale for item in vector)
    length = math.hypot(*scaled)
    return tuple(item / length for item in scaled)


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return tuple(a[(i + 1) % 3] * b[(i + 2) % 3] - a[(i + 2) % 3] * b[(i + 1) % 3] for i in range(3))


def rotation_matrix_world_mm(axis_world, angle_degrees, pivot_world_mm):
    """Return a proper row-major world-mm 4x4 rotation about the specified line."""
    # The rigid target is periodic. The path planner separately retains the
    # original signed sweep for the existing single-drag range check.
    angle = math.remainder(_number(angle_degrees, 'angle_degrees'), 360.)
    matrix = rotation_matrix(axis_world, angle, pivot_world_mm)
    _validate_rigid_matrix(matrix, .002)
    return matrix


def _solve(rows):
    """Solve the eight homography coefficients with partial pivoting."""
    count = len(rows)
    for col in range(count):
        pivot = max(range(col, count), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) <= 1e-12:
            raise ValueError('Projected cardinal samples do not define a usable ring plane')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [value / scale for value in rows[col]]
        for row in range(count):
            if row != col:
                factor = rows[row][col]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[col])]
    return [row[-1] for row in rows]


def _homography(points, size):
    # Normalize the screen coordinates before solving, then return pixels.
    scale = max(size)
    center = (size[0] / 2., size[1] / 2.)
    equations = []
    for (u, v), (px, py) in zip(CARDINALS, points, strict=True):
        x, y = (px - center[0]) / scale, (py - center[1]) / scale
        equations.extend(([u, v, 1., 0., 0., 0., -x * u, -x * v, x],
                          [0., 0., 0., u, v, 1., -y * u, -y * v, y]))
    values = _solve(equations)
    denominator = (values[6], values[7], 1.)
    if math.hypot(*denominator[:2]) >= 1. - 1e-8:
        raise ValueError('Projected ring reaches a camera-plane singularity')
    result = tuple(tuple(scale * v + origin * d for v, d in zip(row, denominator, strict=True))
                   for row, origin in zip((values[:3], values[3:6]), center, strict=True)) + (denominator,)
    if any(not math.isfinite(value) for row in result for value in row):
        raise ValueError('Ring projection coefficients are not finite')
    return result


def _project(homography, angle):
    direction = (math.cos(angle), math.sin(angle), 1.)
    depth = _dot(homography[2], direction)
    if not math.isfinite(depth) or depth <= 0.:
        raise ValueError('Ring projection depth must be finite and positive')
    point = tuple(_dot(row, direction) / depth for row in homography[:2])
    if not all(map(math.isfinite, point)):
        raise ValueError('Projected ring points must be finite')
    return point


def _arc_bounds(homography, angle):
    # For each ratio (a*cos+b*sin+c)/(d*cos+e*sin+1), derivative
    # zeros satisfy A*cos+B*sin+C=0. Check extrema as well as endpoints.
    low, high = sorted((0., angle))
    d, e, _ = homography[2]
    bounds = []
    for index, (a, b, c) in enumerate(homography[:2]):
        A, B, C = b - c * e, c * d - a, b * d - a * e
        magnitude = math.hypot(A, B)
        points = [low, high]
        if magnitude and abs(C) <= magnitude:
            phase = math.atan2(B, A)
            offset = math.acos(-C / magnitude)
            for base in (phase - offset, phase + offset):
                for turn in (-1, 0, 1):
                    value = base + turn * math.tau
                    if low < value < high:
                        points.append(value)
        coordinates = [_project(homography, value)[index] for value in points]
        bounds.append([min(coordinates), max(coordinates)])
    return bounds


def _result(status, reason=None, **fields):
    result = {'schema': SCHEMA, 'status': status, 'single_drag_candidate': status == 'CANDIDATE',
              'native_dispatch_authorized': False, 'native_ring_hit_verified': False,
              'world_rotation_verified': False, 'drag_params': None, **fields}
    if reason is not None:
        result['reason'] = reason
    return result


def _inside(point, size):
    return all(0. <= coordinate < limit and coordinate <= 10_000_000
               for coordinate, limit in zip(point, size, strict=True))


def plan_rotation(request, view, ring, *, tolerance_mm=.002):
    """Plan one signed arc with an independent projected sample and rigid target.

    `view` is a fresh world-adapter view with scene_token and camera_cmt_sha256.
    `ring` has the same scope keys, selected pattern_uuid, axis and pivot, four
    projected cardinals (0,+90,+180,+270), and a separate check_sample with
    angle_degrees and viewport_px in that same positive right-hand convention.
    For a candidate, revalidate scene/view/CMT and the rendered ring start
    before handing `drag_params` to the existing provider and accept only its
    exported-matrix comparison. This function never reads or actuates MD.
    """
    try:
        _params(request, {'pattern_uuid', 'group_name', 'axis_world', 'pivot_world_mm', 'angle_degrees'})
        _params(ring, {'pattern_uuid', 'scene_token', 'view_ref', 'view_fingerprint', 'camera_cmt_sha256',
                       'axis_world', 'pivot_world_mm', 'projected_cardinals_px', 'check_sample'})
        owner = _text(request['pattern_uuid'], 'pattern_uuid')
        group = _text(request['group_name'], 'group_name')
        angle = _number(request['angle_degrees'], 'angle_degrees')
        axis = _unit(request['axis_world'], 'axis_world')
        pivot = _vector(request['pivot_world_mm'], 3, 'pivot_world_mm')
        matrix = rotation_matrix_world_mm(axis, angle, pivot)
        _validate_rigid_matrix(matrix, tolerance_mm)
        if type(view) is not dict or view.get('view') != '3d' or view.get('coordinate_space') != 'viewport_logical_pixels':
            raise ValueError('A current world-adapter 3D logical-pixel view is required')
        token = _hex(view.get('scene_token'), 'scene_token')
        view_ref = view.get('view_ref')
        _decode_ref(view_ref, token)
        fingerprint = _hex(view.get('view_fingerprint'), 'view_fingerprint')
        camera = _hex(view.get('camera_cmt_sha256'), 'camera_cmt_sha256')
        scope = {'pattern_uuid': owner, 'scene_token': token, 'view_ref': view_ref,
                 'view_fingerprint': fingerprint, 'camera_cmt_sha256': camera}
        if any(ring[key] != value for key, value in scope.items()):
            return _result('UNREACHABLE', 'RING_SCOPE_DIFFERS', expected_matrix_world_mm=matrix)
        mapping = view.get('mapping')
        if type(mapping) is not dict or view.get('scene_item_grabber_absent') is not True:
            raise ValueError('Current view mapping and idle scene grabber evidence required')
        size = _vector(mapping.get('viewport_size'), 2, 'viewport_size')
        activation_min = _number(mapping.get('start_drag_distance'), 'start_drag_distance')
        if min(size) <= 0 or activation_min < 0 or mapping.get('enabled') is not True or mapping.get('relative_visible') is not True:
            raise ValueError('The current viewport must be enabled and relatively visible')
        ring_axis = _unit(ring['axis_world'], 'ring axis_world')
        ring_pivot = _vector(ring['pivot_world_mm'], 3, 'ring pivot_world_mm')
        if math.hypot(*_cross(axis, ring_axis)) > 1e-8:
            return _result('UNREACHABLE', 'AXIS_DOES_NOT_MATCH_CURRENT_RING', expected_matrix_world_mm=matrix)
        delta = tuple(a - b for a, b in zip(pivot, ring_pivot, strict=True))
        transverse = math.hypot(*_cross(axis, delta))
        pivot_error = 2. * abs(math.sin(math.radians(math.remainder(angle, 360.)) / 2.)) * transverse
        evidence = {'expected_matrix_world_mm': matrix, 'scene_and_view_binding': scope,
                    'axis_line_offset_mm': transverse, 'pivot_induced_translation_error_mm': pivot_error,
                    'requested_angle_degrees': angle}
        if not math.isfinite(pivot_error):
            raise ValueError('Requested and native axis-line separation is not finite')
        if pivot_error > tolerance_mm:
            return _result('UNREACHABLE', 'PIVOT_LINE_DIFFERS', **evidence)
        if math.remainder(angle, 360.) == 0.:
            return _result('NO_OP', 'IDENTITY_ROTATION', **evidence)
        if abs(angle) > 180.:
            return _result('UNREACHABLE', 'SIGNED_SWEEP_EXCEEDS_ONE_DRAG', **evidence)

        cardinals = ring['projected_cardinals_px']
        if type(cardinals) not in (list, tuple) or len(cardinals) != 4:
            raise ValueError('Four ordered projected cardinal points required')
        points = [_vector(value, 2, 'projected cardinal') for value in cardinals]
        check = ring['check_sample']
        _params(check, {'angle_degrees', 'viewport_px'})
        sample_angle = math.remainder(_number(check['angle_degrees'], 'check angle'), 360.)
        if abs(math.remainder(sample_angle, 90.)) < 1.:
            raise ValueError('Independent projected sample must be away from the cardinal points')
        sample = _vector(check['viewport_px'], 2, 'check viewport_px')
        projection = _homography(points, size)
        predicted = _project(projection, math.radians(sample_angle))
        error = math.dist(predicted, sample)
        if not math.isfinite(error):
            raise ValueError('Projection check residual is not finite')
        evidence.update(projection_check_error_px=error, projection_check_tolerance_px=RING_SAMPLE_TOLERANCE_PX)
        if error > RING_SAMPLE_TOLERANCE_PX:
            return _result('UNREACHABLE', 'INDEPENDENT_RING_PROJECTION_MISMATCH', **evidence)
        screen_center = (projection[0][2], projection[1][2])
        u = tuple(a - b for a, b in zip(points[0], screen_center, strict=True))
        v = tuple(a - b for a, b in zip(points[1], screen_center, strict=True))
        handedness = u[0] * v[1] - u[1] * v[0]
        if not math.isfinite(handedness) or abs(handedness) < 1e-8:
            return _result('UNREACHABLE', 'RING_PROJECTION_EDGE_ON', **evidence)
        ring_angle = math.copysign(1., _dot(axis, ring_axis)) * angle
        sweep = math.radians(ring_angle)
        bounds = _arc_bounds(projection, sweep)
        evidence.update(signed_ring_angle_degrees=ring_angle, arc_bounds_viewport_px=bounds,
                        viewport_sweep='clockwise' if handedness * ring_angle > 0. else 'counterclockwise')
        if any(not (0. <= low <= high < limit) for (low, high), limit in zip(bounds, size, strict=True)):
            return _result('UNREACHABLE', 'ARC_LEAVES_CURRENT_VIEWPORT', **evidence)

        start, end = points[0], _project(projection, sweep)
        if not _inside(start, size) or not _inside(end, size):
            return _result('UNREACHABLE', 'DRAG_ENDPOINT_OUTSIDE_CURRENT_VIEWPORT', **evidence)
        if math.dist(start, end) > DRAG_LIMIT_PX:
            return _result('UNREACHABLE', 'DRAG_EXCEEDS_128_LOGICAL_PIXELS', **evidence)
        for fraction in (.5, .625, .75, .875, .9375):
            activation = _project(projection, sweep * fraction)
            first, last = math.dist(start, activation), math.dist(activation, end)
            if (_inside(activation, size) and first > 0. and first >= activation_min
                    and last > 0. and first + last <= DRAG_LIMIT_PX):
                path = {'pattern_uuid': owner, 'group_name': group, 'scene_token': token, 'view_ref': view_ref,
                        'view_fingerprint': fingerprint, 'purpose': 'rotation_ring', 'start': list(start),
                        'activation': list(activation), 'end': list(end), 'expected_matrix_world_mm': matrix,
                        'matrix_tolerance_mm': tolerance_mm}
                return _result('CANDIDATE', **evidence, drag_params=path, drag_length_logical_px=first + last,
                               activation_distance_logical_px=first, activation_minimum_logical_px=activation_min)
        return _result('UNREACHABLE', 'NO_BOUNDED_ACTIVATION_SPLIT', **evidence)
    except (AdapterError, ValueError, TypeError, OverflowError) as error:
        return _result('REJECTED', 'INVALID_ROTATION_OR_PROJECTION_EVIDENCE', detail=str(error))
