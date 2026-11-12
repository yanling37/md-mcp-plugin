"""Current-scene Gizmo Scale actions and visual restoration evidence."""

import ctypes
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

from . import world_frame
from .common import check_scene_token, digest, process_identity
from .curve_projection import decode_png
from . import world_ring_observation as rings


SERIES_LIBRARY = 'md_qt_gizmo_scale_v2.dll'
CAPACITY = 262144
STATE_KEY = '_md_mcp_gizmo_scale_state'
def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _state(abi=2):
    if abi != 2:
        raise ValueError('Require Gizmo Scale ABI 2')
    key = f'{STATE_KEY}_v2'
    if not hasattr(sys, key):
        setattr(sys, key, {'configuration': None, 'library': None, 'process': None, 'call': None})
    return getattr(sys, key)


def _hash_matches(path, expected):
    return path.is_file() and not path.is_symlink() and path.resolve(strict=True) == path and sha256(path.read_bytes()).hexdigest() == expected


def install_native_bridge(path, expected_hash, *, abi=2):
    """Configure only from trusted bootstrap. Loading waits for a scoped request."""
    path = Path(path)
    _require(type(expected_hash) is str and re.fullmatch('[0-9a-f]{64}', expected_hash) and path.is_absolute()
             and abi == 2 and path.name == SERIES_LIBRARY
             and _hash_matches(path, expected_hash), 'Require the pinned Gizmo Scale bridge')
    state = _state(abi)
    configuration = (path, expected_hash)
    _require(state['library'] is None or state['configuration'] == configuration, 'Loaded Gizmo Scale bridge cannot be replaced')
    state['configuration'] = configuration


def _load(process, version):
    state = _state(version)
    configuration = state['configuration']
    _require(configuration is not None and _hash_matches(*configuration), 'The pinned Gizmo Scale bridge is unavailable or changed')
    if state['library'] is None:
        library = ctypes.CDLL(str(configuration[0]), winmode=0x900)
        prefix = 'mdqt_gizmo_scale_v2'
        abi = getattr(library, f'{prefix}_abi_version')
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        _require(abi() == version, 'Require the configured Gizmo Scale C ABI')
        call = getattr(library, f'{prefix}_json')
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p,
                         ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        call.restype = ctypes.c_int
        state.update(library=library, process=process, call=call)
    _require(state['process'] == process, 'Gizmo Scale bridge belongs to another process lifetime')
    return state['call']


def _native(operation, *, transaction=None, view=None, abi=2):
    process = process_identity()
    request = {'operation': operation, 'budget_ms': 1500}
    if operation != 'inspect':
        request.update(transaction_id=transaction, native_view_ref=view['native_view_ref'], view_fingerprint=view['view_fingerprint'])
    payload = json.dumps(request, separators=(',', ':')).encode('utf8')
    output = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint(), ctypes.c_uint()
    code = _load(process, abi)(process['pid'], process['creation_filetime'], payload, len(payload), output, CAPACITY,
                               ctypes.byref(written), ctypes.byref(needed))
    _require(code in (0, 1, 2) and 0 < written.value < CAPACITY and needed.value == written.value + 1
             and output[written.value] == b'\0', 'Missing bounded Gizmo Scale native receipt')
    result = json.loads(output.raw[:written.value].decode('utf8'))
    _require(result['schema'] == f'md-qt-gizmo-scale/{abi}' and result['return_code'] == code and result.get('operation') == operation
             and result['process_id'] == process['pid'] and result['process_creation_filetime'] == str(process['creation_filetime']),
             'Gizmo Scale native receipt belongs to a different operation or process')
    counters = result['operation_counts']
    expected_triggers = int(operation in ('increase', 'decrease'))
    _require(counters['QAction_trigger_calls'] in (0, expected_triggers)
             and all(type(value) is int and value == 0 for key, value in counters.items() if key != 'QAction_trigger_calls'),
             'Unexpected input or action calls in the native Gizmo Scale receipt')
    if code == 0:
        _require(result['verified'] is result['physical_state_unchanged'] is True and result['before'] == result['after']
                 and result['requires_recovery'] is False and counters['QAction_trigger_calls'] == expected_triggers
                 and (operation == 'inspect' or result['transaction_id'] == transaction), 'Gizmo Scale native phase did not close as expected')
    return result


