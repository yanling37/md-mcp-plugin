"""Current-3D typed Qt primitives plus actual OBJ-label readback.

No operation accepts arbitrary keys, modifiers, code, DLLs, HWNDs or pointers.
This adapter deliberately does not implement transforms.install_world_backend:
screen direction/gizmo pixels do not establish a world-axis/angle/pivot writer.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import ctypes
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from .common import digest, process_identity, scene
from . import mesh

SCHEMA = 'md-qt-world-adapter/1'
PREFIX = 'mdworld1:'
STATE_KEY = '_md_mcp_plugin_world_adapter_state'
CAPACITY = 1048576
MAX_LABEL_FACE_WORK = 5000000
GRID_ACTION_UPGRADE = (
    '338916aa5631f4864d690e0c2d09e465cf44087b739b80309416018510479bf7',
    '718edd85e10a6e8458ff0781e052a9aa17822c4dfd0b8e9432cc86470724d5de',
)
CANONICAL_VIEWPOINTS = {
    'front': (2, [1.0, 0.0, 0.0]),
    'left': (6, [0.0, 0.0, -1.0]),
}
CAMERA_PRESETS = {**CANONICAL_VIEWPOINTS, 'quarter': (3, [0.707107, 0.0, -0.707107])}
# Columns are serialized to six decimals in the N237 Quarter camera archive.
QUARTER_FORWARD = [0.707107, 0.0, 0.707107]
DIRECTIONS = {'left': 0x01000012, 'up': 0x01000013, 'right': 0x01000014, 'down': 0x01000015}
PURPOSES = ('translation_gizmo', 'rotation_ring')
ZERO_COUNTS = ('OS_input_calls', 'OS_cursor_setter_calls', 'activation_calls', 'focus_setter_calls',
               'window_restore_calls', 'global_capture_setter_calls', 'global_modifier_setter_calls',
               'event_pump_calls', 'QAction_trigger_calls', 'model_memory_access_calls',
               'project_open_import_save_calls', 'simulation_calls')


class AdapterError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _state():
    value = getattr(sys, STATE_KEY, None)
    if value is None:
        value = {'configuration': None, 'library': None, 'call': None, 'blocked': False}
        setattr(sys, STATE_KEY, value)
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _hex(value, name):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise AdapterError('INVALID_PARAMETERS', name + ' requires a SHA256 value')
    return value


def _params(params, required=(), optional=()):
    if type(params) is not dict or not set(required) <= params.keys() or params.keys() - set(required) - set(optional):
        raise AdapterError('INVALID_PARAMETERS', 'Unexpected/missing world-adapter parameters')


def _text(value, name):
    if type(value) is not str or not value or len(value) > 512 or value.strip() != value or any(ord(c) < 32 for c in value):
        raise AdapterError('INVALID_PARAMETERS', name + ' requires an exact nonblank string')
    return value


def _budget(value):
    if type(value) is not int or not 50 <= value <= 1500:
        raise AdapterError('INVALID_PARAMETERS', 'budget_ms must be an integer in [50,1500]')
    return value


def _xy(value):
    if type(value) is not list or len(value) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10000000 for v in value):
        raise AdapterError('INVALID_PARAMETERS', 'Finite nonnegative logical XY pairs required')
    return [float(v) for v in value]


def install_native_bridge(path, sha256):
    """Trusted core bootstrap only; does not load the DLL or install a writer."""
    _hex(sha256, 'sha256')
    candidate = Path(path)
    if (not candidate.is_absolute() or candidate.name != 'md_qt_world_adapter_bridge.dll'
            or candidate.is_symlink() or not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != sha256):
        raise AdapterError('INVALID_BRIDGE_CONFIGURATION', 'One reviewed pinned world-adapter DLL is required')
    state = _state(); configuration = {'path': str(candidate.resolve()), 'sha256': sha256}
    if state['blocked'] or state.get('in_call'):
        raise AdapterError('BRIDGE_ALREADY_LOADED', 'Cannot change an active or unresolved bridge')
    if state['library'] is not None and configuration != state['configuration']:
        if (state['configuration']['sha256'], sha256) != GRID_ACTION_UPGRADE:
            raise AdapterError('BRIDGE_ALREADY_LOADED', 'The active bridge revision cannot be replaced')
        # The inspection-only revision keeps the prior code mapped. Its own view
        # reference registry rejects refs issued by the previous revision.
        state.setdefault('retired_libraries', []).append(state['library'])
        state.update(library=None, call=None)
    state['configuration'] = configuration
    return {'configured': True, 'loaded': state['library'] is not None, 'live_validation': 'pending'}


def _load():
    state = _state(); config = state['configuration']
    if config is None:
        raise AdapterError('BRIDGE_NOT_CONFIGURED', 'Root must configure the reviewed native bridge first')
    path = Path(config['path'])
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != config['sha256']:
        raise AdapterError('BRIDGE_CHANGED', 'Pinned DLL changed')
    identity = process_identity()
    if state['library'] is None:
        if sys.platform != 'win32':
            raise AdapterError('UNSUPPORTED_PLATFORM', 'Windows x64 embedded MD is required')
        library = ctypes.CDLL(str(path), winmode=0x900)
        version = library.mdqt_world_adapter_abi_version; version.argtypes = []; version.restype = ctypes.c_uint32
        if version() != 1:
            raise AdapterError('ABI_MISMATCH', 'World adapter ABI1 required')
        call = library.mdqt_world_adapter_json
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=identity)
    if state['process'] != identity:
        raise AdapterError('PROCESS_IDENTITY_MISMATCH', 'Adapter belongs to another process lifetime')
    return state['call'], identity


def _failure(exc, *, unknown=False, effects=False):
    return {'status': 'UNKNOWN' if unknown else 'REJECTED', 'result': {'verified': False, 'world_transform_verified': False},
            'effects_started': bool(effects or unknown), 'requires_recovery': unknown,
            'error': {'code': getattr(exc, 'code', 'WORLD_ADAPTER_ERROR'), 'message': str(exc)}}


def _blocked_reply():
    return {'status': 'REJECTED', 'result': {'verified': False, 'world_transform_verified': False},
            'effects_started': False, 'requires_recovery': True,
            'error': {'code': 'RECOVERY_REQUIRED', 'message': 'A previous uncertain operation blocks new input/selection'}}


def _validate_raw(raw, code, request):
    if (type(raw) is not dict or raw.get('schema') != SCHEMA or type(raw.get('return_code')) is not int
            or raw['return_code'] != code or raw.get('operation') != request['operation']):
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Native schema/operation/return code mismatch')
    for key in ('verified', 'effects_started', 'requires_recovery', 'paired_release_verified', 'press_outstanding'):
        if type(raw.get(key)) is not bool:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Typed native flags required')
    counts = [raw.get(k) for k in ('event_calls', 'scene_mouse_calls', 'key_event_calls')]
    if any(type(n) is not int or not 0 <= n <= 5 for n in counts) or counts[0] != counts[1] + counts[2] or raw['effects_started'] is not bool(counts[0]):
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Actual event counts disagree')
    zero = raw.get('zero_operation_counts')
    if type(zero) is not dict or set(zero) != set(ZERO_COUNTS) or any(type(zero[k]) is not int or zero[k] != 0 for k in zero):
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Forbidden operation count is nonzero or untyped')
    for key in ('semantic_geometry_verified', 'world_axis_mapping_verified', 'world_transform_verified', 'rotation_angle_verified', 'native_target_hit_verified'):
        if raw.get(key) is not False:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Native primitive cannot establish ' + key)
    if raw.get('status') != 'SUCCEEDED':
        if raw.get('status') not in ('REJECTED', 'PARTIAL', 'UNKNOWN') or raw['verified'] or code == 0 or raw['effects_started'] and not raw['requires_recovery']:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Failed receipt contradicts effects/recovery')
        return
    if (code != 0 or not raw['verified'] or raw['requires_recovery'] or raw['press_outstanding']
            or not raw['paired_release_verified'] or raw.get('dispatch_completed') is not True or raw.get('physical_state_unchanged') is not True):
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Success lacks closed input and physical scope')
    operation = request['operation']
    for key in ('before', 'after'):
        value = raw.get(key)
        if type(value) is not dict or value.get('view') != '3d' or value.get('scene_item_grabber_absent') is not True:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Current 3D descriptor/grab cleanup required')
        _hex(value.get('mapping_fingerprint'), 'mapping_fingerprint')
        _hex(value.get('view_fingerprint'), 'view_fingerprint')
    if raw['before']['mapping_fingerprint'] != raw['after']['mapping_fingerprint']:
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Public view mapping changed')
    if operation != 'inspect' and any(raw['before'].get(k) != request[k] for k in ('native_view_ref', 'view_fingerprint')):
        raise AdapterError('INVALID_NATIVE_RECEIPT', 'Receipt belongs to another 3D view')
    if operation == 'inspect':
        if counts != [0, 0, 0]:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Inspection cannot emit input')
    elif operation == 'arrow_step':
        rows = raw.get('key_events')
        if counts != [2, 0, 2] or type(rows) is not list or len(rows) != 2:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Exactly one arrow P/R pair required')
        for stage, row in zip(('press', 'release'), rows):
            if (row.get('stage') != stage or type(row.get('key')) is not int or row['key'] != DIRECTIONS[request['direction']]
                    or type(row.get('modifiers')) is not int or row['modifiers'] != 0 or row.get('auto_repeat') is not False
                    or type(row.get('count')) is not int or row['count'] != 1
                    or any(row.get(k) is not True for k in ('sent', 'returned', 'nonspontaneous'))):
                raise AdapterError('INVALID_NATIVE_RECEIPT', 'Arrow event contract differs')
    else:
        rows = raw.get('mouse_events')
        if (counts != [5, 5, 0] or type(rows) is not list or len(rows) != 5
                or raw.get('purpose') != request['purpose'] or raw.get('profile') != 'NoMod_hover_press_activation_move_release_v1'
                or raw.get('all_mouse_events_nonspontaneous') is not True):
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Fixed five-event gizmo/ring profile required')
        for i, (stage, button, buttons) in enumerate((('hover', 0, 0), ('press', 1, 1), ('activation', 0, 1), ('move', 0, 1), ('release', 1, 0))):
            row = rows[i]; position = request['start'] if i < 2 else request['activation'] if i == 2 else request['end']
            if (row.get('stage') != stage or row.get('viewport_logical') != position
                    or any(type(row.get(k)) is not int or row[k] != v for k, v in (('button', button), ('buttons', buttons), ('modifiers', 0)))
                    or any(row.get(k) is not True for k in ('sent', 'returned'))):
                raise AdapterError('INVALID_NATIVE_RECEIPT', 'Gizmo event coordinates/buttons/modifiers differ')


def _invoke(request):
    state = _state(); attempted = False
    if state.get('in_call'):
        return _failure(AdapterError('BRIDGE_BUSY', 'The current native invocation must finish'))
    state['in_call'] = True
    try:
        if state['blocked']:
            return _blocked_reply()
        call, identity = _load()
        payload = _canonical(request).encode('utf8')
        if len(payload) > 16384:
            raise AdapterError('INVALID_PARAMETERS', 'Native request exceeds byte budget')
        output = ctypes.create_string_buffer(CAPACITY); written = ctypes.c_uint32(); needed = ctypes.c_uint32()
        attempted = True
        code = call(identity['pid'], identity['creation_filetime'], payload, len(payload), output, CAPACITY, ctypes.byref(written), ctypes.byref(needed))
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise AdapterError('INVALID_NATIVE_RECEIPT', 'Native output missing/truncated; no buffer retry allowed')
        raw = json.loads(output.raw[:written.value].decode('utf8'))
        _validate_raw(raw, code, request)
        if raw['requires_recovery']:
            state['blocked'] = True
        return {'status': raw['status'], 'result': raw, 'error': None if raw['status'] == 'SUCCEEDED' else {'code': raw.get('error', 'NATIVE_REJECTED'), 'message': 'See complete native receipt'},
                'effects_started': raw['effects_started'], 'requires_recovery': raw['requires_recovery']}
    except Exception as exc:
        if attempted:
            state['blocked'] = True
        return _failure(exc, unknown=attempted)
    finally:
        state['in_call'] = False


def _encode_ref(native, token):
    value = {'version': 1, 'view': '3d', 'native_view_ref': native, 'scene_token': token}
    return PREFIX + base64.urlsafe_b64encode(_canonical(value).encode()).decode().rstrip('=')


def _decode_ref(value, token):
    try:
        if type(value) is not str or not value.startswith(PREFIX) or len(value) > 2048:
            raise ValueError()
        part = value[len(PREFIX):]
        obj = json.loads(base64.b64decode(part + '=' * (-len(part) % 4), altchars=b'-_', validate=True))
        if (type(obj) is not dict or set(obj) != {'version', 'view', 'native_view_ref', 'scene_token'}
                or type(obj['version']) is not int or obj['version'] != 1 or obj['view'] != '3d' or obj['scene_token'] != token
                or type(obj['native_view_ref']) is not str or re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', obj['native_view_ref']) is None):
            raise ValueError()
        return obj['native_view_ref']
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise AdapterError('STALE_VIEW_REFERENCE', 'Use a fresh world_adapter_inspect_3d view reference') from exc


def _publish(value, token):
    result = dict(value)
    result['view_ref'] = _encode_ref(result.pop('native_view_ref'), token)
    _decode_ref(result['view_ref'], token)
    return result


def runtime_capabilities(params):
    _params(params)
    state = _state()
    return {'configured': state['configuration'] is not None, 'loaded': state['library'] is not None,
            'blocked_until_recovery': state['blocked'], 'qt_required': '6.10.3 release Windows x64',
            'live_validation': 'pending', 'world_transform_verified': False,
            'screen_directions_are_world_axes': False, 'OBJ_indices_are_native_indices': False,
            'no_DLL_load_or_MD_API_calls_in_this_probe': True}


def inspect_3d(params):
    try:
        _params(params, optional={'budget_ms'})
        before = scene()
        response = _invoke({'operation': 'inspect', 'budget_ms': _budget(params.get('budget_ms', 750))})
        if response['status'] == 'SUCCEEDED':
            after = scene()
            if before != after:
                raise AdapterError('SCENE_CHANGED_DURING_READ', 'Current scene changed during inspection')
            for key in ('before', 'after'):
                response['result'][key] = _publish(response['result'][key], after['scene_token'])
            response['result']['scene_token'] = after['scene_token']
        return response
    except Exception as exc:
        return _failure(exc)


def _read_camera_basis(export, *, include_properties=False):
    with TemporaryDirectory(prefix='md-mcp-camera-') as directory:
        path = Path(directory) / 'current_camera.zcmr'
        export(str(path), False)
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 4 * 1024 * 1024:
            raise AdapterError('CAMERA_EXPORT_MISSING', 'Expected a bounded current camera archive')
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 16:
                raise AdapterError('INVALID_CAMERA_ARCHIVE', 'Camera archive has too many members')
            members = {}
            names = ('current_camera.cmt', 'current_camera.cpt') if include_properties else ('current_camera.cmt',)
            for name in names:
                matches = [item for item in entries if item.filename == name]
                if len(matches) != 1 or matches[0].flag_bits & 1 or not 0 < matches[0].file_size <= 1024 * 1024:
                    raise AdapterError('INVALID_CAMERA_ARCHIVE', 'Expected one bounded ' + name)
                members[name] = archive.read(matches[0])
    raw = members['current_camera.cmt']
    parts = [part.strip() for part in raw.decode('utf-8-sig').strip().split('|^^|')]
    if parts[-1] == '':
        parts.pop()
    if len(parts) != 3 or parts[:2] != ['Version: 1', 'Type: 0']:
        raise AdapterError('INVALID_CAMERA_MATRIX', 'Expected the observed Version 1 Type 0 camera matrix')
    matrix = [float(value) for value in parts[2].split()]
    if len(matrix) != 16 or not all(map(math.isfinite, matrix)) or matrix[12:] != [0, 0, 0, 1]:
        raise AdapterError('INVALID_CAMERA_MATRIX', 'Expected a finite affine matrix')
    result = {'right_world': matrix[0:9:4], 'up_world': matrix[1:10:4],
              'matrix': [matrix[i:i + 4] for i in range(0, 16, 4)], 'cmt_sha256': hashlib.sha256(raw).hexdigest()}
    if include_properties:
        properties = members['current_camera.cpt']
        result.update(cpt_sha256=hashlib.sha256(properties).hexdigest(),
                      cpt_base64=base64.b64encode(properties).decode('ascii'),
                      pixel_intrinsics_verified=False)
        from .world_projection import camera_pose_from_matrix

        try:
            result['projection_pose'] = camera_pose_from_matrix(result['matrix'])
        except ValueError as exc:
            result['projection_pose_error'] = str(exc)
    return result


def read_camera(params):
    """Bind the two current camera archive members to the same background 3D view."""
    try:
        _params(params, {'scene_token'})
        token = _hex(params['scene_token'], 'scene_token')
        current = scene()
        if current['scene_token'] != token:
            raise AdapterError('STALE_SCENE', 'Read the current scene token')
        before = inspect_3d({})
        if before['status'] != 'SUCCEEDED':
            return before
        export = importlib.import_module('export_api').GenerateZcmrFrom3DWindow
        camera = _read_camera_basis(export, include_properties=True)
        after = inspect_3d({})
        if after['status'] != 'SUCCEEDED':
            return after
        first, last = before['result']['after'], after['result']['after']
        if (scene() != current or any(first[key] != last[key] for key in (
                'view_ref', 'view_fingerprint', 'mapping_fingerprint'))):
            raise AdapterError('CAMERA_VIEW_CHANGED_DURING_READ', 'The scene or 3D view changed during camera export')
        return {'status': 'SUCCEEDED', 'result': {'verified': True, 'scene_token': token, 'camera': camera, 'view': last},
                'error': None, 'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure(exc)


def _camera_preset_matches(camera, view):
    return (camera['right_world'] == CAMERA_PRESETS[view][1] and camera['up_world'] == [0.0, 1.0, 0.0]
            and (view != 'quarter' or [row[2] for row in camera['matrix'][:3]] == QUARTER_FORWARD))


def set_canonical_view(params):
    effects = False
    observation = None
    try:
        _params(params, {'view'}, {'fit_garment'})
        if type(params.get('fit_garment', False)) is not bool:
            raise AdapterError('INVALID_PARAMETERS', 'fit_garment must be a boolean')
        if type(params['view']) is not str or params['view'] not in CAMERA_PRESETS:
            raise AdapterError('INVALID_PARAMETERS', 'Use the verified Front, Left or Quarter preset')
        if params['view'] == 'quarter' or params.get('fit_garment') is True:
            return _set_scoped_camera_preset(params['view'], fit=params.get('fit_garment', False))
        utility = importlib.import_module('utility_api')
        export = importlib.import_module('export_api').GenerateZcmrFrom3DWindow
        before = scene()
        before_view = _invoke({'operation': 'inspect', 'budget_ms': 750})
        if before_view['status'] != 'SUCCEEDED':
            return before_view
        index, right = CANONICAL_VIEWPOINTS[params['view']]
        effects = True
        utility.SetCamViewPoint(index)
        camera = _read_camera_basis(export)
        after_view = _invoke({'operation': 'inspect', 'budget_ms': 750})
        physical_before = before_view['result']['physical_after']
        physical_after = (after_view['result'] or {}).get('physical_after', {})
        observation = {'camera': camera, 'view': params['view'], 'setter_returned': True,
                       'scene_token': before['scene_token'], 'scene_preserved': before == scene(),
                       'background_verified': after_view['status'] == 'SUCCEEDED' and
                       {k: v for k, v in physical_before.items() if k != 'cursor_position'} ==
                       {k: v for k, v in physical_after.items() if k != 'cursor_position'}}
        if (camera['right_world'] != right or camera['up_world'] != [0.0, 1.0, 0.0]
                or not observation['scene_preserved'] or not observation['background_verified']):
            raise AdapterError('CANONICAL_VIEW_NOT_VERIFIED', 'The camera basis, scene or background readback differs')
        return {'status': 'SUCCEEDED', 'verified': True, 'effects_started': True, 'requires_recovery': False,
                'view': params['view'], 'viewpoint': utility.GetViewPoint(), 'camera': camera,
                'scene_token': before['scene_token']}
    except Exception as exc:
        reply = _failure(exc, unknown=effects)
        if observation is not None:
            reply['result']['camera_observation'] = observation
        return reply


def _camera_fit_scope(observed):
    if observed['status'] != 'SUCCEEDED':
        raise AdapterError('CANONICAL_VIEW_NOT_VERIFIED', 'The same current background 3D view is required')
    result = observed['result']
    view, physical = result['after'], result['physical_after']
    if (view['scene_item_grabber_absent'] is not True or physical['background_safe'] is not True
            or view['mapping']['enabled'] is not True or view['mapping']['relative_visible'] is not True):
        raise AdapterError('CANONICAL_VIEW_NOT_VERIFIED', 'Require idle, enabled background 3D view')
    descriptor = {key: value for key, value in view.items()
                  if key not in ('mapping', 'view_fingerprint', 'mapping_fingerprint')}
    descriptor['mapping'] = {key: value for key, value in view['mapping'].items() if key != 'checked_actions'}
    return {'view': descriptor, 'physical': {key: value for key, value in physical.items() if key != 'cursor_position'}}


def _set_scoped_camera_preset(view, *, fit):
    observation = {'view': view, 'fit_garment': fit, 'viewpoint_attempted': False, 'viewpoint_returned': False,
                   'fit_attempted': False, 'fit_returned': False}
    try:
        utility = importlib.import_module('utility_api')
        export = importlib.import_module('export_api').GenerateZcmrFrom3DWindow
        if fit:
            zoom = getattr(utility, 'SetZoomView', None)
            signature = ''.join((getattr(zoom, '__doc__', '') or '').lower().split())
            if not callable(zoom) or 'setzoomview()->none' not in signature:
                raise AdapterError('GARMENT_FIT_UNAVAILABLE', 'The observed no-argument SetZoomView() -> None is required')
        before = scene()
        first = _invoke({'operation': 'inspect', 'budget_ms': 1500})
        if first['status'] != 'SUCCEEDED':
            return first
        scope = _camera_fit_scope(first)
        if scene() != before:
            raise AdapterError('STALE_SCENE', 'The current scene changed before the camera request')
        index = CAMERA_PRESETS[view][0]
        observation.update(scene_token=before['scene_token'], scene_identity=before['identity'],
                           original_scope=scope, viewpoint_attempted=True)
        utility.SetCamViewPoint(index)
        observation['viewpoint_returned'] = True
        canonical = _read_camera_basis(export, include_properties=True)
        observation['canonical_camera'] = canonical
        if (not _camera_preset_matches(canonical, view)
                or _camera_fit_scope(_invoke({'operation': 'inspect', 'budget_ms': 1500})) != scope
                or scene() != before):
            raise AdapterError('CANONICAL_VIEW_NOT_VERIFIED', 'The camera preset basis or background scope differs')
        if not fit:
            return {'status': 'SUCCEEDED', 'verified': True, 'effects_started': True, 'requires_recovery': False,
                    'view': view, 'viewpoint': utility.GetViewPoint(), 'camera': canonical,
                    'scene_token': before['scene_token']}
        observation['fit_attempted'] = True
        returned = zoom()
        observation['fit_returned'] = True
        fitted = _read_camera_basis(export, include_properties=True)
        observation['camera'] = fitted
        if (returned is not None or [row[:3] for row in fitted['matrix'][:3]] != [row[:3] for row in canonical['matrix'][:3]]
                or _camera_fit_scope(_invoke({'operation': 'inspect', 'budget_ms': 1500})) != scope
                or scene() != before):
            raise AdapterError('CANONICAL_VIEW_NOT_VERIFIED', 'Garment fit did not preserve the scene, basis and background view')
        return {'status': 'SUCCEEDED', 'verified': True, 'effects_started': True, 'requires_recovery': False,
                'view': view, 'viewpoint': utility.GetViewPoint(), 'fit_garment': True, 'fit_command_returned': True,
                'framing_change_observed': any(fitted[key] != canonical[key] for key in ('cmt_sha256', 'cpt_sha256')),
                'canonical_camera': canonical, 'camera': fitted, 'scene_token': before['scene_token']}
    except Exception as exc:
        effects = observation['viewpoint_attempted']
        if effects and observation['viewpoint_returned'] and (not observation['fit_attempted'] or observation['fit_returned']):
            exc = AdapterError('CANONICAL_VIEW_NOT_VERIFIED', str(exc))
        elif effects:
            exc = AdapterError('CANONICAL_VIEW_CALL_UNRESOLVED', str(exc))
        reply = _failure(exc, unknown=effects)
        if effects:
            reply['result']['camera_observation'] = observation
        return reply


def reconcile_camera(params):
    from . import dispatch

    try:
        _params(params, {'blocked_request_id', 'scene_token', 'view'}, {'fit_garment'})
        old_id = params['blocked_request_id']
        token = _hex(params['scene_token'], 'scene_token')
        view = params['view']
        fit = params.get('fit_garment', False)
        if type(view) is not str or view not in CAMERA_PRESETS or type(fit) is not bool:
            raise AdapterError('INVALID_PARAMETERS', 'Expected the original Front, Left or Quarter request')
        old = dispatch._RECEIPTS.get(old_id) or {}
        if (old.get('operation_id') != old_id or old.get('status') != 'UNKNOWN'
                or old.get('effects_started') is not True or old.get('requires_recovery') is not True
                or (old.get('error') or {}).get('code') != 'CANONICAL_VIEW_NOT_VERIFIED'):
            raise AdapterError('ORIGINAL_CAMERA_RECEIPT_REQUIRED', 'Expected the original returned camera operation')
        original = dispatch._PAYLOADS.get(old_id)
        original_params = {'view': view}
        if 'fit_garment' in params:
            original_params['fit_garment'] = fit
        fingerprint = digest({'operation': 'world_adapter.set_canonical_view', 'params': original_params, 'scene_token': token})
        before = scene()
        if original != fingerprint or before['scene_token'] != token:
            raise AdapterError('STALE_CAMERA_RECOVERY', 'The scene or original camera request changed')
        export = importlib.import_module('export_api').GenerateZcmrFrom3DWindow
        if fit or view == 'quarter':
            observation = (old.get('result') or {}).get('camera_observation') or {}
            if (observation.get('viewpoint_returned') is not True or type(observation.get('fit_attempted')) is not bool
                    or observation.get('fit_returned') is not observation['fit_attempted']
                    or observation.get('scene_token') != token or observation.get('scene_identity') != before['identity']):
                raise AdapterError('ORIGINAL_CAMERA_RECEIPT_REQUIRED', 'The original camera/fit calls must have returned')
            prefix = _camera_fit_scope(_invoke({'operation': 'inspect', 'budget_ms': 1500}))
            if prefix != observation.get('original_scope'):
                raise AdapterError('CAMERA_RECOVERY_NOT_VERIFIED', 'The original background 3D viewport differs')
            camera = _read_camera_basis(export, include_properties=True)
            observed = _invoke({'operation': 'inspect', 'budget_ms': 1500})
            if _camera_fit_scope(observed) != prefix:
                raise AdapterError('CAMERA_RECOVERY_NOT_VERIFIED', 'The background 3D viewport changed during camera export')
        else:
            camera = _read_camera_basis(export)
            observed = inspect_3d({})
        if observed['status'] != 'SUCCEEDED' or scene() != before:
            raise AdapterError('CAMERA_RECOVERY_NOT_VERIFIED', 'A stable background camera readback is required')
        result = {'status': 'SUCCEEDED', 'verified': True, 'effects_started': False, 'requires_recovery': False,
                'recovered_operation_id': old_id, 'recovery_action': 'WORLD_CAMERA_OBSERVED',
                'scene_token': token, 'current_camera_observed': True, 'camera': camera, 'view': view,
                'requested_view_verified': _camera_preset_matches(camera, view)}
        if 'fit_garment' in params:
            result.update(fit_garment=fit, requested_framing_verified=False)
        return result
    except Exception as exc:
        return _failure(exc)


def _target(snapshot, pattern_uuid):
    matches = [row for row in snapshot['patterns'] if row['pattern_uuid'] == pattern_uuid]
    if len(matches) != 1:
        raise AdapterError('STALE_PATTERN_REFERENCE', 'UUID must resolve uniquely in the current scene')
    row = matches[0]; name = row.get('information', {}).get('name')
    _text(name, 'current pattern name')
    if sum(other.get('information', {}).get('name') == name for other in snapshot['patterns']) != 1:
        raise AdapterError('AMBIGUOUS_SELECTION_NAME', 'Selected-name readback requires one unique current name')
    return row, name


def _selection(snapshot, pattern_uuid):
    api = importlib.import_module('pattern_api'); target, name = _target(snapshot, pattern_uuid)
    selected = [api.GetSelectedPatternViaIndex(row['index']) for row in snapshot['patterns']]
    names = api.GetSelectedPattern()
    if (any(type(v) is not bool for v in selected) or selected != [row['index'] == target['index'] for row in snapshot['patterns']]
            or type(names) is not list or names != [name]):
        raise AdapterError('CURRENT_3D_SINGLETON_NOT_SELECTED', 'Select this UUID in 3D, then inspect the current view; no native input was sent')
    return {'pattern_uuid': pattern_uuid, 'SDK_pattern_index': target['index'],
            'selected_index_readbacks': selected, 'selected_names': names, 'verified': True,
            'native_mesh_owner_mapping_verified': False}


def select_pattern(params):
    effects = False
    try:
        if _state()['blocked']:
            return _blocked_reply()
        _params(params, {'pattern_uuid', 'scene_token'})
        token = _hex(params['scene_token'], 'scene_token'); target_uuid = _text(params['pattern_uuid'], 'pattern_uuid')
        before = scene()
        if before['scene_token'] != token:
            raise AdapterError('STALE_SCENE', 'Read the current scene before selection')
        target, _ = _target(before, target_uuid)
        api = importlib.import_module('pattern_api')
        effects = True
        returned = api.SelectPatternViaIndex(target['index'], False)
        if returned is not None:
            raise AdapterError('SELECTION_RETURN_UNVERIFIED', 'Documented selection setter must return None')
        after = scene()
        if after != before:
            raise AdapterError('SCENE_CHANGED_DURING_SELECTION', 'Scene data changed unexpectedly during selection')
        proof = _selection(after, target_uuid)
        return {'status': 'SUCCEEDED', 'result': {'verified': True, 'selection': proof, 'scene_token': token,
                'SDK_selection_calls': 1, 'keep_previous_selection': False, 'geometry_write_attempted': False}, 'error': None,
                'effects_started': True, 'requires_recovery': False}
    except Exception as exc:
        if effects:
            _state()['blocked'] = True
        return _failure(exc, unknown=effects)


def _mesh_summary(snapshot):
    return {key: snapshot[key] for key in ('capture_id', 'snapshot_id', 'topology_fingerprint', 'vertex_count', 'scene_token', 'scene_identity', 'group_inventory', 'export')}


def bind_export_labels(current_scene, exported, pattern_uuid, group_name):
    """Complete current-name/OBJ-label bijection, NOT a native owner certificate.

    Extra groups remain unclassified and protected. The certificate never uses
    OBJ order to infer native indices, accepts a caller verified flag, or says a
    native patternList filter was honoured. Runtime callers freshly recompute it.
    """
    mesh._validate_snapshot(exported)
    _text(pattern_uuid, 'pattern_uuid'); _text(group_name, 'group_name')
    if (type(current_scene) is not dict or exported.get('scope') != 'scene_garment_OBJ_export' or exported.get('pattern_uuid') is not None
            or exported.get('scene_token') != current_scene.get('scene_token') or exported.get('scene_identity') != current_scene.get('identity')):
        raise AdapterError('UNBOUND_SCENE_EXPORT', 'One full current-scene export with matching identity/token is required')
    if exported.get('space') != 'world' or exported.get('unit') != 'mm' or exported.get('axis_order') != ['X', 'Y', 'Z']:
        raise AdapterError('UNVERIFIED_EXPORT_COORDINATES', 'Explicit world-mm unconverted XYZ export required')
    rows = current_scene.get('patterns')
    if type(rows) is not list or not 1 <= len(rows) <= 10000:
        raise AdapterError('INVALID_PATTERN_LIST', 'Complete current SDK pattern list required')
    names, uuids = set(), set()
    inventory = mesh.group_inventory(exported)
    if inventory != exported.get('group_inventory') or inventory['ungrouped_face_count'] or inventory['nonexclusive_face_count']:
        raise AdapterError('AMBIGUOUS_EXPORT_LABELS', 'Every exported face must have exactly one explicit group label')
    if max(len(rows), len(inventory['groups'])) * len(exported['faces']) > MAX_LABEL_FACE_WORK:
        raise AdapterError('LABEL_COMPARISON_BUDGET', 'Export label partition exceeds the bounded comparison budget')
    pairs = []; owners = {}
    for index, row in enumerate(rows):
        if (type(row) is not dict or type(row.get('index')) is not int or row['index'] != index
                or type(row.get('information')) is not dict):
            raise AdapterError('INVALID_PATTERN_LIST', 'Fresh ordered SDK index/UUID/name records required')
        identity = _text(row.get('pattern_uuid'), 'pattern_uuid'); name = _text(row['information'].get('name'), 'current pattern name')
        if row['information'].get('uuid') != identity:
            raise AdapterError('CONTRADICTORY_PATTERN_IDENTITY', 'SDK information UUID differs from the current row')
        if identity in uuids or name in names:
            raise AdapterError('AMBIGUOUS_RUNTIME_LABEL', 'SDK pattern UUIDs and names must both be unique')
        uuids.add(identity); names.add(name)
        if name not in inventory['groups']:
            raise AdapterError('INCOMPLETE_PATTERN_GROUP_LABELS', 'Every current SDK pattern name must have an exact nonempty group')
        group = mesh.extract_group(exported, name)
        for vertex in group['group_index_maps']['positions_mm']['compact_to_export']:
            if vertex in owners:
                raise AdapterError('SHARED_PAPER_EXPORT_VERTICES', 'Paper groups share exported vertex indices; independent grouping is unproven')
            owners[vertex] = identity
        pairs.append({'pattern_uuid': identity, 'SDK_pattern_index': index, 'current_name': name, 'group_name': name,
                      'face_count': len(group['faces']), 'vertex_count': group['vertex_count'],
                      'group_topology_fingerprint': group['topology_fingerprint']})
    selected = [row for row in pairs if row['pattern_uuid'] == pattern_uuid]
    if len(selected) != 1 or selected[0]['group_name'] != group_name:
        raise AdapterError('UUID_GROUP_LABEL_MISMATCH', 'Requested group must exactly equal this UUID current unique SDK name')
    result = {'schema': 'md-current-SDK-name-OBJ-label-bijection-v1', 'verified': True,
              'verification_scope': 'label_bijection_only; not independent native mesh ownership',
              'scene_token': exported['scene_token'], 'scene_identity': exported['scene_identity'],
              'snapshot_id': exported['snapshot_id'], 'capture_id': exported['capture_id'],
              'obj_asset': exported['export']['obj_asset'], 'pairs': pairs, 'target': selected[0],
              'additional_unclassified_groups': sorted(set(inventory['groups']) - names),
              'all_runtime_pattern_names_covered': True, 'all_faces_have_one_group': True,
              'paper_group_export_vertex_indices_disjoint': True,
              'native_owner_mapping_verified': False, 'native_vertex_mapping_verified': False,
              'native_pattern_filter_verified': False, 'solver_rest_verified': False}
    result['certificate_id'] = 'sha256:' + hashlib.sha256(_canonical(result).encode()).hexdigest()
    return result


def compare_exports(before, after, group_name, *, companion_groups=()):
    """Measured world-mm OBJ-label changes; no UUID/solver correspondence claim."""
    mesh._validate_snapshot(before); mesh._validate_snapshot(after)
    if len(before['group_inventory']['groups']) * len(before['faces']) > MAX_LABEL_FACE_WORK:
        raise AdapterError('LABEL_COMPARISON_BUDGET', 'Export comparison exceeds the bounded group/face work budget')
    a, b = mesh.extract_group(before, group_name), mesh.extract_group(after, group_name)
    topology = before['topology_fingerprint'] == after['topology_fingerprint'] and before['face_labels'] == after['face_labels']
    compatible = topology and a['group_index_maps'] == b['group_index_maps'] and all(
        mesh.extract_group(before, name)['group_index_maps'] == mesh.extract_group(after, name)['group_index_maps']
        for name in companion_groups)
    uv_exact = before['uv'] == after['uv']
    if not compatible:
        return {'topology_and_index_maps_exact': False, 'uv_exact': uv_exact, 'comparison_completed': True,
                'world_transform_verified': False, 'reason': 'EXPORTED_TOPOLOGY_OR_LABELS_CHANGED'}
    delta = [[y - x for x, y in zip(p, q)] for p, q in zip(a['positions_mm'], b['positions_mm'])]
    common = delta[0]
    residual = max(math.dist(row, common) for row in delta)
    other = {}
    for name in before['group_inventory']['groups']:
        if name == group_name or name in companion_groups:
            continue
        first, last = mesh.extract_group(before, name), mesh.extract_group(after, name)
        other[name] = all(first[key] == last[key] for key in ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices', 'face_labels'))
    return {'comparison_completed': True, 'group_name': group_name, 'group_reference_scope': 'exact_OBJ_label_only',
            'topology_and_index_maps_exact': compatible, 'uv_exact': uv_exact, 'other_export_groups_exact': other,
            'other_export_groups_all_exact': all(other.values()),
            'changed_vertex_count': sum(x != y for x, y in zip(a['positions_mm'], b['positions_mm'])),
            'maximum_vertex_displacement_mm': max(math.dist(p, q) for p, q in zip(a['positions_mm'], b['positions_mm'])),
            'first_vertex_delta_world_mm': common, 'maximum_common_translation_residual_mm': residual,
            'uniform_translation_le_0p002_mm_observed': residual <= .002,
            'target_normals_exact': a['normals'] == b['normals'],
            'requested_world_delta_or_angle': None, 'world_transform_verified': False,
            'group_to_requested_UUID_verified': False, 'native_vertex_mapping_verified': False, 'solver_rest_verified': False}


def _validate_rigid_matrix(matrix, tolerance_mm):
    if type(tolerance_mm) not in (int, float) or not math.isfinite(tolerance_mm) or not 0 < tolerance_mm <= .002:
        raise ValueError('tolerance_mm must be in (0,.002]')
    if (type(matrix) is not list or len(matrix) != 4 or any(type(row) is not list or len(row) != 4 for row in matrix)
            or any(type(x) not in (int, float) or not math.isfinite(x) for row in matrix for x in row) or matrix[3] != [0, 0, 0, 1]):
        raise ValueError('Finite affine 4x4 matrix required')
    r = [row[:3] for row in matrix[:3]]
    if any(abs(math.fsum(a * b for a, b in zip(r[i], r[j])) - int(i == j)) > 1e-10 for i in range(3) for j in range(3)):
        raise ValueError('Only proper rigid transforms are supported')
    det = sum(r[0][i] * (r[1][(i + 1) % 3] * r[2][(i + 2) % 3] - r[1][(i + 2) % 3] * r[2][(i + 1) % 3]) for i in range(3))
    if abs(det - 1) > 1e-10:
        raise ValueError('Reflection/scaling is not a proper rigid transform')
    return r


def _apply_rigid_matrix(matrix, point, *, normal=False):
    result = [math.fsum(row[j] * point[j] for j in range(3)) + (0 if normal else row[3]) for row in matrix[:3]]
    if any(not math.isfinite(x) for x in result):
        raise ValueError('Nonfinite transformed coordinates')
    return result


def _complete_corners(group, array, corners):
    return bool(group[array]) and all(index is not None for row in group[corners] for index in row)


def _expected_motion(group, matrix):
    return max(math.dist(_apply_rigid_matrix(matrix, point), point) for point in group['positions_mm'])


def _outside_group_arrays_exact(before, after, moving_groups):
    groups = [mesh.extract_group(before, name) for name in moving_groups]
    for key in ('positions_mm', 'normals'):
        indices = {index for group in groups for index in group['group_index_maps'][key]['compact_to_export']}
        if not all(p == q for i, (p, q) in enumerate(zip(before[key], after[key], strict=True)) if i not in indices):
            return False
    return True


def _matrix_readback(before, after, group_name, matrix, tolerance_mm, first, last, observed, *, companion_groups=()):
    stable_binding = first['pairs'] == last['pairs'] and first['additional_unclassified_groups'] == last['additional_unclassified_groups']
    same_scene = (before['scene_identity'] == after['scene_identity'] and before['scene_token'] == after['scene_token'])
    fresh_captures = before['capture_id'] != after['capture_id']
    result = {'evidence_kind': 'exported_geometry_matrix_comparison',
              'exported_geometry_matches_requested_matrix': False,
              'exported_positions_match_requested_matrix': False, 'tolerance_mm': tolerance_mm,
              'matrix_world_mm': matrix, 'same_scene': same_scene, 'distinct_captures': fresh_captures,
              'stable_label_bijection': stable_binding, 'before_label_certificate': first['certificate_id'],
              'after_label_certificate': last['certificate_id'], 'observed': observed,
              'exact_world_writer_verified': False, 'full_scene_invariants_verified': False,
              'native_owner_mapping_verified': False, 'native_vertex_mapping_verified': False, 'solver_rest_verified': False}
    if not observed['topology_and_index_maps_exact']:
        return dict(result, reason='TOPOLOGY_OR_INDEX_MAP_CHANGED')
    owner_groups = (group_name, *companion_groups)
    records = {}
    expected_motion = None
    for name in owner_groups:
        a, b = mesh.extract_group(before, name), mesh.extract_group(after, name)
        expected = [_apply_rigid_matrix(matrix, point) for point in a['positions_mm']]
        if name == group_name:
            expected_motion = max(math.dist(p, q) for p, q in zip(expected, a['positions_mm'], strict=True))
        records[name] = {
            'vertex_count': len(a['positions_mm']), 'normal_count': len(a['normals']),
            'maximum_vertex_error_mm': max(math.dist(p, q) for p, q in zip(expected, b['positions_mm'], strict=True)),
            'maximum_normal_error': max((math.dist(_apply_rigid_matrix(matrix, p, normal=True), q)
                                         for p, q in zip(a['normals'], b['normals'], strict=True)), default=0),
            'normal_readback_complete': _complete_corners(a, 'normals', 'face_normal_indices'),
            'uv_readback_complete': _complete_corners(a, 'uv', 'face_uv_indices'),
        }
    maximum = max(record['maximum_vertex_error_mm'] for record in records.values())
    maximum_normal = max(record['maximum_normal_error'] for record in records.values())
    if not all(math.isfinite(error) for error in (maximum, maximum_normal, expected_motion)):
        raise ValueError('Nonfinite transformed readback error')
    normal_complete = all(record['normal_readback_complete'] for record in records.values())
    uv_complete = all(record['uv_readback_complete'] for record in records.values())
    # Include even unused OBJ rows outside the target. Group comparison alone
    # only sees values referenced by a face.
    outside_target_exact = _outside_group_arrays_exact(before, after, owner_groups)
    observed_motion = observed['maximum_vertex_displacement_mm']
    positions_passed = (same_scene and fresh_captures and stable_binding and observed['uv_exact']
                        and observed['other_export_groups_all_exact'] and outside_target_exact
                        and normal_complete and uv_complete and maximum <= tolerance_mm
                        and expected_motion > 2 * tolerance_mm and observed_motion > tolerance_mm)
    passed = positions_passed and maximum_normal <= 1e-6
    result.update(exported_geometry_matches_requested_matrix=passed, maximum_vertex_error_mm=maximum,
                  exported_positions_match_requested_matrix=positions_passed,
                  maximum_normal_error=maximum_normal if normal_complete else None,
                  target_vertex_count=records[group_name]['vertex_count'], target_normal_count=records[group_name]['normal_count'],
                  expected_maximum_motion_mm=expected_motion, observed_maximum_motion_mm=observed_motion,
                  normal_readback_complete=normal_complete, uv_readback_complete=uv_complete,
                  outside_target_export_arrays_exact=outside_target_exact)
    if companion_groups:
        result['moving_group_readback'] = records
    return result


def verify_rigid_transform_readback(current_before, current_after, before, after, pattern_uuid, group_name, matrix_world_mm, tolerance_mm=.002, *, companion_groups=()):
    """Compare the supplied rigid matrix with two independently exported meshes."""
    _validate_rigid_matrix(matrix_world_mm, tolerance_mm)
    first = bind_export_labels(current_before, before, pattern_uuid, group_name)
    last = bind_export_labels(current_after, after, pattern_uuid, group_name)
    observed = compare_exports(before, after, group_name, companion_groups=companion_groups)
    return _matrix_readback(before, after, group_name, matrix_world_mm, tolerance_mm, first, last, observed,
                            companion_groups=companion_groups)


def _mesh_read():
    response = mesh.OPERATIONS['mesh.read_scene']({})
    if response.get('status') != 'SUCCEEDED':
        raise AdapterError('MESH_READ_UNAVAILABLE', 'Complete fresh scene OBJ export required before native input')
    return response['snapshot']


def read_group_binding(params):
    try:
        _params(params, {'pattern_uuid', 'group_name', 'scene_token'})
        token = _hex(params['scene_token'], 'scene_token'); current = scene()
        if current['scene_token'] != token:
            raise AdapterError('STALE_SCENE', 'Current scene token required')
        exported = _mesh_read()
        certificate = bind_export_labels(current, exported, params['pattern_uuid'], params['group_name'])
        if scene() != current:
            raise AdapterError('STALE_SCENE', 'Scene changed while collecting label evidence')
        return {'status': 'SUCCEEDED', 'result': {'verified': True, 'label_certificate': certificate,
                'mesh': _mesh_summary(exported), 'world_transform_verified': False},
                'error': None, 'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure(exc)


def topstitch_label_matches(rows, labels):
    keys = {'TopstitchName', 'Pattern Topstitch Index'}
    records, layout = [], True
    for row in rows:
        pending, assigned = {}, []
        for record in row['instances']:
            if not record or not record.keys() <= keys or pending.keys() & record.keys():
                return False, []
            pending.update(record)
            if pending.keys() == keys:
                index, name = pending['Pattern Topstitch Index'], pending['TopstitchName']
                if re.fullmatch(r'[0-9]{1,10}', index) is None or not name:
                    return False, []
                assigned.append((int(index), name))
                pending.clear()
        layout = layout and not pending and row['assigned_count'] == len(assigned) == len({index for index, _ in assigned})
        records.extend({'pattern_uuid': row['pattern_uuid'], 'assigned_index': index, 'exported_group': name}
                       for index, name in assigned)
    if not layout:
        return False, []
    papers = {row['pattern_name'] for row in rows}
    counts = {}
    for record in records:
        name = record['exported_group']
        counts[name] = counts.get(name, 0) + 1
    return True, [r for r in records if counts[r['exported_group']] == 1 and r['exported_group'] in labels
                  and r['exported_group'] not in papers]


def inspect_topstitch_assignments(params):
    try:
        _params(params, {'scene_token'})
        token = _hex(params['scene_token'], 'scene_token')
        current = scene()
        if current['scene_token'] != token:
            raise AdapterError('STALE_SCENE', 'Current scene token required')
        api = importlib.import_module('pattern_api')
        count = getattr(api, 'GetPatternAssignedTopstitchCount', None)
        assignments = getattr(api, 'GetPatternAssignedTopstitch', None)
        if not callable(count) or not callable(assignments):
            raise AdapterError('TOPSTITCH_ASSIGNMENT_UNAVAILABLE', 'The current MD runtime lacks the assignment getters')

        def read():
            rows = []
            for row in current['patterns']:
                index = row['index']
                total, instances = count(index), assignments(index)
                if (type(total) is not int or type(instances) is not list or not -1 <= total <= 4096
                        or len(instances) > 4096 or any(type(instance) is not dict or any(
                            type(k) is not str or type(v) is not str for k, v in instance.items()) for instance in instances)):
                    raise AdapterError('TOPSTITCH_ASSIGNMENT_READBACK_MISMATCH', 'Expected the runtime SDK integer and bounded list-of-string-maps schema')
                rows.append({'pattern_uuid': row['pattern_uuid'], 'pattern_name': row['information']['name'],
                             'pattern_index': index, 'assigned_count': total, 'instances': instances,
                             'count_matches_records': total == len(instances)})
            return rows

        rows = read()
        exported = _mesh_read()
        if scene() != current or read() != rows or scene() != current:
            raise AdapterError('STALE_SCENE', 'Assignment and export scope changed during readback')
        labels = sorted(exported['group_inventory']['groups'])
        layout, matches = topstitch_label_matches(rows, labels)
        return {'status': 'SUCCEEDED', 'result': {'verified': True, 'scene_token': token,
                'scene_identity': current['identity'], 'pattern_assignments': rows, 'exported_obj_groups': labels,
                'sdk_assignment_record_layout_verified': layout, 'exact_sdk_to_obj_label_matches': matches,
                'mesh': _mesh_summary(exported), 'export_group_ownership_verified': False},
                'error': None, 'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure(exc)


def _execute(operation, params, *, expected_world_arrays_sha256=None, companion_groups=(), settle_after=None,
             rotation_recovery=None):
    effects = False
    evidence = None

    def held(reply):
        if evidence is not None and reply['requires_recovery']:
            from .world_rotation_reconciliation import HOLD_KEY, hold_key

            _state()[HOLD_KEY] = hold_key(evidence)
            reply['result']['rotation_recovery'] = evidence
        return reply

    try:
        if _state()['blocked']:
            return _blocked_reply()
        extra = {'direction'} if operation == 'arrow_step' else {'start', 'activation', 'end', 'purpose'}
        optional = {'budget_ms', 'expected_matrix_world_mm', 'matrix_tolerance_mm'}
        if operation == 'arrow_step':
            optional.add('canonical_view')
        _params(params, {'pattern_uuid', 'group_name', 'view_ref', 'view_fingerprint', 'scene_token'} | extra, optional)
        token = _hex(params['scene_token'], 'scene_token'); target_uuid = _text(params['pattern_uuid'], 'pattern_uuid')
        group_name = _text(params['group_name'], 'group_name')
        request = {'operation': operation, 'budget_ms': _budget(params.get('budget_ms', 750)),
                   'native_view_ref': _decode_ref(params['view_ref'], token), 'view_fingerprint': _hex(params['view_fingerprint'], 'view_fingerprint')}
        if operation == 'arrow_step':
            if type(params['direction']) is not str or params['direction'] not in DIRECTIONS:
                raise AdapterError('INVALID_PARAMETERS', 'Only left/right/up/down exist; these are screen-relative commands')
            request['direction'] = params['direction']
            if 'canonical_view' in params and (type(params['canonical_view']) is not str
                    or params['canonical_view'] not in CANONICAL_VIEWPOINTS):
                raise AdapterError('INVALID_PARAMETERS', 'canonical_view must be Front or Left')
        else:
            if params['purpose'] not in PURPOSES:
                raise AdapterError('INVALID_PARAMETERS', 'Only translation_gizmo/rotation_ring exist')
            request.update({key: _xy(params[key]) for key in ('start', 'activation', 'end')}, purpose=params['purpose'])
            length = math.dist(request['start'], request['activation']) + math.dist(request['activation'], request['end'])
            if not 0 < math.dist(request['start'], request['activation']) or not 0 < math.dist(request['activation'], request['end']) or length > 128:
                raise AdapterError('INVALID_PARAMETERS', 'Nonzero two-segment path of at most 128 logical pixels required')
        matrix = params.get('expected_matrix_world_mm')
        tolerance = params.get('matrix_tolerance_mm', .002)
        if matrix is not None:
            rotation = _validate_rigid_matrix(matrix, tolerance)
            translation_only = rotation == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            expects_translation = request.get('purpose') != 'rotation_ring'
            if translation_only != expects_translation:
                raise AdapterError('MATRIX_KIND_MISMATCH', 'The expected rigid matrix must match the primitive type')
        elif 'matrix_tolerance_mm' in params:
            raise AdapterError('INVALID_PARAMETERS', 'A matrix tolerance requires an expected world matrix')
        canonical_view = params.get('canonical_view')
        if canonical_view is not None and matrix is not None:
            right = CANONICAL_VIEWPOINTS[canonical_view][1]
            axis = right if request['direction'] in ('left', 'right') else [0, 1, 0]
            sign = 1 if request['direction'] in ('right', 'up') else -1
            delta = [row[3] for row in matrix[:3]]
            along = sum(a * b for a, b in zip(axis, delta))
            if along * sign <= 0 or delta != [along * component for component in axis]:
                raise AdapterError('WORLD_DIRECTION_MISMATCH', 'The requested world delta does not follow the selected view and arrow')
        current = scene()
        if current['scene_token'] != token:
            raise AdapterError('STALE_SCENE', 'Read current scene and 3D view again')
        before = _mesh_read()
        if expected_world_arrays_sha256 is not None:
            from .world_frame import MESH_FIELDS

            if digest({key: before[key] for key in MESH_FIELDS}) != expected_world_arrays_sha256:
                raise AdapterError('WORLD_BASELINE_CHANGED', 'The current world OBJ changed after ring qualification')
        before_binding = bind_export_labels(current, before, target_uuid, group_name)
        # Fail before input for absent/ambiguous group or a mesh-induced scene change.
        for name in before['group_inventory']['groups']:
            mesh.extract_group(before, name)
        target_group = mesh.extract_group(before, group_name)
        if matrix is not None:
            moving = [target_group, *(mesh.extract_group(before, name) for name in companion_groups)]
            if not all(_complete_corners(group, 'normals', 'face_normal_indices')
                       and _complete_corners(group, 'uv', 'face_uv_indices') for group in moving):
                raise AdapterError('MATRIX_BASELINE_INCOMPLETE', 'The target baseline must include all face normals and UV indices')
            motion = _expected_motion(target_group, matrix)
            if not math.isfinite(motion) or motion <= 2 * tolerance:
                raise AdapterError('MATRIX_EXPECTATION_TOO_SMALL', 'Expected target motion must exceed twice the position tolerance')
        camera = None
        if canonical_view is not None:
            camera = _read_camera_basis(importlib.import_module('export_api').GenerateZcmrFrom3DWindow)
            if camera['right_world'] != CANONICAL_VIEWPOINTS[canonical_view][1] or camera['up_world'] != [0, 1, 0]:
                raise AdapterError('CAMERA_BASIS_CHANGED', 'Reapply the requested Front or Left camera before stepping')
        if before['scene_token'] != token or scene() != current:
            raise AdapterError('STALE_SCENE', 'Scene changed while reading baseline mesh')
        selection = _selection(current, target_uuid)  # Last SDK prefix; no setters/getters inside the native batch.
        response = _invoke(request); effects = response['effects_started']
        if response['status'] != 'SUCCEEDED':
            return response
        if rotation_recovery is not None:
            from .world_rotation_reconciliation import SCHEMA

            evidence = dict(rotation_recovery, schema=SCHEMA, scene_identity=current['identity'], selection=selection,
                            pattern_uuid=target_uuid, group_name=group_name, native_request=dict(request),
                            native=deepcopy(response['result']), before_mesh=_mesh_summary(before),
                            before_world_arrays_sha256=expected_world_arrays_sha256,
                            closed_terminal_arrays_sha256=None, refresh_outcome=None)
        after = _mesh_read()
        if after['scene_identity'] != before['scene_identity'] or after['scene_token'] != token:
            raise AdapterError('SCENE_CHANGED_AFTER_INPUT', 'Project/input/quality changed; inspect the actual result before retrying')
        after_current = scene()
        after_binding = bind_export_labels(after_current, after, target_uuid, group_name)
        measurement = compare_exports(before, after, group_name, companion_groups=companion_groups)
        stable_labels = before_binding['pairs'] == after_binding['pairs'] and before_binding['additional_unclassified_groups'] == after_binding['additional_unclassified_groups']
        readback_safe = stable_labels and measurement['topology_and_index_maps_exact'] and measurement['uv_exact'] and measurement.get('other_export_groups_all_exact') is True
        refresh = None
        if settle_after is not None:
            readback_safe = readback_safe and _outside_group_arrays_exact(before, after, (group_name, *companion_groups))
        elif evidence is not None and readback_safe and _outside_group_arrays_exact(before, after, (group_name,)):
            evidence['closed_terminal_arrays_sha256'] = digest({key: after[key] for key in MESH_FIELDS})
        if readback_safe and settle_after is not None:
            if evidence is not None:
                evidence['refresh_outcome'] = {'status': 'UNKNOWN', 'requires_recovery': True}
            try:
                settlement = settle_after(current, before, after)
            except Exception as error:
                settlement = {'status': 'REJECTED', 'result': None, 'error': {'message': str(error)}}
            if evidence is not None:
                evidence['refresh_outcome'] = {key: value for key, value in settlement.items() if key != 'snapshot'}
                child = settlement.get('result') or {}
                if child.get('transaction_closed') is True and child.get('offset_restored') is True:
                    evidence['closed_terminal_arrays_sha256'] = child.get('restored_world_arrays_sha256')
            if settlement['status'] != 'SUCCEEDED' or settlement.get('requires_recovery') is not False:
                _state()['blocked'] = True
                return held({'status': 'UNKNOWN', 'effects_started': effects, 'requires_recovery': True,
                        'error': {'code': 'ASSIGNED_TOPSTITCH_REFRESH_UNRESOLVED',
                                  'message': (settlement.get('error') or {}).get('message', 'The assigned Topstitch refresh did not close')},
                        'result': {'native': response['result'], 'before_mesh': _mesh_summary(before),
                                   'rotated_mesh': _mesh_summary(after), 'assigned_topstitch_refresh': settlement.get('result')}})
            after, refresh = settlement['snapshot'], settlement['result']
            after_current = scene()
            after_binding = bind_export_labels(after_current, after, target_uuid, group_name)
            measurement = compare_exports(before, after, group_name, companion_groups=companion_groups)
            stable_labels = before_binding['pairs'] == after_binding['pairs'] and before_binding['additional_unclassified_groups'] == after_binding['additional_unclassified_groups']
            readback_safe = stable_labels and measurement['topology_and_index_maps_exact'] and measurement['uv_exact'] and measurement.get('other_export_groups_all_exact') is True
        acceptance = None
        if matrix is not None:
            acceptance = _matrix_readback(before, after, group_name, matrix, tolerance, before_binding, after_binding, measurement,
                                          companion_groups=companion_groups)
            readback_safe = (readback_safe and acceptance['distinct_captures']
                             and acceptance.get('outside_target_export_arrays_exact') is True)
        raw = response['result']
        for key in ('before', 'after'):
            raw[key] = _publish(raw[key], token)
        result = {'verified': readback_safe, 'primitive_dispatch_verified': True, 'native': raw, 'selection': selection,
                  'before_mesh': _mesh_summary(before), 'after_mesh': _mesh_summary(after), 'measurement': measurement,
                  'before_label_binding': before_binding, 'after_label_binding': after_binding,
                  'requested_pattern_UUID_to_exact_group_label_verified': stable_labels,
                  'world_transform_verified': False, 'whole_pattern_world_backend_installed': False,
                  'requested_pattern_UUID_to_readback_group_verified': False}
        if acceptance is not None:
            result['matrix_acceptance'] = acceptance
        if refresh is not None:
            result['assigned_topstitch_refresh'] = refresh
        if camera is not None:
            result['camera_basis_before_input'] = camera
        if not readback_safe:
            _state()['blocked'] = True
        return held({'status': 'SUCCEEDED' if readback_safe else 'PARTIAL', 'result': result,
                'error': None if readback_safe else {'code': 'UNEXPECTED_EXPORTED_MESH_CHANGE', 'message': 'Complete exported topology/UV/non-target group preservation was not established'},
                'effects_started': effects, 'requires_recovery': not readback_safe})
    except Exception as exc:
        if effects:
            _state()['blocked'] = True
        return held(_failure(exc, unknown=effects))


OPERATIONS = {'world_adapter.probe_runtime': runtime_capabilities, 'world_adapter.inspect_3d': inspect_3d,
              'world_adapter.read_camera': read_camera,
              'world_adapter.set_canonical_view': set_canonical_view,
              'world_adapter.reconcile_camera': reconcile_camera,
              'world_adapter.read_group_binding': read_group_binding,
              'world_adapter.inspect_topstitch_assignments': inspect_topstitch_assignments,
              'world_adapter.select_pattern': select_pattern,
              'world_adapter.arrow_step': lambda params: _execute('arrow_step', params),
              'world_adapter.gizmo_drag': lambda params: _execute('gizmo_drag', params)}
