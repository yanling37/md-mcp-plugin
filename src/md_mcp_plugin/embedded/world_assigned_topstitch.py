"""Bind and rebuild one current Texture Topstitch within an enclosing world operation."""

from importlib import import_module
import math

from . import mesh, rest_scope, selection, topstitch_offset_rebuild as offset, world_adapter, world_frame
from .common import check_scene_token


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read_assignments(current, api):
    rows = []
    for pattern in current['patterns']:
        index = pattern['index']
        count = api.GetPatternAssignedTopstitchCount(index)
        records = api.GetPatternAssignedTopstitch(index)
        _require(type(count) is int and 0 <= count <= 4096 and type(records) is list and len(records) <= 8192
                 and all(type(row) is dict and all(type(k) is type(v) is str for k, v in row.items()) for row in records),
                 'Current SDK Topstitch assignment layout is unavailable')
        rows.append({'pattern_uuid': pattern['pattern_uuid'], 'pattern_name': pattern['information']['name'],
                     'assigned_count': count, 'instances': records})
    return rows


def _read_style(api, pattern_index, assigned_index):
    style_index = api.GetPatternAssignedTopstitchStyleIndex(pattern_index, assigned_index)
    _require(type(style_index) is int and style_index >= 0, 'The assigned style index is unavailable')
    model_type = api.GetTopstitchStyleModelType(style_index)
    _require(type(model_type) is int and model_type == 1, 'The assigned Topstitch must be Texture')
    catalog = offset._sdk_read(api.GetTopstitchStyleList, rows=True).get('raw_value')
    assigned = offset._sdk_read(api.GetPatternAssignedTopstitchStyle, pattern_index, rows=True).get('raw_value')
    _require(catalog is not None and assigned is not None, 'The current SDK Topstitch style catalogs are unavailable')
    index_key, name_key = 'TopstitchStyleIndex', 'TopstitchStyleName'
    hits = [row for row in catalog if row.get(index_key) == str(style_index)]
    _require(len(hits) == 1 and set(hits[0]) == {index_key, name_key} and bool(hits[0][name_key]),
             'The assigned Topstitch style index is ambiguous')
    name = hits[0][name_key]
    expected = {name_key: name, 'TopstitchStyleCount': '1'}
    split = [{key: value} for key, value in expected.items()]
    _require(sum(row.get(name_key) == name for row in catalog) == 1 and assigned in ([expected], split, split[::-1]),
             'The assigned Topstitch style name/count does not match the current style catalog')
    return {'style_index': style_index, 'style_name': name, 'style_model_type': model_type}


def _group_indices(snapshot, label):
    group = mesh.extract_group(snapshot, label)
    indices = {key: set(group['group_index_maps'][key]['compact_to_export']) for key in ('positions_mm', 'normals')}
    _require(all(indices.values()) and world_adapter._complete_corners(group, 'normals', 'face_normal_indices')
             and world_adapter._complete_corners(group, 'uv', 'face_uv_indices'),
             'The assigned Topstitch OBJ group lacks complete positions, UVs or normals')
    for face, face_normals, labels in zip(snapshot['faces'], snapshot['face_normal_indices'], snapshot['face_labels'], strict=True):
        if labels['groups'] != [label]:
            _require(indices['positions_mm'].isdisjoint(face) and indices['normals'].isdisjoint(face_normals),
                     'The assigned Topstitch shares exported positions or normals with another group')
    return indices


def _read_binding(current, target, name, labels, api):
    pattern_uuid = target['pattern_uuid']
    rows = _read_assignments(current, api)
    count = rows[target['index']]['assigned_count']
    layout, matches = world_adapter.topstitch_label_matches(rows, labels)
    _require(layout, 'Current SDK Topstitch assignment rows do not agree with their counts')
    binding = None
    if count:
        owned = [row for row in matches if row['pattern_uuid'] == pattern_uuid]
        _require(count == len(owned) == 1, 'The target must have one uniquely named assigned Topstitch OBJ group')
        candidate = owned[0]
        style = _read_style(api, target['index'], candidate['assigned_index'])
        _require(_read_style(api, target['index'], candidate['assigned_index']) == style,
                 'The assigned Topstitch style changed during binding')
        binding = dict(candidate, pattern_index=target['index'], pattern_name=name, **style)
    _require(_read_assignments(current, api) == rows and check_scene_token(current['scene_token']) == current,
             'The current scene or SDK assignment changed during binding')
    return binding


