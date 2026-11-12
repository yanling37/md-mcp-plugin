"""Fit a rotation axis from paired world-mm OBJ positions, offline only."""

import math

from . import mesh
from .transforms import rotation_matrix
from .world_adapter import bind_export_labels, compare_exports, _matrix_readback


def _vec(value):
    if (type(value) not in (list, tuple) or len(value) != 3
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError('Finite three-component world coordinates required')
    return value


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return tuple(a[(i + 1) % 3] * b[(i + 2) % 3] - a[(i + 2) % 3] * b[(i + 1) % 3]
                 for i in range(3))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _center(points):
    return tuple(math.fsum(p[i] / len(points) for p in points) for i in range(3))


def _dominant_quaternion(matrix):
    # Symmetric Jacobi eigensolve. Power iteration can select the wrong
    # eigenvector for a planar cloth, where eigenvalue magnitudes can tie.
    vectors = [[float(i == j) for j in range(4)] for i in range(4)]
    pairs = [(i, j) for i in range(4) for j in range(i + 1, 4)]
    for _ in range(96):
        p, q = max(pairs, key=lambda ij: abs(matrix[ij[0]][ij[1]]))
        off = matrix[p][q]
        scale = max(abs(matrix[i][i]) for i in range(4))
        if abs(off) <= 1e-15 * scale:
            break
        angle = .5 * math.atan2(2 * off, matrix[q][q] - matrix[p][p])
        c, s = math.cos(angle), math.sin(angle)
        pp, qq = matrix[p][p], matrix[q][q]
        matrix[p][p] = c*c*pp - 2*s*c*off + s*s*qq
        matrix[q][q] = s*s*pp + 2*s*c*off + c*c*qq
        matrix[p][q] = matrix[q][p] = 0.
        for i in range(4):
            if i not in (p, q):
                ip, iq = matrix[i][p], matrix[i][q]
                matrix[i][p] = matrix[p][i] = c*ip - s*iq
                matrix[i][q] = matrix[q][i] = s*ip + c*iq
            vp, vq = vectors[i][p], vectors[i][q]
            vectors[i][p], vectors[i][q] = c*vp - s*vq, s*vp + c*vq
    else:
        raise ValueError('Rotation fit did not converge')
    order = sorted(range(4), key=lambda i: matrix[i][i], reverse=True)
    largest, next_largest = (matrix[i][i] for i in order[:2])
    if largest <= 0 or largest - next_largest <= 1e-12 * largest:
        raise ValueError('Point correspondences do not determine a unique proper rotation')
    q = [row[order[0]] for row in vectors]
    signed_length = math.copysign(math.hypot(*q), q[0])
    return tuple(x / signed_length for x in q), (largest - next_largest) / largest


def _fit_orientation(before, after):
    scale_before = max(math.hypot(*p) for p in before)
    scale_after = max(math.hypot(*p) for p in after)
    if not 0 < scale_before < math.inf or not 0 < scale_after < math.inf:
        raise ValueError('Nonzero finite point spread is required')
    count = len(before)
    # Horn's quaternion solution from paired centroid-relative coordinates.
    [[xx, xy, xz], [yx, yy, yz], [zx, zy, zz]] = [
        [math.fsum((a[i] / scale_before) * (b[j] / scale_after) / count
                   for a, b in zip(before, after, strict=True)) for j in range(3)]
        for i in range(3)
    ]
    return _dominant_quaternion([
        [xx + yy + zz, yz - zy, zx - xz, xy - yx],
        [yz - zy, xx - yy - zz, xy + yx, zx + xz],
        [zx - xz, xy + yx, -xx + yy - zz, yz + zy],
        [xy - yx, zx + xz, yz + zy, -xx - yy + zz],
    ])


def _position_error(matrix, before, after):
    return max(math.dist([_dot(row[:3], a) + row[3] for row in matrix[:3]], b)
               for a, b in zip(before, after, strict=True))


def fit_rotation_axis(before_positions_mm, after_positions_mm, *, tolerance_mm=.002):
    """Fit a proper rigid transform and test whether its screw pitch is negligible.

    Inputs must already be paired by immutable export index. The nearest axis
    point is mathematical evidence about these samples, with no uncertainty or
    native gizmo claim. Insufficient, ambiguous and no-motion fits raise ValueError.
    """
    if type(tolerance_mm) not in (int, float) or not 0 < tolerance_mm <= .002:
        raise ValueError('tolerance_mm must be in (0,.002]')
    a = [_vec(p) for p in before_positions_mm]
    b = [_vec(p) for p in after_positions_mm]
    if len(a) != len(b) or len(a) < 3:
        raise ValueError('At least three paired exported vertices are required')
    center_a, center_b = _center(a), _center(b)
    centered_a = [_sub(p, center_a) for p in a]
    centered_b = [_sub(p, center_b) for p in b]
    (cos_half, *imaginary), relative_gap = _fit_orientation(centered_a, centered_b)
    sin_half = math.hypot(*imaginary)
    if sin_half < 1e-12:
        raise ValueError('Observed displacement does not determine a rotation axis')
    axis = [value / sin_half for value in imaginary]
    angle = math.degrees(2 * math.atan2(sin_half, cos_half))
    rotation_motion = max(2 * sin_half * math.hypot(*_cross(axis, p)) for p in centered_a)
    if rotation_motion <= 2 * tolerance_mm:
        raise ValueError('Observed rotation signal must exceed twice the position tolerance')

    displacement = _sub(center_b, center_a)
    axial = _dot(displacement, axis)
    perpendicular = tuple(d - axial * u for d, u in zip(displacement, axis, strict=True))
    # On the plane normal to the axis, inverse(I-R) is
    # (I + cot(theta/2) * [axis]_cross) / 2. Solve near the observed mesh
    # centroid first to avoid cancellation from a distant world origin.
    cross = _cross(axis, perpendicular)
    centroid_point = [c + .5 * (d + cos_half / sin_half * x)
                      for c, d, x in zip(center_a, perpendicular, cross, strict=True)]
    along_axis = _dot(centroid_point, axis)
    origin_point = [p - along_axis * u for p, u in zip(centroid_point, axis, strict=True)]
    pure = rotation_matrix(axis, angle, centroid_point)
    rigid = [row[:3] + [row[3] + axial * u] for row, u in zip(pure[:3], axis, strict=True)] + [pure[3][:]]
    rigid_error, pure_error = _position_error(rigid, a, b), _position_error(pure, a, b)
    if not all(map(math.isfinite, (rigid_error, pure_error, rotation_motion, *origin_point))):
        raise ValueError('Nonfinite rotation evidence')
    return {'axis_world': axis, 'angle_degrees': angle, 'axis_point_nearest_world_origin_mm': origin_point,
            'axis_point_nearest_before_centroid_mm': centroid_point, 'before_centroid_mm': list(center_a),
            'axial_translation_mm': axial, 'maximum_fitted_rigid_residual_mm': rigid_error,
            'maximum_pure_rotation_residual_mm': pure_error, 'maximum_centered_rotation_motion_mm': rotation_motion,
            'relative_quaternion_eigenvalue_gap': relative_gap, 'vertex_count': len(a), 'tolerance_mm': tolerance_mm,
            'rigid_positions_fit_verified': rigid_error <= tolerance_mm,
            'pure_rotation_positions_fit_verified': rigid_error <= tolerance_mm and pure_error <= tolerance_mm and abs(axial) <= tolerance_mm,
            'fitted_rigid_matrix_world_mm': rigid, 'axis_rotation_matrix_world_mm': pure,
            'native_axis_verified': False, 'native_pivot_verified': False}


def measure_rotation_readback(scene_before, scene_after, before, after, pattern_uuid, group_name, *, tolerance_mm=.002):
    """Measure an exported axis and apply the existing full-scene comparison."""
    first = bind_export_labels(scene_before, before, pattern_uuid, group_name)
    last = bind_export_labels(scene_after, after, pattern_uuid, group_name)
    observed = compare_exports(before, after, group_name)
    if (before['scene_identity'] != after['scene_identity'] or before['scene_token'] != after['scene_token']
            or before['capture_id'] == after['capture_id'] or first['pairs'] != last['pairs']
            or first['additional_unclassified_groups'] != last['additional_unclassified_groups']
            or not all(observed.get(key) is True for key in (
                'topology_and_index_maps_exact', 'uv_exact', 'other_export_groups_all_exact'))):
        raise ValueError('Independent matching scene exports with stable target indices and unchanged non-target groups required')
    a, b = (mesh.extract_group(snapshot, group_name)['positions_mm'] for snapshot in (before, after))
    fit = fit_rotation_axis(a, b, tolerance_mm=tolerance_mm)
    acceptance = _matrix_readback(before, after, group_name, fit['axis_rotation_matrix_world_mm'],
                                  tolerance_mm, first, last, observed)
    return {**fit, 'exported_axis_line_measured': (fit['pure_rotation_positions_fit_verified']
            and acceptance['exported_geometry_matches_requested_matrix']), 'matrix_acceptance': acceptance}


def compare_rotation_axes(first, second, reference_world_mm):
    """Compare two fitted axes at the same local reference, ignoring axis sign."""
    reference = _vec(reference_world_mm)
    axes, near = [], []
    for fit in (first, second):
        if fit.get('pure_rotation_positions_fit_verified') is not True:
            raise ValueError('Both exported point fits must support pure rotations')
        axis = _vec(fit['axis_world'])
        origin = _vec(fit['axis_point_nearest_before_centroid_mm'])
        if abs(math.hypot(*axis) - 1) > 1e-10:
            raise ValueError('A unit rotation axis is required')
        parameter = _dot(_sub(reference, origin), axis)
        near.append([p + parameter * u for p, u in zip(origin, axis, strict=True)])
        axes.append(axis)
    angle = math.atan2(math.hypot(*_cross(*axes)), abs(_dot(*axes)))
    return {'axis_angle_difference_degrees': math.degrees(angle),
            'nearest_point_separation_mm': math.dist(*near), 'reference_world_mm': list(reference),
            'nearest_axis_points_mm': near, 'axis_uncertainty_bound_verified': False}
