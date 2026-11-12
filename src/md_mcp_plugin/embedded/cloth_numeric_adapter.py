"""Current-scene cloth vertex binding, projected displacement, and OBJ readback."""

from base64 import b64decode
from fractions import Fraction
from hashlib import sha256
import math

from . import cloth, mesh, world_adapter
from .cloth_meshbox_uv_fit import _inventory
from .cloth_meshbox_zoom_observation import _frame_bytes
from .cloth_vertex_mapping import _orientation_safe, _plane_delta
from .common import digest
from .world_projection import _validated_pose, camera_pose_from_matrix, project_world


_ARRAYS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices',
           'face_labels', 'topology_fingerprint')
_VIEW = ('scene_token', 'scene_identity', 'view', 'camera', 'selection', 'selected_names')
_MAX_STAGE_MM = 4.0
_ACTIVATION_LOGICAL_PX = 12.0
_SCHEMA = 'md-cloth-numeric-position-plan-v1'


def _require(value, message):
    if not value:
        raise ValueError(message)


def _scope(current_scene, exported, pattern_uuid, group_name):
    certificate = world_adapter.bind_export_labels(current_scene, exported, pattern_uuid, group_name)
    _require(exported.get('scene_before_after_exact') is True, 'Use a complete, settled world OBJ capture')
    group = mesh.extract_group(exported, group_name)
    supplied = cloth.make_mesh_snapshot(pattern_uuid, exported['scene_token'], group['positions_mm'], group['faces'])
    return certificate, group, supplied


def make_references(current_scene, exported, pattern_uuid, group_name, group_vertex_indices=None):
    """Issue compact OBJ-group references scoped to this owner, scene and pose."""
    _, _, snapshot = _scope(current_scene, exported, pattern_uuid, group_name)
    size = len(snapshot['positions_mm'])
    indices = list(range(size)) if group_vertex_indices is None else list(group_vertex_indices)
    _require(len(indices) == len(set(indices)) and all(type(i) is int and 0 <= i < size for i in indices),
             'Use unique, in-range compact OBJ group indices')
    if not indices:
        return []
    reference = cloth.vertex_reference(snapshot, indices[0])
    return [dict(reference, vertex_index=index) for index in indices]


def make_reference(current_scene, exported, pattern_uuid, group_name, group_vertex_index):
    return make_references(current_scene, exported, pattern_uuid, group_name, [group_vertex_index])[0]


def bind_position_target(current_scene, exported, target):
    """Re-resolve a typed position request from a fresh complete scene export."""
    _require(type(target) is dict and set(target) == {'vertex_ref', 'position_mm'}, 'Use one typed cloth position target')
    reference = target['vertex_ref']
    _require(type(reference) is dict and isinstance(reference.get('pattern_uuid'), str), 'Use an issued cloth vertex reference')
    pattern_uuid = reference['pattern_uuid']
    owners = [row for row in current_scene['patterns'] if row['pattern_uuid'] == pattern_uuid]
    _require(len(owners) == 1, 'The requested owner must resolve uniquely in the current scene')
    group_name = owners[0]['information']['name']
    certificate, group, snapshot = _scope(current_scene, exported, pattern_uuid, group_name)
    index = cloth.resolve_snapshot_vertex(snapshot, reference)
    global_index = group['group_index_maps']['positions_mm']['compact_to_export'][index]
    incident = [face for face, attrs in zip(exported['faces'], exported['face_labels'], strict=True)
                if global_index in face and attrs['groups'] == [group_name]]
    _require(incident and all(attrs['groups'] == [group_name]
             for face, attrs in zip(exported['faces'], exported['face_labels'], strict=True) if global_index in face),
             'This exported vertex belongs exclusively to the current cloth group')
    source = list(exported['positions_mm'][global_index])
    destination = cloth._vector(target['position_mm'], 3, 'absolute world position_mm')
    return {'pattern_uuid': pattern_uuid, 'group_name': group_name, 'vertex_ref': dict(reference),
            'group_vertex_index': index, 'export_vertex_index': global_index, 'source_xyz_mm': source,
            'requested_target_xyz_mm': destination, 'incident_faces': incident,
            'label_certificate': certificate, 'source_capture_id': exported['capture_id'],
            'source_snapshot_id': exported['snapshot_id'], 'source_obj_sha256': exported['export']['obj_asset']['sha256'],
            'source_world_arrays_sha256': digest({k: exported[k] for k in _ARRAYS})}