def bind(current, pattern_uuid, snapshot, *, api=None):
    """Bind exactly one SDK assignment to its non-shared Texture Topstitch OBJ group.

    Return None only when the current SDK explicitly reports zero assignments for
    this pattern. The caller supplies a full current world-mm OBJ snapshot.
    """
    token = current['scene_token']
    _require(check_scene_token(token) == current, 'The current MD scene changed before assignment readback')
    target, name = world_adapter._target(current, pattern_uuid)
    world_adapter.bind_export_labels(current, snapshot, pattern_uuid, name)
    api = import_module('pattern_api') if api is None else api
    binding = _read_binding(current, target, name, snapshot['group_inventory']['groups'], api)
    if binding is not None:
        _group_indices(snapshot, binding['exported_group'])
    return binding


def _scope(current):
    token = current['scene_token']
    _require(check_scene_token(token) == current, 'The current project changed during the Topstitch rebuild')
    world_frame._require_stopped_simulation(token)
    selected = selection.inspect_selection({})
    _require(selected['scene_token'] == token, 'The current SDK selection belongs to another scene')
    return rest_scope._read_scope(current), selected


def _verify_geometry(before, after, allowed):
    _require(all(before[key] == after[key] for key in world_frame.MESH_FIELDS if key not in allowed),
             'The Topstitch offset changed full OBJ topology, UVs or labels')
    for key, indices in allowed.items():
        _require(len(before[key]) == len(after[key]) and all(
            first == last for i, (first, last) in enumerate(zip(before[key], after[key], strict=True)) if i not in indices),
            'The Topstitch offset changed geometry outside the assigned group')


def preflight_offset(binding, *, api=None):
    api = import_module('pattern_api') if api is None else api
    getter, setter = offset._methods(api)
    _require(getter is not None and setter is not None, 'Both typed SDK Topstitch offset methods are required')
    value = getter(binding['pattern_index'], binding['assigned_index'])
    _require(type(value) is float and math.isfinite(value) and -10 <= value <= 10
             and -10 <= value + offset.OFFSET_STEP <= 10, 'Offset outside the qualified round-trip range')
    return value


