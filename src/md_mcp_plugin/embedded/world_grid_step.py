"""Fixed Qt bridge for the 3D Grid Properties step field."""
import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from .common import check_scene_token, digest, process_identity, scene
from . import mesh, selection, transforms, world_adapter, world_frame

SCHEMA = 'md-qt-world-grid-step/1'
PACKAGE_SCHEMA = 'md-mcp-packaged-world-grid-step/1'
LIBRARY = 'md_qt_world_grid_step.dll'
STATE_KEY = '_md_mcp_plugin_world_grid_step_state'
CAPACITY = 1048576
STATUS_CODES = {'SUCCEEDED': 0, 'REJECTED': 1, 'PARTIAL': 2, 'UNKNOWN': 3}
VALUE_EVENT_COUNTS = ('step_set_value_calls', 'return_press_calls', 'return_release_calls')
AXIS_OPERATION = 'world_grid_step.translate_axis'
RECOVERY_OPERATION = 'world_grid_step.recover_axis'
POSITION_PROFILE = 'position_with_obj_normal_quantization'
_RESTORE_TOKEN = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_OBJ_NORMAL_COMPONENT = re.compile(rb'[+-]?[0-9]+\.[0-9]{6}')
def _state():
    if not hasattr(sys, STATE_KEY):
        setattr(sys, STATE_KEY, {'library': None, 'busy': False, 'blocked': False})
    return getattr(sys, STATE_KEY)


def _load(identity):
    directory = Path(__file__).resolve().parents[1] / 'native' / 'world_grid_step'
    path = directory / 'manifest.json'
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding='utf8'))
    pin = manifest.get('sha256')
    if (manifest.get('schema') != PACKAGE_SCHEMA or manifest.get('library') != LIBRARY
            or manifest.get('architecture') != 'x64' or manifest.get('qt_version') != '6.10.3'
            or type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None
            or manifest.get('revision') != pin):
        raise ValueError('Invalid packaged Grid Properties bridge manifest')
    binary = directory / pin / LIBRARY
    if binary.is_symlink() or not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != pin:
        raise ValueError('Grid Properties bridge bytes do not match the packaged manifest')
    state = _state()
    if state['library'] is not None:
        if state['process'] != identity:
            raise ValueError('The loaded Grid Properties bridge belongs to another MD process')
        if state['pin'] != pin:
            if state['blocked'] or state.get('restore_token') or state.get('axis_lease'):
                raise ValueError('The active Grid Properties bridge cannot change with an unresolved write or lease')
            # Keep the closed old native image mapped until MD exits.
            state.setdefault('retired_libraries', []).append(state['library'])
            state.update(library=None, call=None)
    if state['library'] is None:
        library = ctypes.CDLL(str(binary), winmode=0x900)
        version = library.mdqt_world_grid_step_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 1:
            raise ValueError('Grid Properties bridge ABI 1 required')
        call = library.mdqt_world_grid_step_json
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, pin=pin, process=identity)
    return state['call']


