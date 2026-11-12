"""Verify one calibrated ring candidate against an explicit exported world matrix."""

import math
from pathlib import Path
import re

from . import mesh, world_adapter as world, world_frame as frames, world_ring_observation as rings
from .common import digest, scene


ROUTE = 'current_calibrated_world_ring'
MISMATCH = 'WORLD_ROTATION_MATRIX_MISMATCH'
PLAN_KEYS = {'frame_request_id', 'roi_px', 'start', 'activation', 'end'}
_RING_AXIS = {'red': 0, 'green': 1, 'blue': 2}
_OPAQUE = {'red': b'\xff\x00\x00', 'green': b'\x00\xff\x00', 'blue': b'\x00\x00\xff'}

# Geometry and camera from the flat-pattern axis repeat, A/B/A trials, and +1°
# matrix readbacks. The profile describes this captured pose and view mapping.
def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _failure(message, *, effects=False):
    return {'status': 'UNKNOWN' if effects else 'REJECTED', 'result': {'verified': False, 'route': ROUTE},
            'error': {'code': 'WORLD_ROTATION_READBACK_UNKNOWN' if effects else 'WORLD_ROTATION_PREFLIGHT_REJECTED',
                      'message': str(message)}, 'effects_started': effects, 'requires_recovery': effects}


def _point_supported(image, point, support, *, opaque_color=None):
    for x, y in support:
        if math.dist(point, (x, y)) > (math.sqrt(.5) if opaque_color is not None else 1.5):
            continue
        if opaque_color is None:
            return True
        channels = image['channels']
        start = channels * (y * image['width'] + x)
        rgba = image['pixels'][start:start+channels]
        if rgba in (opaque_color, opaque_color + b'\xff'):
            return True
    return False


def _ring_contour(frame, png, roi, color, *, allow_clipped=False, decoded=None):
    image = frame['image']
    if decoded is None:
        decoded = rings._decode_capture(png, image['sha256'])
    observed = rings._observe_decoded(*decoded, roi_px=roi, only_color=color)
    _require(observed['image_size_px'] == [image['width'], image['height']], 'Decoded PNG size differs')
    color_fraction = .75 if color == 'blue' else .60
    coverage = .55 if color == 'red' else .85
    candidates = [row for row in observed['candidates'] if row['color_class'] == color
                  and row['status'] == 'IMAGE_CONTOUR_CANDIDATE'
                  and row['dominant_color_support_fraction'] >= color_fraction
                  and row['angular_coverage']['occupied_fraction'] >= coverage]
    _require(len(candidates) == 1, f'Expected one qualified current {color} ring contour')
    candidate = candidates[0]
    roi = observed['roi_px']
    left, top, right, bottom = candidate['support_pixel_bounds_xyxy_inclusive']
    _require(allow_clipped or roi[0]+1 < left < right < roi[2]-2 and roi[1]+1 < top < bottom < roi[3]-2,
             f'The qualified {color} ring support must be inside the supplied ROI')
    return candidate, roi


def _blue_contour(frame, png, roi):
    return _ring_contour(frame, png, roi, 'blue')


def _connected_segment(support, a, b):
    first = min(support, key=lambda point: math.dist(point, a))
    last = min(support, key=lambda point: math.dist(point, b))
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    pending = [first]
    seen = {first}
    for x, y in pending:
        if (x, y) == last:
            return True
        for sx, sy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
            point = (x + sx, y + sy)
            if point not in support or point in seen:
                continue
            t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_sq))
            if math.dist(point, (a[0] + t * dx, a[1] + t * dy)) <= 3:
                seen.add(point)
                pending.append(point)
    return False