def _selection(exported, bound, report, before_png, selected_png):
    a, b = (report[k] for k in ('selection_3d_before', 'selection_3d_after'))
    marker, plan, dispatch = (report[k] for k in ('selection_readback', 'selection_plan', 'selection_dispatch'))
    owner, target = bound['pattern_uuid'], bound['export_vertex_index']
    mesh_summary = report['mesh_before']
    _require(report['entry_verified'] is True and report['mode_during']['mode'] == 'MeshBox'
             and report['mode_during']['world_coordinate_checked'] is True
             and marker['verified'] is True and type(marker['new_blue_pixels']) is int and marker['new_blue_pixels'] >= 1
             and (marker['pattern_uuid'], marker['export_vertex_index']) == (owner, target)
             and (plan['target_pattern_uuid'], plan['target_vertex_index']) == (owner, target)
             and marker['before_sha256'] == report['meshbox_frame']['image']['sha256']
             and marker['after_sha256'] == report['meshbox_after_selection']['image']['sha256']
             and dispatch.get('status') == 'EVENTS_DISPATCHED' and dispatch.get('requires_recovery') is False
             and dispatch.get('paired_release_verified') is True and dispatch.get('dispatch_sequence_completed') is True
             and type(dispatch.get('viewport_mouse_sendEvent_calls')) is int and dispatch['viewport_mouse_sendEvent_calls'] == 4,
             'Require one closed MeshBox selection and a current blue marker bound to this OBJ row')
    _require(mesh_summary['arrays_sha256'] == bound['source_world_arrays_sha256']
             and mesh_summary['topology_fingerprint'] == exported['topology_fingerprint']
             and mesh_summary['obj_asset']['sha256'] == bound['source_obj_sha256'],
             'Selection was established on a different complete world OBJ')
    selected, = a['selection']
    _require(all(a[key] == b[key] for key in _VIEW)
             and a['scene_token'] == report['scene_token'] == exported['scene_token']
             and a['scene_identity'] == report['scene_identity'] == exported['scene_identity']
             and a['selected_names'] == [bound['group_name']] and selected['selected'] is True
             and selected['pattern_uuid'] == owner and selected['name'] == bound['group_name']
             and selected['index'] == bound['label_certificate']['target']['SDK_pattern_index']
             and a['image']['sha256'] != b['image']['sha256'],
             'Require a paired 3D frame from the same selected owner, camera and viewport')
    before, after = _frame_bytes(a, before_png), _frame_bytes(b, selected_png)
    camera = b['camera']
    _require(sha256(b64decode(camera['cpt_base64'], validate=True)).hexdigest() == camera['cpt_sha256'],
             'The current camera parameter bytes changed')
    return a, b, before, after, camera_pose_from_matrix(camera['matrix'])


def _tolerance(value):
    value = cloth._number(value, 'tolerance_mm')
    _require(0 < value <= 0.002, 'The world-mm component tolerance must be in (0, 0.002]')
    return value