def _mm(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _validate(raw, code, request, identity):
    status = raw.get('status')
    operation = request['operation']
    read_only = operation in ('inspect', 'probe_lease')
    if (raw.get('schema') != SCHEMA or raw.get('operation') != request['operation']
            or type(raw.get('return_code')) is not int or raw['return_code'] != code
            or STATUS_CODES.get(status) != code
            or type(raw.get('process_id')) is not int or raw['process_id'] != identity['pid']
            or raw.get('process_creation_filetime') != str(identity['creation_filetime'])
            or any(type(raw.get(k)) is not bool for k in ('verified', 'effects_started', 'requires_recovery', 'restore_required'))):
        raise ValueError('Grid Properties native receipt does not match this request')
    counts = [raw.get(key) for key in VALUE_EVENT_COUNTS]
    if any(type(count) is not int or count not in (0, 1) for count in counts) or read_only and any(counts):
        raise ValueError('Unexpected Grid Properties value input count')
    if any(type(raw.get(key)) is not int or raw[key] != 0 for key in ('OS_input_calls', 'focus_calls', 'event_pump_calls')):
        raise ValueError('Grid Properties bridge reported an unexpected system input or focus operation')
    if status != 'SUCCEEDED':
        if raw['verified'] or raw['requires_recovery'] is not (status == 'UNKNOWN'):
            raise ValueError('Grid Properties failure has contradictory status')
        if status == 'REJECTED' and raw['effects_started']:
            raise ValueError('A rejected Grid Properties request must have no effects')
        if status == 'PARTIAL' and (any(counts) or raw['restore_required'] or raw.get('original_page_restored') is not True):
            raise ValueError('Grid Properties page restoration is uncertain')
        return
    if (code != 0 or not raw['verified'] or raw['requires_recovery'] or raw.get('original_page_restored') is not True
            or raw.get('grid_widget_commit_verified') is not (not read_only)
            or raw.get('effective_step_verified') is not False or not _mm(raw.get('step_mm'))):
        raise ValueError('Grid Properties field or original page was not verified')
    if raw['restore_required'] is not (operation in ('set_temporary', 'probe_lease')):
        raise ValueError('Grid Properties restore state is inconsistent')
    if not read_only and (counts[1:] != [1, 1] or raw.get('return_pair_closed') is not True):
        raise ValueError('Grid Properties Return event pair did not close')
    if operation == 'set_temporary' and (raw['step_mm'] != request['step_mm'] or not _mm(raw.get('original_step_mm'))
            or type(raw.get('restore_token')) is not str or not _RESTORE_TOKEN.fullmatch(raw['restore_token'])):
        raise ValueError('Temporary Grid Properties value has no valid original-value reference')
    if operation == 'restore' and (raw.get('restore_token') != request['restore_token']
            or raw['step_mm'] != raw.get('original_step_mm')):
        raise ValueError('Restored Grid Properties value does not match its original reference')
    if operation == 'probe_lease' and (raw.get('restore_token') != request['restore_token']
            or raw.get('effects_started') is not False or raw.get('return_pair_closed') is not True
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in ('action_trigger_calls', 'page_restore_calls'))):
        raise ValueError('Grid lease observation differs or reported effects')


def _failure(code, message, *, unknown=False):
    return {'status': 'UNKNOWN' if unknown else 'REJECTED', 'result': {'verified': False},
            'error': {'code': code, 'message': message}, 'effects_started': unknown, 'requires_recovery': unknown}