def offset_roundtrip(current, binding, before_snapshot, *, expected_original=None, api=None):
    """Apply one qualified temporary offset and restore the exact original SDK value.

    A closed successful result provides a fresh full OBJ for the enclosing rigid
    transform check. An unresolved setter or scope never returns that snapshot.
    """
    result = {'sdk_set_calls': 0, 'offset_restored': False, 'positive_control_verified': False,
              'transaction_closed': False}

    def reply(status, message=None, snapshot=None):
        code = 'TOPSTITCH_OFFSET_' + status
        return {'status': status, 'result': result, 'error': {'code': code, 'message': str(message)[:500]} if message else None,
                'effects_started': result['sdk_set_calls'] > 0, 'requires_recovery': status == 'UNKNOWN',
                'snapshot': snapshot}

    try:
        api = import_module('pattern_api') if api is None else api
        getter, setter = offset._methods(api)
        _require(getter is not None and setter is not None, 'Both typed SDK Topstitch offset methods are required')
        target_uuid = binding['pattern_uuid']
        target, name = world_adapter._target(current, target_uuid)
        scope = _scope(current)
        fresh = world_adapter._mesh_read()
        mesh._validate_snapshot(before_snapshot)
        _require(before_snapshot['scene_identity'] == current['identity']
                 and before_snapshot['scene_token'] == current['scene_token']
                 and before_snapshot['scope'] == 'scene_garment_OBJ_export'
                 and bind(current, target_uuid, fresh, api=api) == binding
                 and fresh['capture_id'] != before_snapshot['capture_id']
                 and offset._arrays_hash(fresh) == offset._arrays_hash(before_snapshot),
                 'The current Topstitch binding or complete OBJ changed before the rebuild')
        label = binding['exported_group']
        allowed = _group_indices(fresh, label)
        indexes = binding['pattern_index'], binding['assigned_index']

        def read_offset():
            labels = fresh['group_inventory']['groups']
            _require(_scope(current) == scope and _read_binding(current, target, name, labels, api) == binding,
                     'The idle viewport or current assigned Texture Topstitch changed')
            value = getter(*indexes)
            _require(type(value) is float and math.isfinite(value), 'The assigned offset did not return a finite SDK float')
            return value

        original = read_offset()
        _require(expected_original is None or original == expected_original,
                 'The assigned offset changed since the original rotation preflight')
        temporary = original + offset.OFFSET_STEP
        _require(-10 <= original <= 10 and -10 <= temporary <= 10, 'Offset outside the qualified round-trip range')
        result.update(assignment=dict(binding), before_capture_id=fresh['capture_id'], original_offset_sdk=original,
                      temporary_offset_requested_sdk=temporary)
    except Exception as error:
        return reply('REJECTED', error)

    def observe(previous_ids):
        observed = world_adapter._mesh_read()
        _require(observed['capture_id'] not in previous_ids and bind(current, target_uuid, observed, api=api) == binding,
                 'The fresh OBJ does not match the current assigned Texture Topstitch')
        _verify_geometry(fresh, observed, allowed)
        return observed

    first_error = None
    setter_uncertain = False
    capture_ids = {before_snapshot['capture_id'], fresh['capture_id']}
    try:
        result['sdk_set_calls'] = 1
        try:
            _require(setter(*indexes, temporary) is None, 'Temporary offset setter returned an unexpected value')
        except Exception:
            setter_uncertain = True
            raise
        value = read_offset()
        _require(math.isclose(value, temporary, rel_tol=0, abs_tol=1e-6), 'Temporary offset did not read back')
        observed = observe(capture_ids)
        capture_ids.add(observed['capture_id'])
        _require(read_offset() == value, 'The temporary offset changed during geometry readback')
        motion = [math.dist(fresh['positions_mm'][i], observed['positions_mm'][i]) for i in allowed['positions_mm']]
        result.update(temporary_capture_id=observed['capture_id'], temporary_offset_readback_sdk=value,
                      temporary_minimum_stitch_motion_mm=min(motion),
                      temporary_maximum_stitch_motion_mm=max(motion))
        result['positive_control_verified'] = .025 <= min(motion) <= max(motion) <= .25
        _require(result['positive_control_verified'], 'The assigned Topstitch did not show the qualified temporary offset motion')
    except Exception as error:
        first_error = error

    try:
        value = read_offset()
        if first_error is None:
            _require(value == result['temporary_offset_readback_sdk'], 'The known temporary offset changed before restoration')
        if value != original:
            _require(math.isclose(value, temporary, rel_tol=0, abs_tol=1e-6),
                     'The current offset is neither the original nor the known temporary value')
            result['sdk_set_calls'] += 1
            _require(setter(*indexes, original) is None, 'Restoration offset setter returned an unexpected value')
        _require(read_offset() == original, 'The exact original offset did not read back')
        settled = observe(capture_ids)
        _require(read_offset() == original, 'The restored offset changed during geometry readback')
        result.update(offset_restored=True, transaction_closed=not setter_uncertain,
                      restored_capture_id=settled['capture_id'], restored_world_arrays_sha256=offset._arrays_hash(settled),
                      assignment=binding)
    except Exception as error:
        return reply('UNKNOWN', error)
    if setter_uncertain:
        return reply('UNKNOWN', first_error)
    if first_error is not None:
        return reply('FAILED', first_error)
    return reply('SUCCEEDED', snapshot=settled)
