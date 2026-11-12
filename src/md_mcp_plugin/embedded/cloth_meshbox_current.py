"""Plan an isolated MeshBox rectangle from a current 2D frame and whole world OBJ."""

from hashlib import sha256
from itertools import product
import math
from typing import Literal, TypedDict

from . import cloth, mesh, world_adapter
from .cloth_meshbox_uv_fit import MeshboxFitError, _inventory, fit_meshbox_frame
from .cloth_meshbox_outline import _corner, _no_old_blue
from .common import digest
from .curve_projection import decode_png


_PLAN_SCHEMA = 'md-current-meshbox-box-v1'
_REGISTRATION_SCHEMA = 'md-current-meshbox-owner-outline-v1'
_MAX_AUTO_PIXELS = 2_000_000
_MAX_AUTO_CONTOURS = 8
_MAX_AUTO_ASSIGNMENTS = 64
_FIT_CHECKS = ('fit_wire', 'held_out_wire', 'held_out_outline')
_MODE_ACTIONS = {'action_World_Coordinate_Gizmo', 'actionBox_Selecting_Tool'}


class OutlineLandmark(TypedDict):
    export_vertex_index: int
    point_physical_px: list[float]


class OwnerOutline(TypedDict):
    pattern_uuid: str
    outline: Literal['dark', 'yellow']
    landmarks: list[OutlineLandmark]


class OutlineRegistration(TypedDict):
    schema: Literal['md-current-meshbox-owner-outline-v1']
    scene_token: str
    scene_identity: dict
    frame_sha256: str
    mapping_fingerprint: str
    obj_sha256: str
    owners: list[OwnerOutline]


def _require(condition, code, message):
    if not condition:
        raise MeshboxFitError(code, message)


def _frame(current_scene, frame, png):
    image, view = frame['image'], frame['view']
    mapping = view['mapping']
    actions = {row['object_name'] for row in mapping['checked_actions'] if row['enabled'] is True}
    _require(frame['scene_token'] == current_scene['scene_token'] and frame.get('scene_identity') == current_scene['identity']
             and view['viewport']['object_name'] == 'widget2d' and view.get('scene_item_grabber_absent') is True
             and _MODE_ACTIONS <= actions and 'actionMove_Pattern' not in actions,
             'UNBOUND_MESHBOX_FRAME', 'Require the same current 2D widget in MeshBox/World with a closed input scope')
    _require(len(png) == image['byte_count'] and sha256(png).hexdigest() == image['sha256']
             and all(image[key] == view[key] for key in ('view_ref', 'view_fingerprint'))
             and all(image[key] == mapping[key] for key in ('viewport_size', 'device_pixel_ratio')),
             'FRAME_EVIDENCE_CHANGED', 'The current 2D PNG bytes, viewport or view reference changed')
    return image, view, mapping


def _source(current_scene, exported, ref, obj):
    owner = ref['pattern_uuid']
    rows = [row for row in current_scene['patterns'] if row['pattern_uuid'] == owner]
    _require(len(rows) == 1, 'UNKNOWN_OWNER', 'The issued vertex reference must name one current cloth owner')
    name = rows[0]['information']['name']
    certificate = world_adapter.bind_export_labels(current_scene, exported, owner, name)
    _require(exported.get('scene_before_after_exact') is True, 'UNSETTLED_WORLD_EXPORT', 'Use a settled whole scene OBJ')
    asset = exported['export']['obj_asset']
    _require(len(obj) == asset['byte_count'] and sha256(obj).hexdigest() == asset['sha256'],
             'OBJ_EVIDENCE_CHANGED', 'The whole scene OBJ bytes differ from the current capture')
    parsed = mesh.parse_obj(obj)
    _require(all(parsed[key] == exported[key] for key in parsed), 'OBJ_ARRAYS_CHANGED',
             'The captured whole scene arrays differ from the retained OBJ')
    group = mesh.extract_group(exported, name)
    group_snapshot = cloth.make_mesh_snapshot(owner, exported['scene_token'], group['positions_mm'], group['faces'])
    compact = cloth.resolve_snapshot_vertex(group_snapshot, ref)
    index = group['group_index_maps']['positions_mm']['compact_to_export'][compact]
    groups = {row['pattern_uuid']: row['group_name'] for row in certificate['pairs']}
    excluded = tuple(certificate['additional_unclassified_groups'])
    return parsed, groups, excluded, owner, index


