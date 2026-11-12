"""Read-only exported-vertex observations and calibrated move plans.

N237 motivates the local green-handle test and measured two-plane sequence.
The public evidence describes projected/exported vertices. Private picker
state, solver indices, Rest and permission to dispatch remain unestablished.
"""
import hashlib
import math

from . import cloth, mesh, world_adapter
from .curve_projection import decode_png


class VertexMappingError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise VertexMappingError(code, message)


def _one_component(points):
    if not points:
        return False
    unseen = set(points)
    pending = [unseen.pop()]
    while pending:
        x, y = pending.pop()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbour = x+dx, y+dy
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    pending.append(neighbour)
    return not unseen


def verify_meshbox_vertex_marker(before_frame, after_frame, before_png, after_png, plan):
    """Verify one new blue marker in a prequalified 2D box on the same view."""
    code = 'MESHBOX_MARKER_SCOPE_MISMATCH'
    view = before_frame['view']
    before_info, after_info = before_frame['image'], after_frame['image']
    dpr = view['mapping']['device_pixel_ratio']
    size = view['mapping']['viewport_size']
    _require(type(dpr) in (int, float) and .5 <= dpr <= 8
             and plan.get('schema') == 'md-meshbox-projected-rectangle/1' and plan.get('candidate_available') is True
             and plan['device_pixel_ratio'] == dpr and plan['viewport_size_logical_px'] == size
             and plan['start_drag_distance'] == view['mapping']['start_drag_distance']
             and before_frame['scene_token'] == after_frame['scene_token'] and after_frame['view'] == view,
             code, 'Use one qualified box and two frames of the same current view and scene')
    images = []
    for info, png in ((before_info, before_png), (after_info, after_png)):
        _require(info['view_ref'] == view['view_ref'] and info['view_fingerprint'] == view['view_fingerprint']
                 and info['device_pixel_ratio'] == dpr and info['viewport_size'] == size
                 and info['byte_count'] == len(png) and info['sha256'] == hashlib.sha256(png).hexdigest(),
                 code, 'Each frame must be bound to its captured PNG bytes')
        image = decode_png(png)
        _require(image['channels'] == 4 and [image['width'], image['height']] == [info['width'], info['height']]
                 and all(abs(p - q*dpr) <= .5 for p, q in zip((image['width'], image['height']), size)),
                 code, 'Use RGBA frames with current physical viewport dimensions')
        images.append(image)

    def blue(r, g, b, alpha):
        return alpha == 255 and b >= 160 and b-r >= 70 and b-g >= 20

    def yellow(r, g, b, alpha):
        return alpha == 255 and r > 80 and g > 80 and r-b >= 35 and g-b >= 20

    lo_x, lo_y, hi_x, hi_y = plan['target_uncertainty_bounds_logical_px']
    start, end = plan['box_start_logical_px'], plan['box_end_logical_px']
    _require(0 <= start[0] < lo_x <= hi_x < end[0] < size[0]
             and 0 <= start[1] < lo_y <= hi_y < end[1] < size[1], code,
             'The complete target uncertainty must be inside a current viewport rectangle')
    before, after = (image['pixels'] for image in images)
    width = images[0]['width']
    new, new_count, removed, preexisting, outside = set(), 0, 0, 0, 0
    marker_limit = math.floor(81*dpr*dpr)
    green_changes, green_count, green_core, green_outside = set(), 0, 0, 0
    green_limit = math.floor(3000*dpr*dpr)
    # A raster pixel at the edge is local if its footprint intersects the local region.
    local_margin = 20 + .5 / dpr
    for i in range(0, len(before), 4):
        a, b = before[i:i+4], after[i:i+4]
        was, now = blue(*a), blue(*b)
        if a == b and not was:
            continue
        y, x = divmod(i//4, width)
        lx, ly = (x+.5)/dpr, (y+.5)/dpr
        if was and lo_x-5 <= lx <= hi_x+5 and lo_y-5 <= ly <= hi_y+5:
            preexisting += 1
        if now and not was:
            new_count += 1
            if new_count <= marker_limit:
                new.add((x, y))
        if was and not now:
            removed += 1
        local = lo_x-local_margin <= lx <= hi_x+local_margin and lo_y-local_margin <= ly <= hi_y+local_margin
        outside_layout = a != b and not local and not (was or now or yellow(*a) or yellow(*b))
        if outside_layout:
            outside += 1
        before_green, after_green = (rgba[1]-max(rgba[0], rgba[2]) for rgba in (a, b))
        if b[3] == 255 and b[1] >= 190 and after_green >= 30 and after_green - before_green >= 30:
            green_count += 1
            green_core += b == b'\x50\xca\x50\xff'
            green_outside += outside_layout
            if green_count <= green_limit:
                green_changes.add((x, y))

    marker = None
    if 3*dpr*dpr <= new_count <= marker_limit and _one_component(new):
        xs, ys = zip(*new)
        w, h = max(xs)-min(xs)+1, max(ys)-min(ys)+1
        center = [(sum(axis)/len(new)+.5)/dpr for axis in (xs, ys)]
        if (2*dpr <= min(w, h) <= max(w, h) <= 9*dpr
                and start[0] < center[0] < end[0] and start[1] < center[1] < end[1]
                and lo_x-1 <= center[0] <= hi_x+1 and lo_y-1 <= center[1] <= hi_y+1):
            marker = {'centroid_logical_px': center, 'bounds_physical_px': [min(xs), min(ys), max(xs), max(ys)]}
    overlay_pixels = 0
    if marker is not None and 0 < outside == green_outside and green_count <= green_limit and green_core * 2 >= green_count:
        # A selected vertex can reveal a green manipulation overlay around the blue marker.
        cx, cy = marker['centroid_logical_px']
        offsets = [((x+.5)/dpr-cx, (y+.5)/dpr-cy) for x, y in green_changes]
        surrounds_marker = all(any(0 < direction * delta[axis] <= 6 and abs(delta[1-axis]) <= 1/dpr for delta in offsets)
                               for axis in (0, 1) for direction in (-1, 1))
        if surrounds_marker and all(math.hypot(dx, dy) <= 42 for dx, dy in offsets) and _one_component(green_changes):
            overlay_pixels = outside
    unexpected = outside - overlay_pixels
    verified = marker is not None and preexisting == removed == unexpected == 0
    return {'schema': 'md-meshbox-2d-marker-readback/1', 'verified': verified,
            'pattern_uuid': plan['target_pattern_uuid'], 'export_vertex_index': plan['target_vertex_index'],
            'before_sha256': before_info['sha256'], 'after_sha256': after_info['sha256'],
            'new_blue_pixels': new_count, 'removed_blue_pixels': removed, 'preexisting_near_target': preexisting,
            'unexpected_layout_pixels': unexpected, 'selection_overlay_pixels_outside_local_margin': overlay_pixels,
            'marker': marker, 'native_vertex_identity_verified': False}


def _green_anchor(image, physical, dpr):
    width, height, channels = (image[k] for k in ('width', 'height', 'channels'))
    radius = 13*dpr
    cx, cy = physical
    _require(radius <= cx < width-radius and radius <= cy < height-radius,
             'PROJECTION_NEAR_VIEWPORT_EDGE', 'Projected vertex lacks a complete local image neighbourhood')
    masks = [set(), set(), set()]
    pixels = image['pixels']
    for y in range(math.floor(cy-radius), math.ceil(cy+radius)+1):
        for x in range(math.floor(cx-radius), math.ceil(cx+radius)+1):
            if (x-cx)**2+(y-cy)**2 > radius**2:
                continue
            offset = channels*(y*width+x)
            r, g, b = pixels[offset:offset+3]
            if channels == 4 and pixels[offset+3] != 255:
                continue
            excess = g-max(r, b)
            for mask, threshold in zip(masks, (32, 80, 110)):
                if excess >= threshold:
                    mask.add((x, y))
    _require(all(_one_component(mask) for mask in masks) and len(masks[0]) <= 350*dpr*dpr,
             'AMBIGUOUS_GREEN_HANDLE', 'Each green threshold needs one compact local component')
    # The masks are nested. Clearance in the strongest mask implies the same
    # clearance in the other two, and avoids repeated full ROI pair searches.
    core = masks[-1]
    xs, ys = zip(*core)
    outside = [(x, y) for y in range(min(ys)-1, max(ys)+2)
               for x in range(min(xs)-1, max(xs)+2) if (x, y) not in core]
    clearance = {p: min((p[0]-q[0])**2+(p[1]-q[1])**2 for q in outside) for p in core}
    best = max(clearance.values())
    _require(best >= (4*dpr)**2, 'GREEN_HANDLE_INTERIOR_UNQUALIFIED', 'No local four-logical-pixel handle clearance')
    anchor = min((p for p in core if clearance[p] == best), key=lambda p: (math.dist(p, physical), p))
    return [anchor[0]/dpr, anchor[1]/dpr], [len(mask) for mask in masks], math.sqrt(best)/dpr


def match_visible_vertex(current_scene, exported, pattern_uuid, group_name, group_vertex_index,
                         camera, png_bytes, *, device_pixel_ratio=1.):
    """Associate a visible local handle with a unique projected OBJ candidate.

    Supply the actual current world-mm export, an independently calibrated
    current camera and its frame. Pixel fit is evidence for a candidate only.
    """
    certificate = world_adapter.bind_export_labels(current_scene, exported, pattern_uuid, group_name)
    group = mesh.extract_group(exported, group_name)
    _require(type(group_vertex_index) is int and 0 <= group_vertex_index < group['vertex_count'],
             'INVALID_VERTEX_INDEX', 'Expected a current zero-based compact OBJ group index')
    export_index = group['group_index_maps']['positions_mm']['compact_to_export'][group_vertex_index]
    xyz = cloth._vector(group['positions_mm'][group_vertex_index], 3, 'source_xyz_mm')
    values = cloth._camera(camera)
    dpr = device_pixel_ratio
    _require(type(dpr) in (int, float) and .5 <= dpr <= 4,
             'INVALID_DEVICE_PIXEL_RATIO', 'Expected the same capture device pixel ratio in [.5,4]')
    image = decode_png(png_bytes)
    _require(all(abs(image[k]-n*dpr) <= .5 for k, n in zip(('width', 'height'), values[-1])),
             'CAMERA_IMAGE_SIZE_MISMATCH', 'Camera logical viewport and captured frame differ')
    projected = cloth._project(values, xyz)
    anchor, mask_counts, clearance = _green_anchor(image, [v*dpr for v in projected], dpr)
    paper_names = {row['group_name'] for row in certificate['pairs']}
    paper_indices = {index for face, label in zip(exported['faces'], exported['face_labels'])
                     if label['groups'][0] in paper_names for index in face}
    _require(len(paper_indices) <= 50000, 'VERTEX_PROJECTION_BUDGET', 'Projected cloth inventory exceeds 50000 vertices')
    nearest = None
    for index in paper_indices-{export_index}:
        candidate = cloth._vector(exported['positions_mm'][index], 3, 'scene_vertex_xyz_mm')
        depth = -cloth._dot([a-b for a, b in zip(candidate, values[3])], values[2])
        if depth <= 0:
            continue
        distance = math.dist(anchor, cloth._project(values, candidate))
        if nearest is None or (distance, index) < nearest:
            nearest = distance, index
    target_distance = math.dist(anchor, projected)
    _require(nearest is None or nearest[0] > target_distance+2.5,
             'AMBIGUOUS_PROJECTED_VERTEX', 'Another exported cloth vertex competes with the visible handle')
    return dict(schema='md-visible-OBJ-vertex-candidate-v1', visible_marker_to_OBJ_candidate_verified=True,
        scene_token=exported['scene_token'], scene_identity=exported['scene_identity'], capture_id=exported['capture_id'],
        snapshot_id=exported['snapshot_id'], group_name=group_name, pattern_uuid=pattern_uuid,
        label_certificate_id=certificate['certificate_id'], group_vertex_index=group_vertex_index,
        export_vertex_index=export_index, source_xyz_mm=xyz, projected_vertex_px=projected,
        click_anchor_px=anchor, anchor_distance_px=target_distance, joint_clearance_px=clearance,
        green_mask_counts=mask_counts, projected_cloth_vertex_count=len(paper_indices),
        nearest_other_export_vertex_index=None if nearest is None else nearest[1],
        nearest_other_distance_px=None if nearest is None else nearest[0],
        png_sha256=hashlib.sha256(png_bytes).hexdigest(), device_pixel_ratio=dpr,
        independent_camera_registration_verified=False, native_selected_vertex_verified=False,
        native_vertex_index_verified=False, native_hit_verified=False, dispatch_qualified=False)


def _plane_delta(delta, first, second_normal):
    if second_normal is None:
        _require(abs(cloth._dot(delta, first[2])) <= 1e-6, 'SECOND_CAMERA_REQUIRED',
                 'Remaining world displacement needs a second independently calibrated plane')
        return delta
    wanted = cloth._dot(delta, second_normal)
    if abs(wanted) <= 1e-6:
        return [0., 0., 0.]
    projection = cloth._dot(second_normal, first[2])
    direction = [b-projection*a for a, b in zip(first[2], second_normal)]
    magnitude2 = cloth._dot(direction, direction)
    _require(magnitude2 >= .01, 'CAMERA_PLANES_NEAR_PARALLEL', 'The first plane cannot stably span the second plane normal')
    return [v*wanted/magnitude2 for v in direction]


def _orientation_safe(positions, incident_faces, vertex_index, path):
    def cross(points):
        return cloth._cross([b-a for a, b in zip(points[0], points[1])],
                            [c-a for a, c in zip(points[0], points[2])])
    for face in incident_faces:
        if len(face) != 3:
            return False
        original = cross([positions[i] for i in face])
        magnitude2 = cloth._dot(original, original)
        if magnitude2 <= 1e-20:
            return False
        for point in path:
            updated = cross([point if i == vertex_index else positions[i] for i in face])
            if cloth._dot(original, updated) <= magnitude2*1e-6:
                return False
    return bool(incident_faces)


def plan_vertex_drag(current_scene, exported, pattern_uuid, group_name, group_vertex_index,
                     camera, png_bytes, target_xyz_mm, *, second_camera=None,
                     activation_delta_px=(-12., 0.), device_pixel_ratio=1.):
    """Compute this camera's stage toward an absolute world target.

    A supplied second camera's unit forward axis selects the minimal first-plane
    move that removes its inaccessible component. Replan from actual XYZ later.
    """
    observation = match_visible_vertex(current_scene, exported, pattern_uuid, group_name, group_vertex_index,
                                      camera, png_bytes, device_pixel_ratio=device_pixel_ratio)
    target = cloth._vector(target_xyz_mm, 3, 'target_xyz_mm')
    current = observation['source_xyz_mm']
    delta = [b-a for a, b in zip(current, target)]
    first = cloth._camera(camera)
    second_normal = None
    if second_camera is not None:
        _require(type(second_camera) is dict, 'INVALID_SECOND_CAMERA', 'Expected the second camera forward axis')
        second_normal = cloth._vector(second_camera.get('forward'), 3, 'second_camera.forward')
        _require(abs(math.hypot(*second_normal)-1.) <= 1e-6, 'INVALID_SECOND_CAMERA',
                 'The second camera forward axis must be unit length')
    stage = _plane_delta(delta, first, second_normal)
    if math.hypot(*stage) <= 1e-9:
        return dict(observation=observation, requested_target_xyz_mm=target, stage_required=False,
                    viewport_points=None, measured_readback_required=True, dispatch_qualified=False)
    _require(math.hypot(*stage) <= 4., 'WORLD_STAGE_EXCEEDS_BUDGET', 'This small-displacement plan is limited to 4 mm per stage')
    group = mesh.extract_group(exported, group_name)
    snapshot = cloth.make_mesh_snapshot(pattern_uuid, exported['scene_token'], group['positions_mm'], group['faces'])
    reference = cloth.vertex_reference(snapshot, group_vertex_index)
    plan = cloth.plan_projected_delta(snapshot, reference, stage, camera, observation['click_anchor_px'], activation_delta_px)
    _require(plan['math_plan_valid'], 'PROJECTED_PATH_UNQUALIFIED', 'Stage must lie in the calibrated plane and viewport')
    activation = cloth._vector(activation_delta_px, 2, 'activation_delta_px')
    depth = -cloth._dot([a-b for a, b in zip(current, first[3])], first[2])
    activation_world = [x+depth/first[5]*(activation[0]*r-activation[1]*u)
                        for x, r, u in zip(current, first[0], first[1])]
    incident = [face for face in group['faces'] if group_vertex_index in face]
    _require(_orientation_safe(group['positions_mm'], incident, group_vertex_index,
                               [activation_world, plan['target_xyz_mm']]),
             'INCIDENT_TRIANGLE_PATH_UNQUALIFIED', 'Projected activation or target would flatten or reverse an incident face')
    return dict(plan, observation=observation, requested_target_xyz_mm=target, stage_required=True,
                stage_delta_xyz_mm=stage, incident_triangle_orientation_path_verified=True,
                activation_world_model_mm=activation_world, measured_readback_required=True)


def measure_exported_vertex_effect(before, after, group_name, export_vertex_index, *, target_xyz_mm=None,
                                   tolerance_mm=.002):
    """Check one actual changed world-mm OBJ row against the full exported mesh.

    Accept mesh.parse_obj output or hashed mesh.read_scene snapshots. The latter
    also bind the unchanged explicit process/project identity. Capture and file
    provenance belong to the caller. Target normal recomputation is reported.
    """
    if any('schema' in item for item in (before, after)):
        for item in (before, after):
            mesh._validate_snapshot(item)
            _require(item['scope'] == 'scene_garment_OBJ_export' and item['space'] == 'world'
                     and item['unit'] == 'mm' and item['axis_order'] == ['X', 'Y', 'Z'],
                     'UNVERIFIED_WORLD_EXPORT', 'Expected complete world-mm scene OBJ snapshots')
        _require(before['scene_identity'] == after['scene_identity'], 'SCENE_IDENTITY_CHANGED', 'Project or process differs')
    group = mesh.extract_group(before, group_name)
    indices = group['group_index_maps']['positions_mm']['compact_to_export']
    _require(type(export_vertex_index) is int and export_vertex_index in indices,
             'VERTEX_OUTSIDE_EXPECTED_GROUP', 'Expected export row must belong to the exact target group')
    _require(all(label['groups'] == [group_name] for face, label in zip(before['faces'], before['face_labels'])
                 if export_vertex_index in face), 'SHARED_EXPORTED_VERTEX',
             'The target export row is shared with another group')
    invariant_keys = ('faces', 'face_uv_indices', 'face_normal_indices', 'face_labels', 'uv', 'material_libraries')
    comparable = all(before[k] == after[k] for k in invariant_keys) and len(before['positions_mm']) == len(after['positions_mm'])
    original = cloth._vector(before['positions_mm'][export_vertex_index], 3, 'original_xyz_mm')
    actual = cloth._vector(after['positions_mm'][export_vertex_index], 3, 'actual_xyz_mm') if comparable else None
    changed = [i for i, (a, b) in enumerate(zip(before['positions_mm'], after['positions_mm'])) if a != b] if comparable else []
    target = None if target_xyz_mm is None else cloth._vector(target_xyz_mm, 3, 'target_xyz_mm')
    tolerance = cloth._number(tolerance_mm, 'tolerance_mm')
    _require(0 < tolerance <= .002, 'INVALID_TOLERANCE', 'World XYZ tolerance must be in (0,.002] mm')
    error = math.dist(actual, target) if actual is not None and target is not None else None
    normal_lengths_match = len(before['normals']) == len(after['normals'])
    normal_changes = sum(a != b for a, b in zip(before['normals'], after['normals'])) if normal_lengths_match else None
    return dict(schema='md-exported-single-vertex-effect-v1', comparable_exported_topology_uv_and_labels=comparable,
        observed_unique_expected_OBJ_vertex_change=comparable and changed == [export_vertex_index],
        other_exported_xyz_exact=comparable and all(i == export_vertex_index for i in changed),
        expected_group_name=group_name, expected_export_vertex_index=export_vertex_index,
        original_xyz_mm=original, actual_xyz_mm=actual,
        actual_delta_xyz_mm=None if actual is None else [b-a for a, b in zip(original, actual)],
        target_error_mm=error, target_xyz_reached=error is not None and error <= tolerance,
        changed_export_vertex_count=len(changed) if comparable else None, changed_export_indices=changed[:32],
        normal_counts_exact=normal_lengths_match, changed_normal_count=normal_changes,
        native_selected_vertex_verified=False, native_vertex_index_verified=False,
        solver_rest_verified=False, full_scene_invariants_verified=False)