def qualify_path(frame, png, plan, *, contour=None, allow_clipped=False):
    """Bind the submitted logical points to one observed ring contour."""
    color = plan.get('ring_color', 'blue')
    _require(color in _RING_AXIS, 'Use a red, green or blue ring')
    view, image = frame['view'], frame['image']
    mapping = view['mapping']
    size = mapping['viewport_size']
    dpr = mapping['device_pixel_ratio']
    _require(view['view'] == '3d' and view['coordinate_space'] == 'viewport_logical_pixels'
             and view['scene_item_grabber_absent'] is True and mapping['enabled'] is True
             and mapping['relative_visible'] is True, 'The current 3D view must be idle and visible')
    checked = {action['object_name'] for action in mapping['checked_actions'] if action['enabled'] is True}
    _require({'actionMove_Pattern', 'action_World_Coordinate_Gizmo'} <= checked,
             'Select Move Pattern and World Coordinate before capturing the ring')
    _require(type(dpr) in (int, float) and 0 < dpr <= 8 and image['device_pixel_ratio'] == dpr
             and image['pixel_origin'] == 'top_left' and image['viewport_size'] == size
             and all(abs(length*dpr-pixels) <= .5 for length, pixels in zip(size, (image['width'], image['height']), strict=True)),
             'The captured PNG and logical viewport must share the verified pixel map')
    points = [world._xy(plan[key]) for key in ('start', 'activation', 'end')]
    _require(all(0 <= value < limit and math.floor(value+.5) < limit
                 for point in points for value, limit in zip(point, size, strict=True)), 'A ring point lies outside the viewport')
    first, last = math.dist(points[0], points[1]), math.dist(points[1], points[2])
    _require(first >= mapping['start_drag_distance'] and 0 < first and 0 < last and first+last <= 128,
             'The requested drag must cross the current activation distance and fit the 128 px bound')
    pixels = [[value*dpr-.5 for value in point] for point in points]
    decoded = rings._decode_capture(png, image['sha256'])
    candidate, roi = (_ring_contour(frame, png, plan['roi_px'], color, allow_clipped=allow_clipped, decoded=decoded)
                      if contour is None else contour)
    decoded, _ = decoded
    if allow_clipped:
        _require(color == 'red' and all(roi[0] + 4 <= x < roi[2] - 4 and roi[1] + 4 <= y < roi[3] - 4 for x, y in pixels),
                 'The entire enlarged red path must stay inside the visible viewport margin')
    support = {(x, y) for y, x0, x1 in candidate['support_runs_y_x0_x1_exclusive'] for x in range(x0, x1)}
    _require(_point_supported(decoded, pixels[0], support, opaque_color=_OPAQUE[color])
             and _point_supported(decoded, pixels[1], support, opaque_color=None if color == 'blue' else _OPAQUE[color])
             and _point_supported(decoded, pixels[2], support),
             f'The current {color} contour does not support the press, activation and end')
    if color == 'red':
        _require(all(_connected_segment(support, a, b) for a, b in zip(pixels, pixels[1:])),
                 'The red ring path must follow a single visible arc')

    cx, cy = candidate['ellipse_center_px']
    major, minor = candidate['ellipse_semiaxes_px']
    orientation = math.radians(candidate['ellipse_major_axis_image_degrees'])
    cos, sin = math.cos(orientation), math.sin(orientation)
    phases = []
    for x, y in pixels:
        dx, dy = x-cx, y-cy
        phases.append(math.degrees(math.atan2((-dx*sin+dy*cos)/minor, (dx*cos+dy*sin)/major)))
    return {'points': points, 'png_sha256': image['sha256'], 'roi_px': roi,
            'ring_color': color, f'{color}_ring_center_px': [cx, cy], f'{color}_ring_semiaxes_px': [major, minor],
            'ring_support_sha256': digest(candidate['support_runs_y_x0_x1_exclusive']),
            'image_phase_deltas_degrees': [math.remainder(b-a, 360) for a, b in zip(phases, phases[1:])],
            'segment_lengths_logical_px': [first, last]}


def closed_matrix_result(payload):
    """Only a closed batch with complete readback can settle a failed target."""
    proof = payload['matrix_acceptance']
    native = payload['native']
    zero = native['zero_operation_counts']
    _require(payload['primitive_dispatch_verified'] is True
             and payload['requested_pattern_UUID_to_exact_group_label_verified'] is True
             and native['event_calls'] == 5 and native['dispatch_completed'] is True
             and native['paired_release_verified'] is True and native['press_outstanding'] is False
             and native['physical_state_unchanged'] is True and native['all_mouse_events_nonspontaneous'] is True
             and set(zero) == set(world.ZERO_COUNTS) and all(type(value) is int and value == 0 for value in zero.values())
             and all(proof.get(key) is True for key in ('same_scene', 'distinct_captures', 'stable_label_bijection',
                                                       'normal_readback_complete', 'uv_readback_complete',
                                                       'outside_target_export_arrays_exact'))
             and all(payload['measurement'].get(key) is True for key in (
                 'topology_and_index_maps_exact', 'uv_exact', 'other_export_groups_all_exact')),
             'The closed drag did not establish complete isolated geometry readback')
    if payload.get('assigned_topstitch_refresh') is not None:
        refresh = payload['assigned_topstitch_refresh']
        _require(refresh['offset_restored'] is True and refresh['positive_control_verified'] is True
                 and refresh['transaction_closed'] is True and refresh['sdk_set_calls'] == 2
                 and refresh['restored_capture_id'] == payload['after_mesh']['capture_id']
                 and refresh['assignment']['pattern_uuid'] == payload['selection']['pattern_uuid']
                 and refresh['assignment']['exported_group'] in proof['moving_group_readback']
                 and len(proof['moving_group_readback']) == 2,
                 'The bound Topstitch offset transaction did not close')
    return proof


