"""Bind the asynchronous native Rest controller to one saved scratch project."""
import ctypes
import hashlib
import json
from pathlib import Path
import re
import sys

from . import rest_visible_contents as snapshots
from .common import check_scene_token, process_identity


LIBRARY = 'md_qt_rest_cold_bridge.dll'
STATE_KEY = '_md_mcp_plugin_rest_cold_state_v1'
CAPACITY = 1024 * 1024


def _state():
    if not hasattr(sys, STATE_KEY):
        setattr(sys, STATE_KEY, {'requests': {}})
    return getattr(sys, STATE_KEY)


def _file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _load(process):
    directory = Path(__file__).resolve().parents[1] / 'native' / 'rest_cold'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    pin = manifest.get('sha256')
    if (type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None
            or manifest.get('schema') != 'md-mcp-packaged-rest-cold/1' or manifest.get('library') != LIBRARY
            or manifest.get('revision') != pin or manifest.get('architecture') != 'x64'
            or manifest.get('qt_version') != '6.10.3' or manifest.get('c_abi_version') != 1):
        raise ValueError('An audited Rest cold-start build is required')
    binary = directory / pin / LIBRARY
    if binary.is_symlink() or binary.resolve() != binary or _file_sha(binary) != pin:
        raise ValueError('Rest cold-start bridge bytes or path differ')

    state = _state()
    binding = (process, str(binary), pin)
    if 'library' in state:
        if state['binding'] != binding:
            raise ValueError('The loaded Rest cold-start bridge belongs to another build or process')
        return state['calls']
    library = ctypes.CDLL(str(binary), winmode=0x900)
    version = library.mdqt_rest_cold_abi_version
    version.argtypes, version.restype = [], ctypes.c_uint32
    if version() != 1:
        raise ValueError('Rest cold-start ABI 1 is required')
    calls = {'begin': library.mdqt_rest_cold_begin, 'poll': library.mdqt_rest_cold_poll}
    for call in calls.values():
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
    state.update(library=library, binding=binding, calls=calls)
    return calls


def _native(call, process):
    buffer = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = call(process['pid'], process['creation_filetime'], buffer, CAPACITY, ctypes.byref(written), ctypes.byref(needed))
    if code != 0 or not 0 < written.value < CAPACITY or needed.value != written.value:
        raise ValueError(f'Rest cold-start receipt is unavailable (ABI return {code})')
    raw = json.loads(buffer.raw[:written.value].decode('utf8'))
    if (type(raw) is not dict or raw.get('schema') != 'md-qt-rest-cold/1'
            or raw.get('process_id') != process['pid'] or raw.get('process_creation_filetime') != str(process['creation_filetime'])
            or raw.get('status') not in ('PENDING', 'INITIALIZED', 'REJECTED', 'UNKNOWN')
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in ('OS_input_calls', 'focus_calls', 'event_pump_calls'))
            or any(type(raw.get(key)) is not int or raw[key] not in (0, 1) for key in ('start_calls', 'stop_calls'))
            or raw['stop_calls'] > raw['start_calls'] or raw.get('effects_started') is not bool(raw['start_calls'])):
        raise ValueError('Rest cold-start receipt identity or action counters differ')
    return raw


def _report(status, *, raw=None, proof=None, reason=None, effects=False):
    return {
        'status': status, 'effects_started': effects, 'requires_recovery': status == 'UNKNOWN',
        'result': {'native': raw, 'scope': proof, 'verified': status == 'SUCCEEDED'},
        'error': {'code': 'REST_COLD_' + status, 'message': str(reason)[:500]} if reason else None,
    }


def _classify(raw, pending):
    status = raw['status']
    if status == 'PENDING' and raw['start_calls'] == 1 and raw.get('activation_fence_detached') is False:
        return _report('PENDING', raw=raw, effects=True)
    if status == 'REJECTED' and raw['start_calls'] == 0 and raw.get('activation_fence_detached') is True:
        return _report('REJECTED', raw=raw, reason=raw.get('reason') or 'Rest preflight refused')
    if (status == 'INITIALIZED' and raw.get('verified') is True and raw.get('requires_recovery') is False
            and raw['start_calls'] == raw['stop_calls'] == raw.get('host_count_after') == 1
            and raw.get('host_count_before') == raw.get('user_activations_observed') == 0
            and all(raw.get(key) is True for key in ('host_hidden_after', 'stopped_select_move_after',
                                                     'main_window_minimized_after', 'activation_fence_detached'))):
        try:
            current = check_scene_token(pending['scene_token'])
            same_project = (current['identity'] == pending['identity'] and _file_sha(pending['project']) == pending['sha'])
            proof = snapshots._preservation(pending['before'], snapshots._native_snapshot()) if same_project else {}
            if (same_project and proof['native_2d_geometry_unchanged_verified'] is True
                    and proof['runtime_records_unchanged_verified'] is True):
                proof.update(scene_token=pending['scene_token'], scratch_project_sha256=pending['sha'],
                             saved_project_unchanged=True, simulated_positions_may_have_changed=True)
                return _report('SUCCEEDED', raw=raw, proof=proof, effects=True)
        except Exception as error:
            return _report('UNKNOWN', raw=raw, reason=error, effects=True)
        return _report('UNKNOWN', raw=raw, reason='The original scratch project scope changed', effects=True)
    return _report('UNKNOWN', raw=raw, reason=raw.get('reason') or 'Rest settlement could not be verified', effects=True)


def begin(params, *, request_id):
    try:
        if set(params) != {'scene_token', 'scratch_project_sha256'}:
            raise ValueError('Supply the current scratch scene token and saved scratch project SHA-256')
        token, pin = params['scene_token'], params['scratch_project_sha256']
        if type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None:
            raise ValueError('A lowercase scratch project SHA-256 is required')
        current = check_scene_token(token)
        identity = current['identity']
        project = Path(identity['project_path'])
        if (not project.is_absolute() or project.suffix.lower() != '.zprj' or project.is_symlink()
                or not current['pattern_count'] or _file_sha(project) != pin):
            raise ValueError('Open the saved disposable .zprj copy with the supplied SHA-256')
        process = process_identity()
        calls = _load(process)
        pending = {'scene_token': token, 'identity': identity, 'process': process, 'project': project,
                   'sha': pin, 'before': snapshots._native_snapshot()}
    except Exception as error:
        return _report('REJECTED', reason=error)
    state = _state()
    if state.get('pending') is True:
        return _report('REJECTED', reason='An existing Rest initialization requires observation')
    state['requests'][request_id] = pending
    state['pending'] = True
    return _observe(calls['begin'], pending)


def _observe(call, pending):
    try:
        raw = _native(call, pending['process'])
        result = _classify(raw, pending)
        if result['status'] != 'PENDING' and raw.get('activation_fence_detached') is True:
            pending['terminal'] = result
            _state()['pending'] = False
        return result
    except Exception as error:
        return _report('UNKNOWN', reason=error, effects=True)


def poll(params):
    request_id = params.get('request_id')
    pending = _state()['requests'].get(request_id) if type(request_id) is str else None
    if pending is None:
        return _report('NO_OP', reason='This MD lifetime has no matching Rest initialization')
    if 'terminal' in pending:
        return pending['terminal']
    try:
        calls = _load(pending['process'])
    except Exception as error:
        return _report('UNKNOWN', reason=error, effects=True)
    return _observe(calls['poll'], pending)


OPERATIONS = {'rest_cold.begin': begin, 'rest_cold.poll': poll}