def inspect(params):
    """Read the fixed action and menu identities. This operation triggers no action."""
    _require(type(params) is dict and set(params) == {'scene_token'}, 'Use a current scene token')
    current = check_scene_token(params['scene_token'])
    result = _native('inspect', abi=2)
    _require(check_scene_token(params['scene_token']) == current, 'The scene changed while observing Gizmo Scale actions')
    return result


def _frame_bytes(image):
    path = Path(image['path'])
    _require(not path.is_symlink() and path.is_file() and 33 <= path.stat().st_size == image['byte_count'] <= world_frame.CAPACITY,
             'Missing bounded native PNG')
    with path.open('rb') as stream:
        raw = stream.read(world_frame.CAPACITY + 1)
    _require(len(raw) == image['byte_count'] and sha256(raw).hexdigest() == image['sha256'], 'Native PNG bytes differ from the capture')
    return raw


def _capture_frame(current, *, pattern_uuid):
    reply = world_frame.capture({'scene_token': current['scene_token'], 'pattern_uuid': pattern_uuid, 'include_mesh': True})
    _require(reply['status'] == 'SUCCEEDED', f'Current red ring capture failed: {reply.get("error")}')
    frame = reply['result']
    native = frame['native']
    _require(frame['verified'] is True and frame['scene_identity'] == current['identity']
             and frame['scene_token'] == current['scene_token'] and len(frame['selected_names']) == 1
             and all(native.get(key) is True for key in ('verified', 'background_endpoints_verified',
                 'dual_copies_bit_exact_equal_every_pixel', 'dual_baseline_owned_coverage_verified', 'gl_state_restored')),
             'Expected a current red ring capture and preserved native scope')
    proof = frame['mesh_proof']
    target = proof['group_binding']['target']
    _require(target['pattern_uuid'] == pattern_uuid and frame['selected_names'] == [target['group_name']],
             'The fresh ring capture must bind the exact selected panel')
    decoded = decode_png(_frame_bytes(frame['image']))
    return {'frame': frame, 'pixels_sha256': sha256(decoded['pixels']).hexdigest(),
            'world_arrays_sha256': proof['world_arrays_sha256']}, decoded


def _red_observation(state, decoded, roi):
    observed = rings._observe_decoded(decoded, state['frame']['image']['sha256'], roi_px=roi, only_color='red')
    fields = ('status', 'ellipse_center_px', 'ellipse_semiaxes_px', 'dominant_color_support_fraction',
              'support_pixel_bounds_xyxy_inclusive')
    return {'roi_px': observed['roi_px'], 'candidates': [{**{key: candidate[key] for key in fields},
                'ring_support_sha256': digest(candidate['support_runs_y_x0_x1_exclusive'])}
                for candidate in observed['candidates'] if candidate['color_class'] == 'red']}


def _qualified_red(observed):
    red = [candidate for candidate in observed['candidates'] if candidate['status'] == 'IMAGE_CONTOUR_CANDIDATE'
           and candidate['dominant_color_support_fraction'] >= .60]
    return red[0] if len(red) == 1 else None


def _same_frame_scope(a, b):
    keys = ('scene_identity', 'scene_token', 'camera', 'selection', 'view')
    return (a['world_arrays_sha256'] == b['world_arrays_sha256']
            and all(a['frame'][key] == b['frame'][key] for key in keys))


def prove_pair(params, *, operation_id):
    """Run the current-owner scale preview under the caller's mutation journal."""
    from .dispatch import _module

    return _module('gizmo_scale_series').prove(params, operation_id)


def recover_pair(params, *, operation_id):
    from .dispatch import _RECEIPTS, _module

    original = _RECEIPTS.get(params.get('blocked_request_id')) or {}
    return _module('gizmo_scale_series').recover(params, operation_id, original)


OPERATIONS = {'gizmo_scale.inspect': inspect, 'gizmo_scale.prove_pair': prove_pair, 'gizmo_scale.recover_pair': recover_pair}