def plan_position_write(current_scene, exported, target, selection_report, before_png, selected_png, *,
                        focal_seed_px, tolerance_mm=0.002, second_camera=None):
    """Plan one stage from same-camera paired MeshBox captures and an absolute target."""
    from .dispatch import _module

    tolerance = _tolerance(tolerance_mm)
    bound = bind_position_target(current_scene, exported, target)
    a, b, before, after, pose = _selection(exported, bound, selection_report, before_png, selected_png)
    owner, index = bound['pattern_uuid'], bound['export_vertex_index']
    certificate = bound['label_certificate']
    groups = {row['pattern_uuid']: row['group_name'] for row in certificate['pairs']}
    pieces = _inventory(exported, groups, tuple(certificate['additional_unclassified_groups']))
    _require(index in pieces[owner].uv, 'The visible vertex lies outside the selected cloth inventory')
    dpr = b['image']['device_pixel_ratio']
    marker, calibration, visual = _module('cloth_meshbox_zoom_observation').bind_current_green_vertex(
        pose, before, after, exported, pieces, index, focal_seed_px, dpr)
    _require(visual['visual_binding_verified'] is True, 'Current green core does not uniquely resolve the requested OBJ vertex')

    start_world, goal_world = bound['source_xyz_mm'], bound['requested_target_xyz_mm']
    delta = [b - a for a, b in zip(start_world, goal_world, strict=True)]
    next_pose = None if second_camera is None else _validated_pose(second_camera)
    axes = tuple(pose[key] for key in ('right', 'up', 'forward'))
    stage = _plane_delta(delta, axes, None if next_pose is None else next_pose['forward'])
    stage_goal = [a + b for a, b in zip(start_world, stage, strict=True)]
    _require(math.hypot(*stage) <= _MAX_STAGE_MM, 'This bounded path allows at most 4 mm in one camera plane')
    stage_required = math.hypot(*stage) > 1e-9
    points = None
    activation_world = None
    relative_delta = [0.0, 0.0]
    if stage_required:
        camera = calibration['camera']
        start_projection = visual['target_projected_px']
        end_projection = project_world(camera, stage_goal)
        relative_delta = [(q - p) / dpr for p, q in zip(start_projection, end_projection, strict=True)]
        core = min(marker['deepest_core_pixel_centers'], key=lambda point: (math.dist(point, start_projection), point))
        x, y = (value / dpr for value in core)
        points = {'start': [x, y], 'activation': [x - _ACTIVATION_LOGICAL_PX, y],
                  'end': [x + relative_delta[0], y + relative_delta[1]]}
        width, height = b['image']['viewport_size']
        threshold = b['view']['mapping']['start_drag_distance']
        _require(type(threshold) is int and 0 <= threshold < _ACTIVATION_LOGICAL_PX
                 and all(8 <= x <= width - 9 and 8 <= y <= height - 9 for x, y in points.values()),
                 'The activation threshold and all relative drag points must fit the live viewport')
        offset = [a - b for a, b in zip(start_world, pose['camera_center_world_mm'], strict=True)]
        depth = -cloth._dot(offset, pose['forward'])
        step = _ACTIVATION_LOGICAL_PX * dpr * depth / camera['focal_x_px']
        activation_world = [coordinate - step * axis for coordinate, axis in zip(start_world, pose['right'], strict=True)]
        _require(_orientation_safe(exported['positions_mm'], bound['incident_faces'], index, [activation_world, stage_goal]),
                 'The activation and refinement would flatten or reverse an incident triangle')
    result = {'schema': _SCHEMA, **{k: v for k, v in bound.items() if k not in ('incident_faces', 'label_certificate')},
              'scene_token': exported['scene_token'], 'scene_identity': exported['scene_identity'],
              'view_ref': b['view']['view_ref'], 'view_fingerprint': b['view']['view_fingerprint'],
              'mapping_fingerprint': b['view']['mapping_fingerprint'], 'device_pixel_ratio': dpr,
              'viewport_size': b['image']['viewport_size'], 'before_frame_sha256': a['image']['sha256'],
              'selected_frame_sha256': b['image']['sha256'],
              'camera_cmt_sha256': b['camera']['cmt_sha256'], 'camera_cpt_sha256': b['camera']['cpt_sha256'],
              'camera_pose': pose, 'camera_registration': {key: calibration[key] for key in
                   ('camera', 'fit', 'holdout', 'fit_edge_ids', 'holdout_edge_ids', 'target_uncertainty_px', 'registration_basis')},
              'second_camera_forward': None if next_pose is None else next_pose['forward'],
              'viewport_points': points, 'relative_delta_logical_px': relative_delta,
              'stage_required': stage_required, 'stage_delta_xyz_mm': stage, 'stage_target_xyz_mm': stage_goal,
              'requested_delta_xyz_mm': delta, 'activation_world_model_mm': activation_world, 'tolerance_mm': tolerance,
              'selection_event_batch_closed': True, 'visual_binding': visual, 'marker': marker,
              'incident_triangle_orientation_path_verified': stage_required,
              'fresh_camera_required_for_next_stage': next_pose is not None,
              'fresh_selected_frame_required_before_dispatch': True,
              'native_vertex_mapping_verified': False, 'native_execution_verified': False, 'dispatch_qualified': False}
    return dict(result, plan_sha256=digest(result))


