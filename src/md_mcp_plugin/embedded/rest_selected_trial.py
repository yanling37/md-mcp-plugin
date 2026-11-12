"""Dispatch one fixed Selected Rest button in the current validated scene."""

import ctypes

from . import rest_scope, selection, world_adapter, world_frame
from .common import check_scene_token, digest
from .rest_adapter import CAPACITY, _base, _bridge_state, _install_bridge, _sha


STATE_KEY = '_md_mcp_plugin_rest_selected_trial_state'
DLL_NAME = 'md_qt_rest_selected_trial_bridge.dll'
SCHEMA = 'md-qt-rest-selected-trial/1'
_ACTIONS = {'update': 0, 'reset': 1}
_CONTROL_TEXTS = ('Update Rest Shape', 'Reset Rest Shape', 'Selected Patterns', 'All Patterns')
_CONTROL_ANCESTRY = [['MVGroupFrame', 'MVGroupFrame'], ['CloUICommon::Frame', 'subFrameRestShape'], ['CloUICommon::Frame', '']]
_ZERO_COUNTS = ('OS_input_calls', 'focus_calls', 'simulation_calls', 'show_raise_activate_calls', 'private_slot_invoke_calls',
                'QAction_trigger_calls', 'property_write_calls')


def _state():
    return _bridge_state(STATE_KEY)


def install_native_bridge(path, sha256):
    return _install_bridge(_state(), DLL_NAME, path, sha256)


def _load(environment):
    state = _state()
    config = state['configuration']
    if not config or _sha(config['path']) != config['sha256']:
        raise ValueError('Configure the unchanged pinned Selected Rest trial DLL')
    if state['library'] is None:
        library = ctypes.CDLL(config['path'], winmode=0x900)
        abi = library.mdqt_rest_selected_trial_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise ValueError('Selected Rest trial requires C ABI1')
        call = library.mdqt_rest_selected_trial_click
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(environment['process']))
    if state['process'] != environment['process']:
        raise ValueError('Selected Rest trial DLL belongs to another MD lifetime')
    return state['call']


def selected_owner(current, pattern_uuid, *, allow_empty=False):
    target, name = world_adapter._target(current, pattern_uuid)
    observed = selection.inspect_selection({})
    expected = {'pattern_uuid': pattern_uuid, 'index': target['index'], 'name': name, 'selected': True}
    selected = observed['selected']
    if observed['scene_token'] == current['scene_token']:
        if selected == [expected] and observed['selected_names'] == [name]:
            return selected
        if allow_empty and selected == [] and observed['selected_names'] == []:
            return selected
    raise ValueError('The SDK selection must belong exclusively to the requested current pattern')


def current_mesh(current):
    snapshot = world_adapter._mesh_read()
    if snapshot['scene_identity'] != current['identity'] or snapshot['scene_token'] != current['scene_token']:
        raise ValueError('The complete current OBJ belongs to another scene')
    return snapshot


def _validate_control_scope(scope):
    if (scope['simulation_ui_checked'] is not False or scope['selected_scope_checked'] is not True
            or scope['all_scope_checked'] is not False or scope['main_window_minimized'] is not True):
        raise ValueError('Selected, stopped and minimized Rest control scope required')
    owner, host = scope['owner_path'][-2:]
    if ((owner['class_name'], owner['object_name'], host['class_name'], host['object_name']) != (
            'MDMainWindow', 'CLO', 'Marvelous::DialogOverrideSettings', 'DialogOverrideSettings')
            or host['enabled'] is not True or host['visible'] is not False):
        raise ValueError('Hidden Rest host is not owned by the current MD main window')
    for index, (row, text) in enumerate(zip(scope['controls'], _CONTROL_TEXTS, strict=True)):
        kind = 'CloUICommon::TextButton' if index < 2 else 'CloUICommon::RadioButton'
        if (row['class_name'] != kind or row['object_name'] != '' or row['standard_control'] != 'QAbstractButton'
                or row['text'] != text or row['path_from_host'] != [*_CONTROL_ANCESTRY, [kind, '']]
                or row['text_truncated'] is not False or row['enabled'] is not True or row['visible'] is not False
                or row['checkable'] is not (index >= 2) or row['checked'] is not (index == 2)):
            raise ValueError('Actual Rest button roles, paths or Selected scope differ')