def _invoke(request):
    state = _state()
    operation = request['operation']
    restore_token = state.get('restore_token')
    restore = operation == 'restore' and restore_token is not None and restore_token == request.get('restore_token')
    probe = operation == 'probe_lease' and not state['blocked'] and restore_token is not None and restore_token == request.get('restore_token')
    if state['busy'] or not (restore or probe) and (state['blocked'] or state.get('restore_token')):
        return _failure('GRID_STEP_BUSY_OR_UNRESOLVED', 'Finish or reconcile the previous Grid Properties operation')
    state['busy'] = True
    attempted = False
    try:
        identity = process_identity()
        call = _load(identity)
        if call is None:
            return {'status': 'UNSUPPORTED', 'result': {'verified': False}, 'effects_started': False,
                    'requires_recovery': False, 'error': {'code': 'GRID_STEP_BRIDGE_NOT_PACKAGED',
                                                          'message': 'The Grid Properties bridge is not available in this build'}}
        payload = json.dumps(request, ensure_ascii=True, allow_nan=False).encode('utf8')
        output = ctypes.create_string_buffer(CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        attempted = True
        code = call(identity['pid'], identity['creation_filetime'], payload, len(payload), output, CAPACITY,
                    ctypes.byref(written), ctypes.byref(needed))
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise ValueError('Grid Properties bridge returned an incomplete receipt')
        raw = json.loads(output.raw[:written.value].decode('utf8'))
        _validate(raw, code, request, identity)
        state['blocked'] = raw['requires_recovery']
        if operation == 'set_temporary' and raw['restore_required']:
            token = raw.get('restore_token')
            if type(token) is str and _RESTORE_TOKEN.fullmatch(token):
                state['restore_token'] = token
        if restore and raw['status'] == 'SUCCEEDED':
            state.pop('restore_token', None)
            state.pop('axis_lease', None)
        error = None if raw['status'] == 'SUCCEEDED' else {'code': raw.get('reason', 'GRID_STEP_NOT_VERIFIED'),
                                                        'message': 'See the native Grid Properties receipt'}
        status = 'NO_OP' if raw['status'] == 'PARTIAL' else raw['status']
        return {'status': status, 'result': raw, 'error': error,
                'effects_started': raw['effects_started'], 'requires_recovery': raw['requires_recovery']}
    except Exception as exc:
        state['blocked'] = attempted
        return _failure('GRID_STEP_RECEIPT_UNVERIFIED', str(exc), unknown=attempted)
    finally:
        state['busy'] = False


def inspect(params):
    if set(params) != {'scene_token'}:
        return _failure('INVALID_PARAMETERS', 'Read the current scene token')
    try:
        before = scene()
    except Exception as exc:
        return _failure('GRID_STEP_SCENE_NOT_VERIFIED', str(exc))
    if before['scene_token'] != params['scene_token']:
        return _failure('STALE_SCENE', 'Read the current scene token')
    reply = _invoke({'operation': 'inspect', 'budget_ms': 750})
    if reply['status'] not in ('SUCCEEDED', 'NO_OP'):
        return reply
    try:
        if scene() != before:
            raise ValueError('The active project or pattern metadata changed')
    except Exception as exc:
        if reply['effects_started']:
            _state()['blocked'] = True
        return _failure('GRID_STEP_SCENE_NOT_VERIFIED', str(exc), unknown=reply['effects_started'])
    reply['result']['scene_token'] = before['scene_token']
    return reply


def _current_scene(token):
    current = check_scene_token(token)
    if current['identity']['md_version'] != [2026, 1, 237]:
        raise ValueError('Grid Properties requires the reviewed MD 2026.1.237 build')
    return current


def _current_target_scope(current, target, group):
    snapshot = world_adapter._mesh_read()
    labels = world_adapter.bind_export_labels(current, snapshot, target, group)
    arrays_sha = digest({key: snapshot[key] for key in world_frame.MESH_FIELDS})
    extra_groups = set(labels['additional_unclassified_groups'])
    assignments = []
    if extra_groups:
        reply = world_adapter.inspect_topstitch_assignments({'scene_token': current['scene_token']})
        result = reply.get('result') or {}
        rows = result.get('pattern_assignments') or []
        selected = [row for row in rows if row['pattern_uuid'] == target and row['pattern_name'] == group]
        assignments = result.get('exact_sdk_to_obj_label_matches') or []
        observed = {row['exported_group'] for row in assignments}
        if (reply.get('status') != 'SUCCEEDED' or result.get('verified') is not True
                or result.get('scene_identity') != current['identity'] or result.get('scene_token') != current['scene_token']
                or result.get('sdk_assignment_record_layout_verified') is not True
                or len(selected) != 1 or selected[0]['assigned_count'] != 0 or selected[0]['instances'] != []
                or observed != extra_groups or len(assignments) != len(observed)
                or any(row['pattern_uuid'] == target for row in assignments)):
            raise ValueError('All extra OBJ groups must bind to protected assignments on other panels')
        asset = result['mesh']['export']['obj_asset']
        payload, actual = mesh._asset(Path(asset['path']))
        arrays = mesh.parse_obj(payload)
        if actual != asset or digest({key: arrays[key] for key in world_frame.MESH_FIELDS}) != arrays_sha:
            raise ValueError('The current assignment OBJ differs from the pre-move capture')
    return {'pattern_uuid': target, 'group_name': group,
            'protected_groups': sorted(set(snapshot['group_inventory']['groups']) - {group}),
            'protected_topstitch_assignments': assignments,
            'world_arrays_sha256': arrays_sha}


def _normal_quanta(payload, indices):
    rows = [fields[1:] for line in payload.splitlines()
            if (fields := line.split(b'#', 1)[0].split())[:1] == [b'vn']]
    result = []
    for index in indices:
        values = rows[index]
        if len(values) != 3 or not all(_OBJ_NORMAL_COMPONENT.fullmatch(value) for value in values):
            raise ValueError('Target OBJ normals must have exactly six decimal places per component')
        result.append(tuple(int(value.replace(b'.', b'')) for value in values))
    return result


def compare_obj_normal_quantization(before_asset, after_asset, group):
    """Check two retained OBJ assets for a bounded target-only normal difference."""
    payloads = []
    for asset in (before_asset, after_asset):
        payload, actual = mesh._asset(Path(asset['path']))
        if actual != asset:
            raise ValueError('Retained OBJ bytes differ from the movement receipt')
        payloads.append(payload)
    before, after = (mesh.parse_obj(payload) for payload in payloads)
    fixed = ('faces', 'face_uv_indices', 'face_normal_indices', 'face_labels', 'uv')
    if any(before[key] != after[key] for key in fixed) or len(before['normals']) != len(after['normals']):
        raise ValueError('Retained OBJ topology, normal indices, UVs or labels changed')
    target = mesh.extract_group(before, group)
    maps = target['group_index_maps']
    indices = maps['normals']['compact_to_export']
    if not indices or any(index is None for face in target['face_normal_indices'] for index in face):
        raise ValueError('Every target face corner must have a retained normal index')
    target_faces = set(maps['faces']['compact_to_export'])
    protected = set(range(len(before['normals']))) - set(indices)
    protected.update(index for i, face in enumerate(before['face_normal_indices']) if i not in target_faces
                     for index in face if index is not None)
    if any(before['normals'][index] != after['normals'][index] for index in protected):
        raise ValueError('An OBJ normal outside the target changed')
    first, last = (_normal_quanta(payload, indices) for payload in payloads)
    deltas = [tuple(abs(a - b) for a, b in zip(x, y, strict=True)) for x, y in zip(first, last, strict=True)]
    maxima = [max(values) for values in zip(*deltas, strict=True)]
    return {'verified': max(maxima) <= 1, 'decimal_places': 6, 'quantum': 1e-6,
            'target_normal_count': len(indices), 'changed_target_normal_count': sum(map(any, deltas)),
            'maximum_component_delta_quanta': maxima,
            'maximum_normal_l2_error': max(math.dist(before['normals'][i], after['normals'][i]) for i in indices),
            'before_obj_sha256': before_asset['sha256'], 'after_obj_sha256': after_asset['sha256'],
            'native_exact_normals_verified': False}


def _position_readback_verified(movement, observed, proof, matrix, current, target, group):
    measured = proof.get('observed') or {}
    captures = [observed.get(key) or {} for key in ('before_mesh', 'after_mesh')]
    tolerance = proof.get('tolerance_mm')
    error = proof.get('maximum_vertex_error_mm')
    return ((movement or {}).get('status') == 'SUCCEEDED' and movement.get('effects_started') is True
            and movement.get('requires_recovery') is False and observed.get('verified') is True
            and observed.get('primitive_dispatch_verified') is True and proof.get('matrix_world_mm') == matrix
            and (observed.get('selection') or {}).get('pattern_uuid') == target and measured.get('group_name') == group
            and all(row.get('scene_identity') == current['identity'] and row.get('scene_token') == current['scene_token']
                    and type(row.get('capture_id')) is str for row in captures)
            and captures[0]['capture_id'] != captures[1]['capture_id']
            and all(proof.get(key) is True for key in ('same_scene', 'distinct_captures', 'stable_label_bijection',
                                                        'uv_readback_complete', 'normal_readback_complete',
                                                        'outside_target_export_arrays_exact'))
            and all(measured.get(key) is True for key in ('topology_and_index_maps_exact', 'uv_exact',
                                                          'other_export_groups_all_exact'))
            and type(tolerance) in (int, float) and 0 < tolerance <= .002
            and type(error) in (int, float) and 0 <= error <= tolerance
            and _mm(proof.get('expected_maximum_motion_mm')) and proof['expected_maximum_motion_mm'] > 2 * tolerance
            and _mm(proof.get('observed_maximum_motion_mm')) and proof['observed_maximum_motion_mm'] > tolerance)


def _bound_native_reply(reply, operation, saved):
    raw = (reply or {}).get('result') or {}
    identity = saved['scene_identity']
    return (raw.get('schema') == SCHEMA and raw.get('operation') == operation
            and raw.get('restore_token') == saved['restore_token']
            and raw.get('process_id') == identity['pid']
            and raw.get('process_creation_filetime') == str(identity['creation_filetime'])
            and raw.get('original_step_mm') == saved['original_step_mm']
            and raw.get('original_page_restored') is True and raw.get('return_pair_closed') is True)


def _restore_safe(reply, operation, saved):
    return (_bound_native_reply(reply, operation, saved)
            and reply['result'].get('restore_required') is True
            and _state().get('restore_token') == saved['restore_token'])


def _restore_guard_rejected(reply, saved, held_token):
    """Recognize the native idle guard that runs before inspecting the Grid page."""
    if type(reply) is not dict or type(reply.get('result')) is not dict or type(reply.get('error')) is not dict:
        return False
    raw = reply['result']
    identity = saved['scene_identity']
    guard = 'MINIMIZED_BACKGROUND_GRID_SCOPE_REQUIRED'
    zero_calls = VALUE_EVENT_COUNTS + ('action_trigger_calls', 'page_restore_calls',
                                       'OS_input_calls', 'focus_calls', 'event_pump_calls')
    return (type(held_token) is str and _RESTORE_TOKEN.fullmatch(held_token) is not None
            and held_token == saved['restore_token']
            and reply.get('status') == raw.get('status') == 'REJECTED'
            and reply['error'].get('code') == raw.get('reason') == guard
            and reply.get('effects_started') is raw.get('effects_started') is False
            and reply.get('requires_recovery') is raw.get('requires_recovery') is False
            and raw.get('schema') == SCHEMA and raw.get('operation') == 'restore'
            and type(raw.get('return_code')) is int and raw['return_code'] == STATUS_CODES['REJECTED']
            and type(raw.get('process_id')) is int and raw['process_id'] == identity['pid']
            and raw.get('process_creation_filetime') == str(identity['creation_filetime'])
            and raw.get('restore_required') is True and raw.get('return_pair_closed') is True
            and all(raw.get(key) is False for key in ('verified', 'original_page_restored',
                                                      'grid_widget_commit_verified', 'effective_step_verified'))
            and all(raw.get(key) == {} for key in ('original_stack', 'opened_stack'))
            and all(key in raw and raw[key] is None for key in ('restore_token', 'original_step_mm', 'step_mm', 'step_2d_mm'))
            and all(type(raw.get(key)) is int and raw[key] == 0 for key in zero_calls))


def _axis_restore_retry_safe(reply, saved, temporary):
    if _restore_safe(reply, 'restore', saved):
        return True
    # This guard has no echoed token or page. Bind it to the still-held token
    # and the original verified temporary write that created the native lease.
    state = _state()
    raw = (temporary or {}).get('result') or {}
    return (state.get('axis_lease') is saved and state.get('blocked') is False
            and temporary is not None and temporary.get('status') == raw.get('status') == 'SUCCEEDED'
            and temporary.get('effects_started') is raw.get('effects_started') is True
            and temporary.get('requires_recovery') is raw.get('requires_recovery') is False
            and raw.get('verified') is raw.get('grid_widget_commit_verified') is True
            and _mm(raw.get('step_mm')) and _mm(saved['original_step_mm'])
            and _restore_safe(temporary, 'set_temporary', saved)
            and _restore_guard_rejected(reply, saved, state.get('restore_token')))


def _restore(saved, current, temporary):
    try:
        if _current_scene(saved['scene_token']) != current:
            saved['safe_to_retry'] = False
            return _failure('GRID_RESTORE_SCENE_CHANGED', 'The original Grid scene is required', unknown=True), False
        restored = _invoke({'operation': 'restore', 'restore_token': saved['restore_token'], 'budget_ms': 750})
        scene_preserved = _current_scene(saved['scene_token']) == current
        saved['safe_to_retry'] = scene_preserved and _axis_restore_retry_safe(restored, saved, temporary)
        raw = restored.get('result') or {}
        verified = (restored['status'] == 'SUCCEEDED' and _bound_native_reply(restored, 'restore', saved)
                    and raw.get('verified') is True and raw.get('grid_widget_commit_verified') is True
                    and raw.get('restore_required') is False and raw.get('step_mm') == saved['original_step_mm']
                    and _state().get('restore_token') is None and scene_preserved)
        return restored, verified
    except Exception as exc:
        saved['safe_to_retry'] = False
        return _failure('GRID_RESTORE_NOT_VERIFIED', str(exc), unknown=True), False


def translate_axis(params):
    """Move the selected pattern on an admitted sealed Grid calibration copy."""
    try:
        required = {'scene_token', 'pattern_uuid', 'axis', 'distance_mm'}
        if not required <= params.keys() <= required | {'verification_profile'}:
            raise ValueError('Use a current scene token, pattern UUID, axis and distance')
        profile = params.get('verification_profile', 'strict')
        if profile not in ('strict', POSITION_PROFILE):
            raise ValueError('Use the strict or position verification profile')
        token = params['scene_token']
        target, axis, signed = params['pattern_uuid'], params['axis'], params['distance_mm']
        if axis not in ('x', 'y', 'z') or type(signed) not in (int, float) or not .1 <= abs(signed) <= 100000:
            raise ValueError('Expected axis x, y or z and a signed distance of .1–100000 mm')
        distance = abs(signed)
        if round(distance, 1) != distance:
            raise ValueError('Grid Properties accepts one decimal place in mm')
        current = _current_scene(token)
        _, group = world_adapter._target(current, target)
        world_adapter._selection(current, target)
        from .dispatch import _module

        _module('native_setup').configure_world_adapter()
        view = 'left' if axis == 'z' else 'front'
        observed = world_adapter.read_camera({'scene_token': token})
        if observed['status'] != 'SUCCEEDED':
            return observed
        camera = observed['result']['camera']
        if camera['right_world'] != world_adapter.CANONICAL_VIEWPOINTS[view][1] or camera['up_world'] != [0, 1, 0]:
            return _failure('CAMERA_BASIS_CHANGED', 'Apply the canonical Front or Left view before this axis move')
        world_frame._require_stopped_simulation(token)
        target_scope = _current_target_scope(current, target, group)
        if scene() != current:
            return _failure('STALE_SCENE', 'The project changed while reading the camera')
        delta = [signed if axis == component else 0 for component in ('x', 'y', 'z')]
        matrix = transforms.translation_matrix(delta)
        direction = ('up' if signed > 0 else 'down') if axis == 'y' else ('right' if (signed > 0) == (axis == 'x') else 'left')
    except Exception as exc:
        return _failure(getattr(exc, 'code', 'GRID_AXIS_PREFLIGHT_REJECTED'), str(exc))

    temporary = _invoke({'operation': 'set_temporary', 'step_mm': distance, 'budget_ms': 750})
    if temporary['status'] in ('REJECTED', 'UNSUPPORTED') and temporary.get('effects_started') is False:
        return temporary
    raw = temporary.get('result') or {}
    original = raw.get('original_step_mm')
    restore_token = raw.get('restore_token')
    saved = {'scene_token': token, 'scene_identity': current['identity'], 'restore_token': restore_token,
             'original_step_mm': original, 'safe_to_retry': False,
             'payload': digest({'operation': AXIS_OPERATION, 'params': params, 'scene_token': token})}
    pending = (type(restore_token) is str and bool(_RESTORE_TOKEN.fullmatch(restore_token)) and _mm(original)
               and _restore_safe(temporary, 'set_temporary', saved))
    movement = None
    refresh = None
    restored, cleaned = None, False
    if pending:
        _state()['axis_lease'] = saved
        arrow_attempted = False
        try:
            if temporary['status'] == 'SUCCEEDED':
                try:
                    world_adapter._selection(current, target)
                except world_adapter.AdapterError as exc:
                    if exc.code != 'CURRENT_3D_SINGLETON_NOT_SELECTED':
                        raise
                    cleared = selection.inspect_selection({})
                    if cleared['scene_token'] != token or cleared['selected'] != [] or cleared['selected_names'] != []:
                        raise
                    probe_request = {'operation': 'probe_lease', 'restore_token': restore_token, 'budget_ms': 750}
                    refresh = {'before': _invoke(probe_request)}
                    first = refresh['before']
                    if (first['status'] != 'SUCCEEDED' or not _restore_safe(first, 'probe_lease', saved)
                            or first['result']['step_mm'] != distance or first['result']['step_2d_mm'] != raw['step_2d_mm']):
                        raise ValueError('Grid lease did not verify before selection refresh')
                    refreshed = world_adapter.select_pattern({'pattern_uuid': target, 'scene_token': token})
                    refresh['selection'] = refreshed
                    if refreshed['status'] != 'SUCCEEDED':
                        movement = refreshed
                    else:
                        refresh['after'] = _invoke(probe_request)
                        last = refresh['after']
                        if (last['status'] != 'SUCCEEDED' or not _restore_safe(last, 'probe_lease', saved)
                                or last['result']['step_mm'] != distance or last['result']['step_2d_mm'] != raw['step_2d_mm']):
                            raise ValueError('Grid lease did not verify after selection refresh')
                if movement is None:
                    inspected = world_adapter.inspect_3d({})
                    if inspected['status'] != 'SUCCEEDED':
                        movement = inspected
                    else:
                        frame = inspected['result']['after']
                        arrow_attempted = True
                        movement = world_adapter._execute('arrow_step', {
                            'pattern_uuid': target, 'group_name': group, 'scene_token': token, 'canonical_view': view,
                            'view_ref': frame['view_ref'], 'view_fingerprint': frame['view_fingerprint'],
                            'direction': direction, 'expected_matrix_world_mm': matrix,
                        }, expected_world_arrays_sha256=target_scope['world_arrays_sha256'])
        except Exception as exc:
            movement = _failure('GRID_AXIS_MOVE_NOT_VERIFIED', str(exc), unknown=arrow_attempted)
        finally:
            restored, cleaned = _restore(saved, current, temporary)

    observed = (movement or {}).get('result') or {}
    proof = observed.get('matrix_acceptance') or {}
    exact = ((movement or {}).get('status') == 'SUCCEEDED' and movement.get('requires_recovery') is False
             and proof.get('exported_geometry_matches_requested_matrix') is True and proof.get('matrix_world_mm') == matrix)
    position_verified = _position_readback_verified(movement, observed, proof, matrix, current, target, group)
    normal_quantization = None
    if profile == POSITION_PROFILE and position_verified:
        try:
            before, after = (observed[key]['export']['obj_asset'] for key in ('before_mesh', 'after_mesh'))
            normal_quantization = compare_obj_normal_quantization(before, after, group)
            if normal_quantization['target_normal_count'] != proof.get('target_normal_count'):
                raise ValueError('The retained normal count differs from the movement receipt')
        except Exception as exc:
            normal_quantization = {'verified': False, 'reason': str(exc), 'native_exact_normals_verified': False}
    quantum_verified = (normal_quantization or {}).get('verified') is True
    accepted = position_verified and quantum_verified if profile == POSITION_PROFILE else exact
    absent = movement is None or (movement.get('effects_started') is False and movement.get('requires_recovery') is False)
    mismatched = ((movement or {}).get('status') == 'SUCCEEDED' and movement.get('effects_started') is True
                  and movement.get('requires_recovery') is False and observed.get('verified') is True
                  and observed.get('primitive_dispatch_verified') is True and proof.get('matrix_world_mm') == matrix
                  and proof.get('exported_geometry_matches_requested_matrix') is False
                  and all(proof.get(key) is True for key in ('same_scene', 'distinct_captures', 'stable_label_bijection',
                                                            'uv_readback_complete', 'normal_readback_complete',
                                                            'outside_target_export_arrays_exact')))
    untouched = (temporary['status'] == 'NO_OP' and raw.get('original_page_restored') is True
                 and raw.get('return_pair_closed') is True and raw.get('restore_required') is False
                 and all(raw.get(key) == 0 for key in VALUE_EVENT_COUNTS))
    geometry_settled = exact or absent or mismatched
    closed = geometry_settled and (cleaned or untouched)
    status = ('SUCCEEDED' if accepted else 'FAILED' if exact or mismatched else 'NO_OP') if closed else 'PARTIAL' if cleaned else 'UNKNOWN'
    recovery_available = geometry_settled and not cleaned and pending and saved['safe_to_retry']
    result = {'verified': status == 'SUCCEEDED', 'scene_token': token, 'scene_identity': current['identity'],
              'requested_delta_mm': delta, 'temporary_step': temporary, 'movement': movement, 'restore': restored,
              'selection_refresh': refresh,
              'verification_profile': profile, 'requested_matrix_verified': exact,
              'requested_position_verified': exact or position_verified,
              'target_exported_normals_within_obj_quantum': quantum_verified, 'obj_normal_quantization': normal_quantization,
              'original_step_widget_restored': cleaned or untouched,
              'original_effective_step_verified': False, 'closed_grid_axis_transaction': closed,
              'grid_recovery_available': recovery_available, 'geometry_closed_or_absent': geometry_settled}
    result['target_scope'] = target_scope
    error = None if status == 'SUCCEEDED' else (movement or {}).get('error') or temporary.get('error') or {
        'code': ('GRID_AXIS_POSITION_PROFILE_NOT_VERIFIED' if profile == POSITION_PROFILE and (exact or mismatched)
                 else 'GRID_AXIS_MOVEMENT_MISMATCH' if mismatched else 'GRID_AXIS_TRANSACTION_NOT_VERIFIED'),
        'message': 'Read the step, movement and restore receipts before retrying',
    }
    return {'status': status, 'result': result, 'error': error, 'effects_started': True,
            'requires_recovery': not closed}


def recover_axis(params):
    """Retry only a closed, same-process native Grid lease from the retained parent receipt."""
    from . import dispatch

    try:
        if set(params) != {'scene_token', 'blocked_request_id'}:
            raise ValueError('Supply the original Grid request ID and current scene token')
        parent_id, token = params['blocked_request_id'], params['scene_token']
        if type(parent_id) is not str or not re.fullmatch(r'[a-f0-9]{32}', parent_id):
            raise ValueError('A 32-character Grid request ID is required')
        current = _current_scene(token)
        parent = dispatch._RECEIPTS.get(parent_id) or {}
        result = parent.get('result') or {}
        saved = _state().get('axis_lease')
        if (saved is None or not saved['safe_to_retry'] or saved['scene_token'] != token
                or current['identity'] != saved['scene_identity'] or result.get('scene_identity') != saved['scene_identity']
                or result.get('scene_token') != token or parent.get('operation_id') != parent_id
                or dispatch._PAYLOADS.get(parent_id) != saved['payload']
                or parent.get('status') not in ('UNKNOWN', 'PARTIAL') or parent.get('requires_recovery') is not True
                or result.get('grid_recovery_available') is not True or result.get('geometry_closed_or_absent') is not True
                or not _restore_safe(result.get('temporary_step'), 'set_temporary', saved)
                or not _axis_restore_retry_safe(result.get('restore'), saved, result.get('temporary_step'))):
            raise ValueError('A settled same-scene Grid parent with its exact safe native lease is required')
    except Exception as exc:
        return _failure(getattr(exc, 'code', 'GRID_RECOVERY_PARENT_NOT_VERIFIED'), str(exc))

    restored, verified = _restore(saved, current, result.get('temporary_step'))
    result = {'verified': verified, 'blocked_request_id': parent_id, 'scene_token': token,
              'scene_identity': current['identity'], 'grid_restore_verified': verified,
              'original_step_mm': saved['original_step_mm'],
              'restored_step_mm': (restored.get('result') or {}).get('step_mm'),
              'original_step_widget_restored': verified, 'original_effective_step_verified': False,
              'restore': restored}
    return {'status': 'SUCCEEDED' if verified else 'UNKNOWN', 'result': result,
            'error': None if verified else restored.get('error') or {'code': 'GRID_RESTORE_NOT_VERIFIED',
                                                                     'message': 'The native Grid restore did not verify'},
            'effects_started': restored.get('effects_started') is True, 'requires_recovery': not verified}


OPERATIONS = {'world_grid_step.inspect': inspect, AXIS_OPERATION: translate_axis, RECOVERY_OPERATION: recover_axis}
