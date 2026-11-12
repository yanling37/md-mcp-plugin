"""Read-only click-ray intersection and conditional pivot uncertainty on a bound OBJ."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re

from . import mesh
from .common import digest
from .world_projection import _dot, _intrinsics, _number, _validated_pose, _vector, project_world


SCHEMA = 'md-world-ring-pivot-v1'
_MESH_FIELDS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices', 'face_labels')
_PUBLIC_VIEW = ('view', 'coordinate_space', 'mapping_fingerprint', 'mapping', 'selector_chain', 'scene', 'scene_rect', 'viewport')
_DEPTH_TIE_MM = 1e-6


def _success(receipt, label):
    result = receipt.get('result') if isinstance(receipt, dict) else None
    if receipt is None or not isinstance(result, dict) or receipt.get('status') != 'SUCCEEDED' or result.get('verified') is not True:
        raise ValueError(f'{label} must be a successful verified receipt')
    return result


def _hex(value, size, label):
    if type(value) is not str or re.fullmatch(f'[0-9a-f]{{{size}}}', value) is None:
        raise ValueError(f'Invalid {label}')
    return value


def _frame_id(receipt):
    value = _hex(receipt.get('operation_id'), 32, 'frame operation ID')
    if receipt.get('request_id', value) != value:
        raise ValueError('Frame request and operation IDs differ')
    return value


def _same_fields(a, b, fields):
    return all(key in a and key in b and a[key] == b[key] for key in fields)


def _click_point(click_receipt, token, view):
    click = _success(click_receipt, 'Click')
    params = click_receipt['params']
    before, after = click['before'], click['after']
    if (click_receipt.get('operation') != 'canvas.gesture' or params.get('action') != 'click'
            or params.get('view') != '3d' or params.get('modifiers') != [] or params.get('scene_token') != token
            or click.get('scene_token_after') != token or click.get('dispatch_completed') is not True
            or click.get('paired_release_verified') is not True or click.get('press_outstanding') is not False
            or not _same_fields(params, before, ('view_ref', 'view_fingerprint'))
            or not _same_fields(before, after, ('view_ref', 'view_fingerprint'))
            or not _same_fields(before, view, _PUBLIC_VIEW) or not _same_fields(after, view, _PUBLIC_VIEW)):
        raise ValueError('Click and retained frames do not share the verified public 3D viewport mapping')
    events = click['events']
    if (type(events) is not list or len(events) != 3 or click.get('event_calls') != 3
            or events[0].get('stage') != 'hover' or events[1].get('stage') != 'press'
            or events[2].get('stage') not in ('release', 'cleanup_release')):
        raise ValueError('Expected the actual closed hover/press/release click receipt')
    point = _vector(events[1]['viewport_logical'], 2, 'delivered viewport click')
    for event, button, buttons in zip(events, (0, 1, 1), (0, 1, 0), strict=True):
        if (event.get('viewport_logical') != point or event.get('button') != button or event.get('buttons') != buttons
                or event.get('modifiers') != 0 or any(event.get(k) is not True for k in ('sent', 'returned', 'accepted'))):
            raise ValueError('The actual click events do not share one accepted viewport point')
    if params.get('points') != [point]:
        raise ValueError('Requested and delivered click coordinates differ')
    return point


def _asset_for_frame(frame, token, identity, arrays, arrays_hash):
    proof = frame['mesh_proof']
    if proof.get('world_arrays_sha256') != arrays_hash or proof['before']['capture_id'] == proof['after']['capture_id']:
        raise ValueError('Retained frame does not prove stable independent world OBJ captures')
    for summary in (proof['before'], proof['after']):
        options = summary['export']['options']
        expected = {'scale': 1.0, 'axisX': 0, 'axisY': 1, 'axisZ': 2, 'bInvertX': False, 'bInvertY': False,
                    'bInvertZ': False, 'bExportGarment': True, 'bExportAvatar': False, 'bThin': True}
        if (summary.get('scene_token') != token or summary.get('scene_identity') != identity
                or not _same_fields(summary, arrays, ('topology_fingerprint', 'vertex_count'))
                or not _same_fields(options, expected, expected)):
            raise ValueError('Frame mesh identity, topology or world-mm export options differ')
    certificate = proof['group_binding']
    asset = proof['before']['export']['obj_asset']
    if (certificate.get('verified') is not True or certificate.get('scene_token') != token
            or certificate.get('scene_identity') != identity or certificate.get('obj_asset') != asset
            or certificate.get('snapshot_id') != proof['before']['snapshot_id']):
        raise ValueError('The selected label certificate does not belong to the retained mesh')
    return certificate['target'], asset


def _bind(click_receipt, before_receipt, after_receipt, calibration_receipt, rings_receipt, obj_bytes):
    before, after = _success(before_receipt, 'Calibration frame'), _success(after_receipt, 'Ring frame')
    calibration, rings = _success(calibration_receipt, 'Projection calibration'), _success(rings_receipt, 'Ring observation')
    before_id, after_id = _frame_id(before_receipt), _frame_id(after_receipt)
    token = _hex(before['scene_token'], 64, 'scene token')
    identity, view = before['scene_identity'], before['view']
    cmt = _hex(before['camera']['cmt_sha256'], 64, 'CMT SHA256')
    if (before_id == after_id or after.get('scene_identity') != identity or after.get('scene_token') != token
            or after['camera'].get('cmt_sha256') != cmt or after['camera']['projection_pose'] != before['camera']['projection_pose']
            or view.get('view') != '3d' or view.get('coordinate_space') != 'viewport_logical_pixels'
            or not _same_fields(view, after['view'], (*_PUBLIC_VIEW, 'view_ref', 'view_fingerprint'))):
        raise ValueError('Require distinct retained frames with matching scene, public viewport and CMT')
    if type(before.get('selection')) is not list or len(before['selection']) != 1 or before['selection'] != after.get('selection'):
        raise ValueError('Frames must have the same single selected pattern')
    selected = before['selection'][0]
    target, group = selected['pattern_uuid'], selected['name']
    if (selected.get('selected') is not True or before.get('selected_names') != [group]
            or after.get('selected_names') != [group] or rings.get('selection') != before['selection']):
        raise ValueError('The selected UUID and export label are not consistent')
    scope = {'scene_token': token, 'view_ref': view['view_ref'], 'view_fingerprint': view['view_fingerprint'], 'camera_cmt_sha256': cmt}
    for receipt, frame, frame_id in ((calibration, before, before_id), (rings, after, after_id)):
        if (not _same_fields(receipt, scope, scope) or receipt.get('frame_request_id') != frame_id
                or receipt.get('png_sha256') != frame['image']['sha256']):
            raise ValueError('The calibration or ring observation is not bound to its retained frame')
    dpr = _number(view['mapping']['device_pixel_ratio'], 'device pixel ratio')
    logical = _vector(view['mapping']['viewport_size'], 2, 'logical viewport')
    size = [after['image']['width'], after['image']['height']]
    if (dpr <= 0 or min(logical) <= 0 or any(type(x) is not int or x <= 0 for x in size)
            or any(abs(pixels - dpr * units) > .5 for pixels, units in zip(size, logical, strict=True))
            or rings.get('image_size_px') != size or rings.get('coordinate_space') != 'png_pixel_index_centers_x_right_y_down'):
        raise ValueError('Require the calibrated top-left framebuffer pixel-center convention')
    for frame in (before, after):
        image = frame['image']
        if (image.get('pixel_origin') != 'top_left' or image.get('device_pixel_ratio') != dpr
                or image.get('viewport_size') != logical or image.get('mapping_fingerprint') != view['mapping_fingerprint']
                or [image['width'], image['height']] != size):
            raise ValueError('Frame image geometry differs from the bound viewport')
    if calibration.get('pattern_uuid') != target:
        raise ValueError('Calibration belongs to a different selected UUID')
    click_xy = _click_point(click_receipt, token, view)
    if any(not 0 <= value < limit for value, limit in zip(click_xy, logical, strict=True)):
        raise ValueError('The delivered click lies outside the bound viewport')

    arrays = mesh.parse_obj(obj_bytes)
    arrays_hash = digest({key: arrays[key] for key in _MESH_FIELDS})
    if calibration.get('world_arrays_sha256') != arrays_hash:
        raise ValueError('OBJ world arrays differ from the calibrated mesh')
    first_label, _ = _asset_for_frame(before, token, identity, arrays, arrays_hash)
    second_label, asset = _asset_for_frame(after, token, identity, arrays, arrays_hash)
    if hashlib.sha256(obj_bytes).hexdigest() != asset['sha256'] or len(obj_bytes) != asset['byte_count']:
        raise ValueError('OBJ bytes are not the retained ring-frame asset')
    subset = mesh.extract_group(arrays, group)
    if (first_label != second_label or second_label.get('pattern_uuid') != target or second_label.get('group_name') != group
            or second_label.get('group_topology_fingerprint') != subset['topology_fingerprint']
            or second_label.get('face_count') != len(subset['faces']) or second_label.get('vertex_count') != subset['vertex_count']):
        raise ValueError('OBJ group does not match the selected pattern label certificate')
    cameras = {'camera': calibration['camera']}
    if calibration.get('cloth_camera') is not None:
        cameras['cloth_camera'] = calibration['cloth_camera']
    for camera in cameras.values():
        pose = _validated_pose(camera)
        if camera.get('viewport_size_px') != size or not _same_fields(pose, before['camera']['projection_pose'], pose):
            raise ValueError('Calibrated camera pose/size differs from the frame CMT')
        _intrinsics(camera)
    bound = {**scope, 'pattern_uuid': target, 'group_name': group, 'frame_request_ids': [before_id, after_id],
             'click_operation_id': _hex(click_receipt.get('operation_id'), 32, 'click operation ID'), 'ring_png_sha256': after['image']['sha256'],
             'obj_sha256': asset['sha256'], 'world_arrays_sha256': arrays_hash}
    return arrays, calibration, cameras, rings, bound, size, dpr, click_xy


@dataclass(frozen=True, slots=True)
class _Triangle:
    index: int
    group: str
    world: tuple
    raster: tuple
    bounds: tuple
    determinant: float


def _cross2(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _project_triangles(arrays, camera):
    origin = camera['camera_center_world_mm']
    focal, center = _intrinsics(camera)
    projected = []
    points = arrays['positions_mm']
    for point in points:
        offset = [a - b for a, b in zip(point, origin, strict=True)]
        depth = -_dot(offset, camera['forward'])
        if depth <= 0 or not math.isfinite(depth):
            raise ValueError('The retained OBJ must lie in front of the calibrated camera')
        uv = (center[0] + focal[0] * _dot(offset, camera['right']) / depth,
              center[1] - focal[1] * _dot(offset, camera['up']) / depth, depth)
        if not all(math.isfinite(x) for x in uv):
            raise ValueError('Nonfinite projected OBJ vertex')
        projected.append(uv)
    triangles = []
    for index, (face, label) in enumerate(zip(arrays['faces'], arrays['face_labels'], strict=True)):
        if len(face) != 3 or len(label['groups']) != 1:
            raise ValueError('Require triangle faces with one explicit OBJ group each')
        pixels = tuple(projected[i] for i in face)
        determinant = _cross2(*pixels)
        if abs(determinant) <= 1e-12:
            continue
        x, y, _ = zip(*pixels, strict=True)
        triangles.append(_Triangle(index, label['groups'][0], tuple(points[i] for i in face), pixels,
                                   (min(x), min(y), max(x), max(y)), determinant))
    return triangles


def _lift(triangle, pixel, *, inside=True):
    a, b, c = triangle.raster
    screen = [_cross2(b, c, pixel) / triangle.determinant, _cross2(c, a, pixel) / triangle.determinant]
    screen.append(1 - sum(screen))
    if inside and min(screen) < -1e-8:
        return None
    unscaled = [weight / p[2] for weight, p in zip(screen, triangle.raster, strict=True)]
    depth = 1 / math.fsum(unscaled)
    weights = [value * depth for value in unscaled]
    world = [math.fsum(weight * point[k] for weight, point in zip(weights, triangle.world, strict=True)) for k in range(3)]
    return {'point_world_mm': world, 'camera_depth_mm': depth, 'obj_face_index_zero_based': triangle.index,
            'obj_group_name': triangle.group, 'barycentric_world': weights}


def _overlaps(first, second):
    return first[0] <= second[2] and second[0] <= first[2] and first[1] <= second[3] and second[1] <= first[3]


def _hit(triangles, pixel, group):
    x, y = pixel
    candidates = []
    for triangle in triangles:
        if _overlaps(triangle.bounds, (x, y, x, y)):
            hit = _lift(triangle, pixel)
            if hit is not None:
                candidates.append((hit['camera_depth_mm'], triangle.index, hit, triangle))
    if not candidates:
        return None, None, 'NO_EXPORTED_SURFACE_AT_PIXEL'
    candidates.sort(key=lambda row: row[:2])
    nearest = candidates[0][0]
    if any(triangle.group != group for depth, _, _, triangle in candidates if depth <= nearest + _DEPTH_TIE_MM):
        return None, None, 'FOREGROUND_OR_COINCIDENT_OTHER_EXPORT_GROUP'
    _, _, hit, triangle = candidates[0]
    hit['coincident_target_face_count'] = sum(depth <= nearest + _DEPTH_TIE_MM for depth, *_ in candidates)
    return hit, triangle, None


def _clip(polygon, sign):
    if not polygon:
        return []
    output = []
    previous = polygon[-1]
    old = sign(previous)
    for point in polygon:
        new = sign(point)
        if (old >= 0) != (new >= 0):
            t = old / (old - new)
            output.append(tuple(a + t * (b - a) for a, b in zip(previous, point, strict=True)))
        if new >= 0:
            output.append(point)
        previous, old = point, new
    return output


def _area(polygon):
    if len(polygon) < 3:
        return 0.
    return math.fsum(_cross2(polygon[0], a, b) for a, b in zip(polygon[1:-1], polygon[2:], strict=True)) / 2


def _clip_box(triangle, box):
    left, top, right, bottom = box
    polygon = [p[:2] for p in triangle.raster]
    for sign in (lambda p: p[0] - left, lambda p: right - p[0], lambda p: p[1] - top, lambda p: bottom - p[1]):
        polygon = _clip(polygon, sign)
    return polygon


def _nonoverlapping_coverage(polygons, box_area):
    areas = [_area(p) for p in polygons]
    epsilon = max(1e-10, 1e-8 * box_area)
    total = math.fsum(abs(a) for a in areas)
    if abs(total - box_area) > epsilon or len(polygons) > 128:
        return False, total
    for i, first in enumerate(polygons):
        if abs(areas[i]) <= epsilon:
            continue
        direction = math.copysign(1., areas[i])
        for second in polygons[i+1:]:
            overlap = second
            for a, b in zip(first, first[1:] + first[:1], strict=True):
                overlap = _clip(overlap, lambda p: direction * _cross2(a, b, p))
            if abs(_area(overlap)) > epsilon:
                return False, total
    return True, total


def _envelope(triangles, group, pixel, half_width, origin, size):
    x, y = pixel
    box = (x-half_width, y-half_width, x+half_width, y+half_width)
    lifted, polygons, foreign_depth, selected_depth = [], [], math.inf, -math.inf
    for triangle in triangles:
        if not _overlaps(triangle.bounds, box):
            continue
        polygon = _clip_box(triangle, box)
        if not polygon:
            continue
        samples = [_lift(triangle, p, inside=False) for p in polygon]
        if triangle.group == group:
            lifted.extend(row['point_world_mm'] for row in samples)
            polygons.append(polygon)
            selected_depth = max(selected_depth, *(row['camera_depth_mm'] for row in samples))
        else:
            foreign_depth = min(foreign_depth, *(row['camera_depth_mm'] for row in samples))
    coverage, total = _nonoverlapping_coverage(polygons, (2 * half_width)**2)
    return {'assumed_pixel_box_xyxy': list(box), 'clipped_target_face_count': len(polygons),
            'target_projected_area_sum_px2': total, 'complete_nonoverlapping_target_coverage': coverage,
            'possible_foreign_export_occlusion': foreign_depth <= selected_depth + _DEPTH_TIE_MM,
            'box_inside_frame': box[0] >= -.5 and box[1] >= -.5 and box[2] <= size[0]-.5 and box[3] <= size[1]-.5,
            'surface_world_bounds_mm': [[min(v[k] for v in lifted), max(v[k] for v in lifted)] for k in range(3)] if lifted else None,
            'maximum_distance_from_nominal_mm': max((math.dist(origin, value) for value in lifted), default=None)}


def _local_pixel_limit(triangle, pixel, origin, allowance, maximum):
    """Invert the exact same-face corner bound within a tested pixel range."""
    lo, hi = 0., maximum
    for _ in range(36):
        width = (lo + hi) / 2
        samples = [_lift(triangle, (pixel[0] + dx, pixel[1] + dy)) for dx in (-width, width) for dy in (-width, width)]
        if all(row is not None and math.dist(origin, row['point_world_mm']) <= allowance for row in samples):
            lo = width
        else:
            hi = width
    return lo


def _ray(camera, pixel):
    focal, center = _intrinsics(camera)
    x, y = ((p-c)/f for p, c, f in zip(pixel, center, focal, strict=True))
    direction = [x*r - y*u - f for r, u, f in zip(camera['right'], camera['up'], camera['forward'], strict=True)]
    norm = math.hypot(*direction)
    return {'origin_world_mm': camera['camera_center_world_mm'], 'direction_unit_world': [d/norm for d in direction]}


def estimate_pivot(click_receipt, frame_before_receipt, frame_after_receipt, calibration_receipt, rings_receipt,
                   obj_bytes, *, angle_degrees=1., tolerance_mm=.002, raster_origin_margin_px=.5):
    """Measure a conditional pivot candidate from trusted retained click/frame receipts.

    The calibration's admitted scalar residual is used as an assumed pixel
    perturbation, plus an explicit raster origin margin. A fit made with edge
    normals does not certify absolute 2D accuracy at the interior click.
    """
    angle = _number(angle_degrees, 'requested rotation degrees')
    tolerance = _number(tolerance_mm, 'position tolerance')
    margin = _number(raster_origin_margin_px, 'raster origin margin')
    factor = 2 * abs(math.sin(math.radians(math.remainder(angle, 360.)) / 2))
    if not (0 < factor and 0 < tolerance <= .002 and 0 <= margin <= 4):
        raise ValueError('Require nonidentity rotation, position tolerance in (0,.002] mm, and bounded raster margin')
    allowed = tolerance / factor
    if not math.isfinite(allowed):
        raise ValueError('The requested angle gives a nonfinite pivot error budget')
    arrays, calibration, cameras, rings, binding, size, dpr, click = _bind(
        click_receipt, frame_before_receipt, frame_after_receipt, calibration_receipt, rings_receipt, obj_bytes)
    budget = _number(calibration['max_error_px'], 'admitted calibration error')
    if not 0 < budget <= 10:
        raise ValueError('Require a positive bounded calibration pixel budget')
    observed_limits = {}
    for name, prefix in (('camera', ''), ('cloth_camera', 'shared_focal_')):
        if name not in cameras:
            continue
        fit = _number(calibration[prefix + 'fit']['max_error_px'], 'fit residual')
        holdout = _number(calibration[prefix + 'holdout']['max_error_px'], 'holdout residual')
        if not 0 <= fit <= budget or not 0 <= holdout <= budget:
            raise ValueError('Calibration fit and holdout residuals exceed the admitted pixel budget')
        observed_limits[name] = max(fit, holdout)
    pixel = [x * dpr - .5 for x in click]
    half_width = budget + margin
    group = binding['group_name']
    projected = {name: _project_triangles(arrays, camera) for name, camera in cameras.items()}
    hits = {name: _hit(triangles, pixel, group) for name, triangles in projected.items()}
    primary, triangle, reason = hits['camera']
    result = {'schema': SCHEMA, 'status': 'SURFACE_CANDIDATE' if primary is not None else 'NO_SURFACE_CANDIDATE',
              'binding': binding, 'delivered_click_viewport_logical_px': click, 'nominal_click_png_index_px': pixel,
              'raster_mapping': 'logical_times_DPR_minus_half_pixel_for_pixel_index_centers',
              'ray': _ray(cameras['camera'], pixel), 'surface_candidate': primary, 'reason': reason,
              'native_pivot_verified': False, 'native_axis_verified': False, 'native_ring_hit_verified': False,
              'camera_and_mesh_sampled_in_click_receipt': False, 'native_rotation_ready': False}
    if primary is None:
        return result
    origin = primary['point_world_mm']
    envelopes = {name: _envelope(triangles, group, pixel, half_width, origin, size) for name, triangles in projected.items()}
    measured_sensitivity = {name: _envelope(triangles, group, pixel, observed_limits[name], origin, size)['maximum_distance_from_nominal_mm']
                            for name, triangles in projected.items()}
    radius = max((row['maximum_distance_from_nominal_mm'] for row in envelopes.values()
                  if row['maximum_distance_from_nominal_mm'] is not None), default=None)
    covered = all(row['complete_nonoverlapping_target_coverage'] and row['box_inside_frame']
                  and not row['possible_foreign_export_occlusion'] for row in envelopes.values())
    other_convention, _, _ = _hit(projected['camera'], [x * dpr for x in click], group)
    reprojected = project_world(cameras['camera'], origin)
    comparison = []
    for candidate in rings['candidates']:
        center = _vector(candidate['ellipse_center_px'], 2, 'measured ellipse center')
        comparison.append({'color_class': candidate['color_class'], 'image_status': candidate['status'],
                           'ellipse_center_png_index_px': center, 'candidate_reprojected_png_index_px': reprojected,
                           'center_residual_px': math.dist(reprojected, center),
                           'ellipse_center_fit_sensitivity_px': candidate['uncertainty'].get('alternate_sector_center_max_shift_px')})
    displacement = radius * factor if radius is not None else None
    holdout = calibration['holdout']
    result.update(
        ring_center_reprojection=comparison,
        uncertainty={'scope': 'conditional exported surface envelope with two calibrated camera models when available',
                     'calibration_scope': calibration.get('verification_scope'),
                     'holdout_max_error_px': holdout['max_error_px'], 'holdout_normal_only_count': holdout['normal_only_count'],
                     'holdout_count': holdout['count'], 'assumed_calibration_per_axis_px': budget,
                     'observed_residual_assumed_per_axis_px': observed_limits,
                     'observed_residual_only_surface_sensitivity_mm': measured_sensitivity,
                     'raster_origin_margin_per_axis_px': margin, 'assumed_total_per_axis_px': half_width,
                     'absolute_pixel_error_bound_verified': False, 'complete_exported_target_coverage': covered,
                     'maximum_distance_from_nominal_mm': radius, 'camera_model_envelopes': envelopes,
                     'camera_model_nominal_offsets_mm': {name: math.dist(origin, hit['point_world_mm']) if hit else None
                                                         for name, (hit, _, _) in hits.items()},
                     'unshifted_pixel_convention_offset_mm': math.dist(origin, other_convention['point_world_mm']) if other_convention else None},
        pivot_precision_gate={'requested_angle_degrees': angle, 'position_tolerance_mm': tolerance,
                              'maximum_transverse_axis_line_offset_mm_if_pivot_uses_full_budget': allowed,
                              'conditional_pivot_only_position_error_mm': displacement,
                              'conditional_surface_envelope_within_pivot_budget': covered and displacement is not None and displacement <= tolerance,
                              'nominal_face_pixel_halfwidth_px_for_pivot_only': _local_pixel_limit(triangle, pixel, origin, allowed, half_width),
                              'requires_independent_native_axis_line_and_whole_mesh_matrix_validation': True})
    return result