def verify_position_write(current_scene, before_export, after_export, plan):
    """Compare distinct full OBJ captures after one dispatch, preserving all other data."""
    _check_plan(plan)
    target = {'vertex_ref': plan['vertex_ref'], 'position_mm': plan['requested_target_xyz_mm']}
    bound = bind_position_target(current_scene, before_export, target)
    _require(all(plan[key] == bound[key] for key in ('pattern_uuid', 'group_name', 'export_vertex_index',
                  'source_capture_id', 'source_snapshot_id', 'source_obj_sha256', 'source_world_arrays_sha256')),
             'The before capture differs from the exact OBJ used to plan')
    owner, index = bound['pattern_uuid'], bound['export_vertex_index']
    group = bound['group_name']
    world_adapter.bind_export_labels(current_scene, after_export, owner, group)
    _require(after_export.get('scene_before_after_exact') is True
             and before_export['capture_id'] != after_export['capture_id']
             and before_export['export']['options'] == after_export['export']['options'],
             'A new independent full OBJ with the same export options is required')
    before, after = before_export, after_export
    exact_keys = ('topology_fingerprint', 'uv', 'faces', 'face_uv_indices', 'face_normal_indices',
                  'face_labels', 'material_libraries')
    exact = all(before[key] == after[key] for key in exact_keys)
    changes = [i for i, (a, b) in enumerate(zip(before['positions_mm'], after['positions_mm'], strict=True)) if a != b] if exact else []
    nearby = {i for face in bound['incident_faces'] for i in face}
    normals = {n for face, rows in zip(before['faces'], before['face_normal_indices'], strict=True)
               if nearby.intersection(face) for n in rows if n is not None}
    protected = {n for attrs, rows in zip(before['face_labels'], before['face_normal_indices'], strict=True)
                 if attrs['groups'] != [group] for n in rows if n is not None}
    normal_changes = [i for i, (a, b) in enumerate(zip(before['normals'], after['normals'], strict=True)) if a != b] if exact else []
    normals_preserved = exact and all(i in normals - protected and abs(math.hypot(*after['normals'][i]) - 1) <= 0.0001
                                     for i in normal_changes)
    measured = after['positions_mm'][index] if exact else None
    errors = [abs(Fraction(str(q)) - Fraction(str(p))) for p, q in zip(plan['stage_target_xyz_mm'], measured, strict=True)] if exact else None
    stage_error = None if errors is None else float(max(errors))
    residual = None if measured is None else [float(Fraction(str(q)) - Fraction(str(p))) for p, q in
                                              zip(measured, plan['requested_target_xyz_mm'], strict=True)]
    tolerance = _tolerance(plan['tolerance_mm'])
    expected_change = exact and changes == ([index] if plan['stage_required'] else [])
    verified = expected_change and normals_preserved and stage_error is not None and stage_error <= tolerance
    return {'verified': verified, 'pattern_uuid': owner, 'export_vertex_index': index,
            'before_capture_id': before['capture_id'], 'after_capture_id': after['capture_id'],
            'before_obj_sha256': before['export']['obj_asset']['sha256'], 'after_obj_sha256': after['export']['obj_asset']['sha256'],
            'before_xyz_mm': before['positions_mm'][index], 'actual_xyz_mm': measured,
            'actual_delta_xyz_mm': None if measured is None else [q - p for p, q in
                                                                  zip(before['positions_mm'][index], measured, strict=True)],
            'maximum_stage_component_error_mm': stage_error, 'remaining_delta_xyz_mm': residual,
            'absolute_target_reached': verified and max(map(abs, residual)) <= tolerance, 'tolerance_mm': tolerance,
            'topology_uv_labels_preserved': exact, 'other_exported_xyz_exact': exact and all(i == index for i in changes),
            'changed_export_vertex_count': len(changes) if exact else None, 'changed_export_indices': changes[:32],
            'local_normal_recomputation_only': normals_preserved, 'changed_normal_indices': normal_changes[:32],
            'native_vertex_mapping_verified': False, 'solver_rest_write_verified': False,
            'persistence_verified': False}


def _check_plan(plan):
    _require(plan.get('schema') == _SCHEMA and plan.get('plan_sha256') == digest({k: v for k, v in plan.items() if k != 'plan_sha256'}),
             'The bound projection plan changed')


def verify_fresh_selected_frame(plan, frame, png_bytes):
    """Require the current green selection to remain unchanged before native dispatch."""
    _check_plan(plan)
    _frame_bytes(frame, png_bytes)
    image, view, camera = frame['image'], frame['view'], frame['camera']
    _require(frame['scene_token'] == plan['scene_token'] and frame['scene_identity'] == plan['scene_identity']
             and frame['selected_names'] == [plan['group_name']]
             and len(frame['selection']) == 1 and frame['selection'][0].get('selected') is True
             and frame['selection'][0].get('pattern_uuid') == plan['pattern_uuid']
             and frame['selection'][0].get('name') == plan['group_name']
             and all(view[key] == plan[key] for key in ('view_ref', 'view_fingerprint', 'mapping_fingerprint'))
             and all(image[key] == plan[key] for key in ('device_pixel_ratio', 'viewport_size'))
             and image['sha256'] == plan['selected_frame_sha256']
             and camera['cmt_sha256'] == plan['camera_cmt_sha256']
             and camera['cpt_sha256'] == plan['camera_cpt_sha256']
             and camera_pose_from_matrix(camera['matrix']) == plan['camera_pose'],
             'The current green selection, viewport, camera or scene changed before dispatch')
    return {'verified': True, 'scene_token': plan['scene_token'], 'selected_frame_sha256': image['sha256'],
            'view_ref': view['view_ref'], 'view_fingerprint': view['view_fingerprint']}