def _seeds_from_registration(value, scene, image, view, obj_sha, groups):
    binding = {'schema': _REGISTRATION_SCHEMA, 'scene_token': scene['scene_token'], 'scene_identity': scene['identity'],
               'frame_sha256': image['sha256'], 'mapping_fingerprint': view['mapping_fingerprint'], 'obj_sha256': obj_sha}
    _require(type(value) is dict and set(value) == {*binding, 'owners'} and all(value[k] == v for k, v in binding.items())
             and isinstance(value['owners'], list), 'STALE_OUTLINE_REGISTRATION',
             'Bind independent owner-associated outline observations to this scene, mapping, PNG and OBJ')
    seeds = {}
    for item in value['owners']:
        _require(type(item) is dict and set(item) == {'pattern_uuid', 'outline', 'landmarks'}
                 and item['pattern_uuid'] in groups and item['pattern_uuid'] not in seeds
                 and item['outline'] in ('dark', 'yellow') and isinstance(item['landmarks'], list)
                 and 3 <= len(item['landmarks']) <= 24, 'INVALID_OUTLINE_REGISTRATION',
                 'Use at least three noncollinear boundary landmarks for each current selectable owner')
        points = {}
        for point in item['landmarks']:
            _require(type(point) is dict and set(point) == {'export_vertex_index', 'point_physical_px'}
                     and type(point['export_vertex_index']) is int and point['export_vertex_index'] not in points,
                     'INVALID_OUTLINE_LANDMARK', 'Use one resolved observation per exact exported boundary vertex')
            points[point['export_vertex_index']] = point['point_physical_px']
        seeds[item['pattern_uuid']] = {'outline': item['outline'], 'points': points}
    _require(seeds.keys() == groups.keys(), 'OUTLINE_REGISTRATION_REQUIRED',
             'Register all current selectable paper groups, including competitors')
    return seeds