def _bind_topstitch(current, target, extras, baseline):
    from .dispatch import _module

    companion = _module('world_assigned_topstitch')
    snapshot = world._mesh_read()
    _require(digest({key: snapshot[key] for key in frames.MESH_FIELDS}) == baseline,
             'The current world OBJ changed before Topstitch binding')
    binding = companion.bind(current, target, snapshot)
    _require(binding is not None and extras == [binding['exported_group']],
             'The extra exported group must be the sole current target Texture Topstitch')
    original = companion.preflight_offset(binding)

    def settle(checked_scene, before, rotated):
        _require(checked_scene == current and companion.bind(current, target, before) == binding
                 and companion.bind(current, target, rotated) == binding,
                 'The selected Topstitch assignment changed across rotation')
        return companion.offset_roundtrip(current, binding, rotated, expected_original=original)

    return (binding['exported_group'],), settle, {'assignment': binding, 'original_offset_sdk': original}


def rotate(target, matrix, tolerance, token, plan, *, verification_profile='strict', include_assigned_topstitch=False,
           retained_frame=None):
    from .world_rotation_reconciliation import active_rotation

    effects = False
    try:
        scaled_keys = {'gizmo_scale_steps', 'expanded_ring_support_sha256'}
        _require(type(plan) is dict and PLAN_KEYS <= plan.keys() <= PLAN_KEYS | {'ring_color'} | scaled_keys and plan['roi_px'] is not None,
                 'Use a current capture request, a physical ROI and three logical points')
        _require(verification_profile in ('strict', 'positions'), 'Use the strict or positions verification profile')
        _require(type(include_assigned_topstitch) is bool, 'include_assigned_topstitch must be a boolean')
        world._validate_rigid_matrix(matrix, tolerance)
        if matrix[:3] == [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]:
            return {'status': 'NO_OP', 'result': {'verified': True, 'route': ROUTE, 'write_attempted': False},
                    'error': None, 'effects_started': False, 'requires_recovery': False}
        if 'ring_color' in plan:
            color = plan['ring_color']
            _require(color in _RING_AXIS, 'Use a red, green or blue ring')
            axis = _RING_AXIS[color]
            _require(all(math.isclose(matrix[axis][index], int(index == axis), rel_tol=0, abs_tol=1e-12) for index in range(4)),
                     f'The requested world axis does not match the {color} ring')
        if scaled_keys & plan.keys():
            from .dispatch import _module

            active = active_rotation.get()
            _require(scaled_keys <= plan.keys() and plan['gizmo_scale_steps'] == 2 and plan.get('ring_color') == 'red'
                     and not include_assigned_topstitch
                     and retained_frame is None and active is not None and active['scene_token'] == token
                     and matrix[1][1] >= math.cos(math.radians(5.)),
                     'Use a journaled calibrated red rotation of at most 5 degrees with two scale increments')
            support_sha = world._hex(plan['expanded_ring_support_sha256'], 'expanded_ring_support_sha256')
            ordinary_plan = {key: value for key, value in plan.items() if key not in scaled_keys}

            def dispatch_enlarged(frame, png):
                marker = active_rotation.set(None)
                try:
                    return rotate(target, matrix, tolerance, token, ordinary_plan, verification_profile=verification_profile,
                                  retained_frame=(frame, png, support_sha))
                finally:
                    active_rotation.reset(marker)

            _module('gizmo_scale')
            return _module('gizmo_scale_series').rotate_scaled(target, token, ordinary_plan,
                                                               active['parent_request_id'], dispatch_enlarged)
        current = scene()
        _require(current['scene_token'] == token, 'The current scene token changed')
        row, group = world._target(current, target)
        retained, png = retained_frame[:2] if retained_frame else frames._read_retained_frame(plan['frame_request_id'], token)
        selected = [{'pattern_uuid': target, 'name': group, 'index': row['index'], 'selected': True}]
        _require(retained['selection'] == selected and retained['selected_names'] == [group]
                 and type(retained['mesh_proof']) is dict, 'Capture the current singleton-selected pattern with include_mesh')

        # Ring placement is visual state and can change without the scene token.
        # A new bounded capture in this request must reproduce it before input.
        reread = frames.capture({'scene_token': token, 'pattern_uuid': target, 'include_mesh': True})
        if reread['status'] != 'SUCCEEDED':
            return reread
        fresh = reread['result']
        proof = fresh['mesh_proof']
        baseline = world._hex(proof['world_arrays_sha256'], 'world_arrays_sha256')
        _require(fresh['verified'] is True and fresh['scene_identity'] == current['identity']
                 and fresh['scene_token'] == token and fresh['selection'] == selected
                 and fresh['selected_names'] == [group], 'The fresh ring selection or scene identity changed')
        _require(fresh['view'] == retained['view'] and fresh['camera'] == retained['camera'],
                 'The current 3D viewport or camera changed after ring capture')
        _require(baseline == retained['mesh_proof']['world_arrays_sha256']
                 and proof['group_binding']['target']['pattern_uuid'] == target
                 and proof['group_binding']['target']['group_name'] == group
                 and proof['group_binding']['additional_unclassified_groups']
                 == retained['mesh_proof']['group_binding']['additional_unclassified_groups'],
                 'The complete world export or target label changed after ring capture')
        path_options = {'allow_clipped': True} if retained_frame else {}
        previous = qualify_path(retained, png, plan, **path_options)
        if retained_frame:
            _require(previous['ring_support_sha256'] == retained_frame[2],
                     'The enlarged red support changed from the calibrated path')
        qualification = previous
        if fresh['image']['sha256'] != retained['image']['sha256']:
            qualification = qualify_path(fresh, frames._verified_png(fresh), plan, **path_options)
            _require(all(qualification[key] == value for key, value in previous.items() if key != 'png_sha256'),
                     'The current observed ring support or path changed after capture')
        start, activation, end = qualification['points']
        view = fresh['view']
        qualification.update(frame_request_id=plan['frame_request_id'], scene_token=token,
                             camera_cmt_sha256=fresh['camera']['cmt_sha256'], view_fingerprint=view['view_fingerprint'],
                             before_world_arrays_sha256=baseline)
        extras = proof['group_binding']['additional_unclassified_groups']
        companions, settle_after, companion_preflight = (), None, None
        if include_assigned_topstitch:
            companions, settle_after, companion_preflight = _bind_topstitch(current, target, extras, baseline)
        else:
            _require(not extras, 'Additional exported groups require a verified Topstitch assignment')
        companion_options = {'companion_groups': companions, 'settle_after': settle_after} if include_assigned_topstitch else {}
        prefix = active_rotation.get()
        if prefix is not None:
            _require(prefix['scene_token'] == token, 'The active journaled rotation belongs to another scene')
            companion_options['rotation_recovery'] = dict(prefix, companion_preflight=companion_preflight)
        effects = True
        reply = world._execute('gizmo_drag', {
            'pattern_uuid': target, 'group_name': group, 'view_ref': view['view_ref'],
            'view_fingerprint': view['view_fingerprint'], 'scene_token': token, 'start': start,
            'activation': activation, 'end': end, 'purpose': 'rotation_ring', 'budget_ms': 1500,
            'expected_matrix_world_mm': matrix, 'matrix_tolerance_mm': tolerance,
        }, expected_world_arrays_sha256=baseline, **companion_options)
        effects = reply['effects_started']
        if reply['status'] != 'SUCCEEDED':
            return reply
        payload = reply['result']
        _require(payload['verified'] is True, 'The underlying primitive did not verify its readback')
        proof = closed_matrix_result(payload)
        strict = proof['exported_geometry_matches_requested_matrix']
        positions = proof['exported_positions_match_requested_matrix']
        matched = strict if verification_profile == 'strict' else positions
        _require(type(strict) is bool and type(positions) is bool and (not strict or positions)
                 and proof['matrix_world_mm'] == matrix and proof['tolerance_mm'] == tolerance
                 and payload['selection']['pattern_uuid'] == target and effects is True,
                 'The returned matrix, owner or effect scope differs from the request')
        result = {**payload, 'verified': matched, 'requested_world_rotation_verified': strict,
                  'requested_world_position_matrix_verified': positions, 'verification_profile': verification_profile,
                  'include_assigned_topstitch': include_assigned_topstitch,
                  'route': ROUTE, 'write_attempted': True, 'pattern_uuid': target, 'ring_frame': qualification,
                  'closed_world_rotation_mismatch_verified': not matched,
                  'verification_scope': ('exported target positions and normals with preserved topology/UV/non-target geometry'
                                         if verification_profile == 'strict' else
                                         'exported target positions with preserved topology/UV/non-target geometry')}
        return {'status': 'SUCCEEDED' if matched else 'FAILED', 'result': result,
                'error': None if matched else {'code': MISMATCH, 'message': 'The closed rotation did not match the requested world matrix'},
                'effects_started': True, 'requires_recovery': False}
    except Exception as exc:
        return _failure(exc, effects=effects)