def _validate_native(raw, code, process, action):
    if (type(raw) is not dict or raw.get('schema') != SCHEMA or type(raw.get('return_code')) is not int
            or raw['return_code'] != code or code not in (0, 1, 2) or raw['action'] != action
            or type(raw['process_id']) is not int or raw['process_id'] != process['pid']
            or raw['process_creation_filetime'] != str(process['creation_filetime'])
            or type(raw['button_click_calls']) is not int or raw['button_click_calls'] != int(code != 1)
            or raw['effects_started'] is not (code != 1) or raw['requires_recovery'] is not (code == 2)
            or raw['automatic_retry_allowed'] is not False or raw['solver_rest_xyz_verified'] is not False
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in _ZERO_COUNTS)
            or raw['status'] != ('DISPATCH_CLOSED', 'REJECTED', 'UNKNOWN')[code]
            or raw['dispatch_closed'] is not (code == 0) or raw['physical_endpoints_verified'] is not (code == 0)):
        raise ValueError('Selected Rest native receipt did not verify its fixed identity and effect boundary')
    if code == 0:
        if raw['click_returned'] is not True or raw['before'] != raw['after'] or raw['physical_before'] != raw['physical_after']:
            raise ValueError('Selected Rest native click or endpoint readback did not close')
        physical = raw['physical_before']
        if (physical['capture'] != '0' or physical['cursor_valid'] is not True
                or type(physical['active_key_or_button_count']) is not int or physical['active_key_or_button_count'] != 0
                or any(physical[key] is not False for key in ('modal_present', 'popup_present', 'mouse_grabber_present',
                                                              'keyboard_grabber_present'))):
            raise ValueError('Selected Rest native physical endpoint was not idle')
        _validate_control_scope(raw['before'])
    elif code == 1 and raw['click_returned'] is not False:
        raise ValueError('Rejected Selected Rest result claims it dispatched')


def _capture_summary(snapshot):
    return {'capture_id': snapshot['capture_id'], 'obj_asset': snapshot['export']['obj_asset'],
            'world_arrays_sha256': digest({key: snapshot[key] for key in world_frame.MESH_FIELDS})}


def trial(params, *, expected_current, pattern_uuid):
    result = {'button_click_calls': 0, 'transaction_closed': False, 'current_obj_unchanged': False,
              'solver_rest_xyz_verified': False, 'automatic_retry_allowed': False}
    attempted = False
    try:
        if type(params) is not dict or set(params) != {'scene_token', 'action'} or params['action'] not in _ACTIONS:
            raise ValueError('A current token and fixed update/reset action are required')
        token, action = params['scene_token'], params['action']
        state = _state()
        if state.get('blocked'):
            raise ValueError('An uncertain Selected Rest trial is still held')
        current = check_scene_token(token)
        if current != expected_current or current['identity']['md_version'] != rest_scope._BUILD:
            raise ValueError('The current Rest project or qualified MD build changed')
        world_frame._require_stopped_simulation(token)
        scope = rest_scope._read_scope(current)
        before = current_mesh(current)
        _, name = world_adapter._target(current, pattern_uuid)
        world_adapter.bind_export_labels(current, before, pattern_uuid, name)
        if check_scene_token(token) != current or rest_scope._read_scope(current) != scope:
            raise ValueError('The scene or background viewport changed before the Rest action')
        environment = _base._environment()
        process = environment['process']
        if any(current['identity'][key] != process[key] for key in ('pid', 'creation_filetime')):
            raise ValueError('The Selected Rest trial process identity changed')
        call = _load(environment)
        output = ctypes.create_string_buffer(CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        selected = selected_owner(current, pattern_uuid)
        result.update(action=action, scene_token=token, scene_identity=current['identity'], selected_pattern_uuid=pattern_uuid,
                      sdk_selection_before=selected, before=_capture_summary(before))
        attempted = True
        result['button_click_calls'] = None
        code = call(process['pid'], process['creation_filetime'], _ACTIONS[action], 1500, output, len(output),
                    ctypes.byref(written), ctypes.byref(needed))
        if code in (-1, -2, -3, -4) and not written.value and not needed.value:
            attempted = False
            result['button_click_calls'] = 0
            raise ValueError('Native Selected Rest preflight rejected before dispatch')
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise ValueError('Native Selected Rest receipt is missing or out of bounds')
        raw = _base._json(output.raw[:written.value].decode('utf8'))
        result['native'] = raw
        _validate_native(raw, code, process, action)
        attempted = raw['effects_started']
        result['button_click_calls'] = raw['button_click_calls']
        if code != 0:
            raise ValueError(raw.get('reason') or 'Native Selected Rest dispatch did not close')
        world_frame._require_stopped_simulation(token)
        after = current_mesh(current)
        result['after'] = _capture_summary(after)
        selected_after = selected_owner(current, pattern_uuid)
        if (check_scene_token(token) != current or rest_scope._read_scope(current) != scope or selected_after != selected
                or before['capture_id'] == after['capture_id']
                or any(before[key] != after[key] for key in world_frame.MESH_FIELDS)):
            raise ValueError('Selected Rest button changed scene, scope, selected owner or the current complete OBJ')
        result.update(verified=True, transaction_closed=True, current_obj_unchanged=True, sdk_selection_after=selected)
        return {'status': 'SUCCEEDED', 'result': result, 'error': None, 'effects_started': True, 'requires_recovery': False}
    except Exception as exc:
        if attempted:
            _state()['blocked'] = True
        return {'status': 'UNKNOWN' if attempted else 'REJECTED', 'result': dict(result, verified=False),
                'error': {'code': 'REST_SELECTED_TRIAL_UNVERIFIED', 'message': str(exc)[:500]},
                'effects_started': attempted, 'requires_recovery': attempted}