def _outline_components(image, style):
    width, height, channels, pixels = (image[k] for k in ('width', 'height', 'channels', 'pixels'))
    _require(width * height <= _MAX_AUTO_PIXELS, 'OUTLINE_PIXEL_BUDGET', 'Automatic contour registration exceeds the raster budget')
    marked = set()
    for n in range(width * height):
        at = n * channels
        r, g, b = pixels[at:at + 3]
        if channels == 4 and pixels[at + 3] != 255:
            continue
        if (max(r, g, b) <= 139 if style == 'dark' else min(r, g) >= 192 and min(r, g) - b >= 57):
            marked.add((n % width, n // width))
    _require(len(marked) <= 80_000, 'OUTLINE_PIXEL_BUDGET', 'Too many pixels for an isolated contour')
    deltas = [(x, y) for x, y in product(range(-2, 3), repeat=2) if x or y]
    output = []
    while marked:
        pending = [marked.pop()]
        component = []
        while pending:
            x, y = pending.pop()
            component.append((x, y))
            for dx, dy in deltas:
                neighbour = (x + dx, y + dy)
                if neighbour in marked:
                    marked.remove(neighbour)
                    pending.append(neighbour)
        if len(component) < 80:
            continue
        xs, ys = zip(*component)
        bounds = [min(xs) + .5, min(ys) + .5, max(xs) + .5, max(ys) + .5]
        xsize, ysize = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if min(xsize, ysize) > 25 and len(component) / (xsize * ysize) <= .15:
            output.append((len(component), bounds))
    return sorted(output, reverse=True)


def _candidate_maps(bounds, lows, highs, allow_partial):
    spans = tuple(high - low for low, high in zip(lows, highs, strict=True))
    if min(spans) <= 0:
        return
    spans_px = tuple(bounds[axis + 2] - bounds[axis] for axis in (0, 1))
    scales = tuple(width / span for width, span in zip(spans_px, spans, strict=True))
    if max(scales) <= 1.05 * min(scales):
        yield scales, tuple(bounds[axis] - lows[axis] * scales[axis] for axis in (0, 1))
    scale = max(scales)
    if allow_partial and max(scale * span - width for span, width in zip(spans, spans_px, strict=True)) > 3:
        # An overlap can hide one extent of an otherwise visible dark outline.
        # Use the intact extent to seed scale and either endpoint to seed origin.
        for anchor in product((0, 1), repeat=2):
            yield (scale, scale), tuple(bounds[axis + 2 * side] - scale * (lows, highs)[side][axis]
                                        for axis, side in enumerate(anchor))


def _axis_seeds(image, piece, style, components, *, allow_partial=False):
    boundary = sorted({index for edge in piece.outline for index in edge})
    for component, (_, bounds) in enumerate(components):
        seen = []
        for axes, signs in product(((0, 1), (1, 0)), product((-1, 1), repeat=2)):
            rotated = {index: tuple(sign * uv[axis] for axis, sign in zip(axes, signs, strict=True))
                       for index, uv in piece.uv.items()}
            lows = tuple(min(rotated[i][axis] for i in boundary) for axis in (0, 1))
            highs = tuple(max(rotated[i][axis] for i in boundary) for axis in (0, 1))
            for scales, offsets in _candidate_maps(bounds, lows, highs, allow_partial):
                coarse = {i: tuple(offsets[axis] + scales[axis] * p[axis] for axis in (0, 1))
                          for i, p in rotated.items()}
                points = {i: point for i in boundary if (point := _corner(image, piece, coarse, i, style)) is not None}
                if not 3 <= len(points) <= 24:
                    continue
                if any(points.keys() == prior.keys()
                       and all(math.dist(point, prior[i]) < 1e-6 for i, point in points.items()) for prior in seen):
                    continue
                seen.append(points)
                yield (style, component), {'outline': style, 'points': points}


def _selected_owner(sdk_selection, scene, owner):
    rows = [row for row in scene['patterns'] if row['pattern_uuid'] == owner]
    _require(len(rows) == 1, 'OUTLINE_REGISTRATION_REQUIRED', 'The selected cloth owner must be unique in the current scene')
    row, = rows
    expected = {'pattern_uuid': owner, 'index': row['index'], 'name': row['information']['name'], 'selected': True}
    _require(isinstance(sdk_selection, dict) and sdk_selection.get('status') == 'OBSERVED'
             and sdk_selection.get('scene_token') == scene['scene_token'] and sdk_selection.get('names_match_flags') is True
             and sdk_selection.get('patterns') == [expected] and sdk_selection.get('selected_names') == [expected['name']],
             'OUTLINE_REGISTRATION_REQUIRED', 'Capture independent owner-bound SDK selection of the exact target pattern')
    return expected


def _multi_owner_seeds(pixels, pieces, owner):
    _require(len(pieces) <= _MAX_AUTO_CONTOURS, 'OUTLINE_REGISTRATION_REQUIRED',
             'Automatic association exceeds the current outline budget')
    contours = {style: _outline_components(pixels, style) for style in ('yellow', 'dark')}
    _require(len(contours['yellow']) == 1 and len(pieces) - 1 <= len(contours['dark']) <= _MAX_AUTO_CONTOURS,
             'OUTLINE_REGISTRATION_REQUIRED', 'Require one yellow target outline and separately visible dark competitor outlines')
    options = []
    for uuid, piece in pieces.items():
        style = 'yellow' if uuid == owner else 'dark'
        options.append(list(_axis_seeds(pixels, piece, style, contours[style], allow_partial=style == 'dark')))
    _require(0 < math.prod(map(len, options)) <= _MAX_AUTO_ASSIGNMENTS, 'OUTLINE_REGISTRATION_REQUIRED',
             'Current outline associations are missing or exceed the joint fitting budget')
    for assignment in product(*options):
        if len({component for component, _ in assignment}) == len(pieces):
            yield dict(zip(pieces, (seed for _, seed in assignment), strict=True))


def plan_current_meshbox_box(current_scene, exported, vertex_ref, frame, png_bytes, obj_bytes, *, registration=None,
                             sdk_selection=None):
    """Return the existing separated-box contract, with current asset and owner binding.

    Associate the sole SDK-selected owner with its yellow outline, or use
    independently supplied paper anchors. Verify every owner against disjoint
    fit and held-out wire and outline evidence from this exact frame.
    """
    image, view, mapping = _frame(current_scene, frame, png_bytes)
    parsed, groups, excluded, owner, index = _source(current_scene, exported, vertex_ref, obj_bytes)
    obj_sha = exported['export']['obj_asset']['sha256']
    pixels = decode_png(png_bytes)

    def fit(seeds, model_error_px=(.25, .25)):
        return fit_meshbox_frame(png_bytes, obj_bytes, png_sha256=image['sha256'], obj_sha256=obj_sha,
                                 group_by_uuid=groups, excluded_groups=excluded, seeds_by_uuid=seeds,
                                 viewport_size_logical_px=mapping['viewport_size'], device_pixel_ratio=mapping['device_pixel_ratio'],
                                 landmark_error_px=1.25, model_error_px=model_error_px)

    selected_owner = None
    if registration is not None:
        seeds = _seeds_from_registration(registration, current_scene, image, view, obj_sha, groups)
        fitted = fit(seeds)
        basis = 'independent_owner_outline_input_rechecked_against_current_pixels'
    elif len(groups) == 1:
        piece = _inventory(parsed, groups, excluded)[owner]
        qualified = []
        for style in ('yellow', 'dark'):
            for _, candidate in _axis_seeds(pixels, piece, style, _outline_components(pixels, style)[:4]):
                try:
                    # Allow coarse mesh chords to differ slightly from the drawn curved outline.
                    qualified.append(fit({owner: candidate}, model_error_px=(.75, .75)))
                except MeshboxFitError:
                    continue
        _require(len(qualified) == 1, 'OUTLINE_REGISTRATION_REQUIRED',
                 'The isolated 2D contour did not resolve uniquely; capture independent noncollinear paper anchors')
        fitted, = qualified
        basis = 'single_owner_axis_aligned_outline_and_held_out_wires'
    else:
        selected_owner = _selected_owner(sdk_selection, current_scene, owner)
        qualified = []
        pieces = _inventory(parsed, groups, excluded)
        for seeds in _multi_owner_seeds(pixels, pieces, owner):
            try:
                qualified.append(fit(seeds))
            except MeshboxFitError:
                continue
            if len(qualified) > 1:
                break
        _require(len(qualified) == 1, 'OUTLINE_REGISTRATION_REQUIRED',
                 'The SDK-selected yellow outline and dark competitors did not resolve to one held-out fit')
        fitted, = qualified
        basis = 'sdk_selected_yellow_outline_and_unique_held_out_competitors'

    _no_old_blue(pixels, fitted.diagnostics_by_piece)
    plan = fitted.plan_vertex(owner, index, start_drag_distance=mapping['start_drag_distance'])
    _require(plan['candidate_available'] is True, 'NO_ISOLATED_VERTEX_BOX',
             f'No rectangle encloses the entire target interval and excludes every competitor: {plan["reason"]}')
    result = {'schema': _PLAN_SCHEMA, 'scene_token': frame['scene_token'], 'scene_identity': frame['scene_identity'],
              'frame_sha256': image['sha256'], 'obj_sha256': obj_sha, 'snapshot_id': exported['snapshot_id'],
              'mapping_fingerprint': view['mapping_fingerprint'], 'view_ref': view['view_ref'],
              'view_fingerprint': view['view_fingerprint'], 'viewport_mapping': mapping,
              'target_vertex_ref': dict(vertex_ref), 'target_pattern_uuid': owner, 'target_export_vertex_index': index,
              'topology_fingerprint': exported['topology_fingerprint'], 'registration_basis': basis,
              'fit_summary': {uuid: {'model_error_px': row['model_error_px'], **{
                  k: {n: row[k][n] for n in ('count', 'max_normal_residual_px')} for k in _FIT_CHECKS}}
                  for uuid, row in fitted.diagnostics_by_piece.items()},
              'plan': plan, 'fresh_frame_required_before_dispatch': True, 'dispatch_authorized': False}
    if selected_owner is not None:
        result['sdk_selected_owner'] = selected_owner
    return dict(result, plan_sha256=digest(result))


def verify_fresh_box_frame(current_scene, candidate, frame, png_bytes, *, sdk_selection=None):
    """Verify the still-unselected pre-event frame, ignoring only the capture path."""
    image, view, mapping = _frame(current_scene, frame, png_bytes)
    _require(candidate.get('schema') == _PLAN_SCHEMA
             and candidate.get('plan_sha256') == digest({k: v for k, v in candidate.items() if k != 'plan_sha256'}),
             'BOX_PLAN_CHANGED', 'The planned projection or selector rectangle changed')
    _require(all(candidate[k] == frame[k] for k in ('scene_token', 'scene_identity'))
             and all(candidate[k] == view[k] for k in ('mapping_fingerprint', 'view_ref', 'view_fingerprint'))
             and candidate['viewport_mapping'] == mapping and candidate['frame_sha256'] == image['sha256'],
             'BOX_FRAME_DRIFTED', 'Capture and refit the current unselected 2D frame')
    if 'sdk_selected_owner' in candidate:
        _require(_selected_owner(sdk_selection, current_scene, candidate['target_pattern_uuid']) == candidate['sdk_selected_owner'],
                 'BOX_SELECTION_DRIFTED', 'The observed SDK selection differs from the owner used for outline association')
    return {'verified': True, 'frame_sha256': image['sha256'], 'scene_token': frame['scene_token'],
            'view_fingerprint': view['view_fingerprint'], 'plan': candidate['plan']}
