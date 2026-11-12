"""Capture and bind the current 3D viewport, camera and retained PNG."""
import base64
import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
from tempfile import mkdtemp

from .common import digest, process_identity, scene
from . import selection, world_adapter

SCHEMA = 'md-qt-world-frame-probe/1'
PACKAGE_SCHEMA = 'md-mcp-packaged-world-frame-probe/1'
LIBRARY = 'md_qt_world_frame_probe_bridge.dll'
CAPACITY = 8 * 1024 * 1024
STATE_KEY = '_md_mcp_plugin_world_frame_state'
MESH_FIELDS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices', 'face_labels')
ZERO_INPUT_COUNTS = ('OS_input_calls', 'focus_calls', 'activation_calls', 'camera_switch_calls', 'model_setter_calls')


def _state():
    if not hasattr(sys, STATE_KEY):
        setattr(sys, STATE_KEY, {'busy': False, 'blocked': False, 'library': None})
    return getattr(sys, STATE_KEY)


def _load(identity):
    directory = Path(__file__).resolve().parents[1] / 'native' / 'world_frame_probe'
    manifest_path = directory / 'manifest.json'
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    pin = manifest.get('sha256')
    if (manifest.get('schema') != PACKAGE_SCHEMA or manifest.get('library') != LIBRARY
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None or manifest.get('revision') != pin):
        raise ValueError('Invalid packaged 3D frame probe manifest')
    path = directory / pin / LIBRARY
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin:
        raise ValueError('3D frame probe bytes do not match the packaged manifest')
    state = _state()
    if state['library'] is None:
        library = ctypes.CDLL(str(path), winmode=0x900)
        version = library.mdqt_world_frame_probe_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 1:
            raise ValueError('3D frame probe ABI 1 required')
        call = library.mdqt_world_frame_probe_json
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, pin=pin, process=identity)
    if state['pin'] != pin or state['process'] != identity:
        raise ValueError('The 3D frame probe is bound to another binary or MD process')
    return state['call']


def _frame_bytes(native, code, identity, view):
    status = native.get('status')
    if (native.get('schema') != SCHEMA or native.get('operation') != 'capture'
            or type(native.get('return_code')) is not int or native['return_code'] != code
            or type(native.get('process_id')) is not int or native['process_id'] != identity['pid']
            or native.get('process_creation_filetime') != str(identity['creation_filetime'])
            or any(type(native.get(key)) is not bool for key in ('verified', 'effects_started', 'requires_recovery'))
            or any(type(native.get(key)) is not int or native[key] != 0 for key in ZERO_INPUT_COUNTS)):
        raise ValueError('Invalid 3D frame probe receipt')
    if status != 'SUCCEEDED':
        if code == 0 or native['verified'] or status not in ('REJECTED', 'PARTIAL', 'UNKNOWN'):
            raise ValueError('Invalid failed 3D frame receipt')
        return None
    if (code != 0 or native['verified'] is not True or native['effects_started'] or native['requires_recovery']
            or any(native.get(key) is not True for key in ('capture_effects_started', 'gl_state_restored',
                                                          'background_endpoints_verified'))):
        raise ValueError('3D framebuffer recovery is unverified')
    fingerprint = view['mapping_fingerprint']
    for key in ('before', 'after'):
        observed = native[key]
        if (observed.get('view') != '3d' or observed.get('mapping_fingerprint') != fingerprint
                or observed.get('scene_item_grabber_absent') is not True):
            raise ValueError('3D view reference changed during frame capture')
    image = native['image']
    viewport = view['mapping']['viewport_size']
    dpr = view['mapping']['device_pixel_ratio']
    if (image.get('format') != 'PNG' or image.get('pixel_origin') != 'top_left_after_CPU_vertical_flip'
            or image.get('viewport_size') != viewport
            or image.get('device_pixel_ratio') != dpr or image.get('mapping_fingerprint') != fingerprint):
        raise ValueError('PNG metadata does not match the bound 3D viewport')
    png = base64.b64decode(native['png_base64'], validate=True)
    if (not 33 <= len(png) <= CAPACITY or png[:16] != b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR'
            or type(image.get('byte_count')) is not int or image['byte_count'] != len(png)
            or image.get('sha256') != hashlib.sha256(png).hexdigest()):
        raise ValueError('3D viewport PNG bytes do not match the native receipt')
    width, height = struct.unpack_from('>II', png, 16)
    physical = [math.floor(size * dpr + 0.5) for size in viewport]
    if ([width, height] != [image.get('width'), image.get('height')] or [width, height] != physical
            or not 0 < width * height <= 4_000_000):
        raise ValueError('3D viewport physical pixel dimensions differ')
    return png


def _failure(status, code, message, *, unknown=False, native=None):
    return {'status': status, 'result': {'verified': False, 'native': native},
            'error': {'code': code, 'message': message}, 'effects_started': unknown, 'requires_recovery': unknown}


def _invoke(view):
    state = _state()
    if state['busy'] or state['blocked']:
        return _failure('REJECTED', 'WORLD_FRAME_RECOVERY_REQUIRED', 'The previous frame operation has not been reconciled'), None
    state['busy'] = True
    attempted = False
    closed = False
    try:
        identity = process_identity()
        call = _load(identity)
        if call is None:
            return _failure('UNSUPPORTED', 'WORLD_FRAME_NOT_PACKAGED', 'The 3D frame probe is not in this build'), None
        request = {'operation': 'capture', 'mapping_fingerprint': view['mapping_fingerprint'], 'budget_ms': 1500}
        payload = json.dumps(request, separators=(',', ':')).encode('utf8')
        output = ctypes.create_string_buffer(CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        attempted = True
        code = call(identity['pid'], identity['creation_filetime'], payload, len(payload), output, CAPACITY,
                    ctypes.byref(written), ctypes.byref(needed))
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise ValueError('3D frame probe returned an incomplete receipt')
        native = json.loads(output.raw[:written.value].decode('utf8'))
        closed = (code == 0 and native.get('schema') == SCHEMA and native.get('operation') == 'capture'
                  and native.get('process_id') == identity['pid']
                  and native.get('process_creation_filetime') == str(identity['creation_filetime'])
                  and native.get('effects_started') is False and native.get('requires_recovery') is False
                  and native.get('gl_state_restored') is True and native.get('background_endpoints_verified') is True
                  and all(type(native.get(k)) is int and native[k] == 0 for k in ZERO_INPUT_COUNTS))
        png = _frame_bytes(native, code, identity, view)
        native.pop('png_base64', None)
        if png is None:
            state['blocked'] = native['requires_recovery'] or native['status'] in ('PARTIAL', 'UNKNOWN')
            if state['blocked']:
                state['blocked_frame_receipt_sha256'] = digest(native)
            return _failure('UNKNOWN' if state['blocked'] else 'REJECTED', native.get('error_code') or 'WORLD_FRAME_NOT_VERIFIED',
                            'See the native frame receipt', unknown=state['blocked'], native=native), None
        return native, png
    except Exception as exc:
        state['blocked'] = attempted and not closed
        return _failure('UNKNOWN' if state['blocked'] else 'REJECTED', 'WORLD_FRAME_RECEIPT_INVALID',
                        str(exc), unknown=state['blocked']), None
    finally:
        state['busy'] = False


def simulation_context_cursor_only_rejection(reply):
    """Identify a read-only simulation sample interrupted by ordinary cursor motion."""
    if (reply.get('status') != 'REJECTED' or reply.get('effects_started') is not False
            or reply.get('requires_recovery') is not False
            or (reply.get('error') or {}).get('code') != 'SIMULATION_CONTEXT_NOT_VERIFIED'):
        return False
    native = reply.get('result') or {}
    before, after = native.get('physical_before'), native.get('physical_after')
    return (native.get('reason') == 'MINIMIZED_PHYSICAL_STATE_CHANGED'
            and type(before) is dict and type(after) is dict
            and before.get('cursor_valid') is True and after.get('cursor_valid') is True
            and type(before.get('cursor')) is list and type(after.get('cursor')) is list
            and before['cursor'] != after['cursor'] and dict(before, cursor=after['cursor']) == after)


def _require_stopped_simulation(token):
    from .dispatch import _module

    _module('native_setup').configure_rest_simulation_context()
    inspect = _module('rest_simulation_context').inspect
    for _ in range(3):
        reply = inspect({'scene_token': token})
        if not simulation_context_cursor_only_rejection(reply):
            break
    result = reply.get('result') or {}
    actions = [action for action in result.get('actions', ()) if action.get('object_name') == 'action_SimStop']
    if (reply['status'] != 'SUCCEEDED' or result.get('catalog_complete') is not True or len(actions) != 1
            or actions[0].get('reference_unique') is not True or actions[0].get('checked') is not False):
        error = reply.get('error') or {}
        diagnostic = result.get('unverified_native_diagnostic') or {}
        reason = diagnostic.get('reason') or result.get('reason') or error.get('message') or 'Action readback differs'
        checked = actions[0].get('checked') if len(actions) == 1 else None
        raise ValueError(f'Verify the current Simulate action is off before exporting the frame mesh '
                         f'({reply["status"]}, {error.get("code", "ACTION_NOT_OFF")}, '
                         f'{str(reason)[:300]}, checked={checked!r})')


def capture(params):
    try:
        current = scene()
        if set(params) - {'scene_token', 'pattern_uuid', 'include_mesh'} or params.get('scene_token') != current['scene_token']:
            return _failure('REJECTED', 'STALE_SCENE', 'Read the current scene token')
        include_mesh = params.get('include_mesh', False)
        if type(include_mesh) is not bool:
            return _failure('REJECTED', 'INVALID_PARAMETERS', 'include_mesh must be a boolean')
        camera_params = {'scene_token': current['scene_token']}
        selected_before = selection.inspect_selection({})
        target = None
        if 'pattern_uuid' in params:
            target = world_adapter._text(params['pattern_uuid'], 'pattern_uuid')
            world_adapter._selection(current, target)
        if include_mesh:
            _require_stopped_simulation(current['scene_token'])
            mesh_before = world_adapter._mesh_read()
        first = world_adapter.read_camera(camera_params)
        if first['status'] != 'SUCCEEDED':
            return first
        before = first['result']
        native, png = _invoke(before['view'])
        if png is None:
            return native
        last = world_adapter.read_camera(camera_params)
        if last['status'] != 'SUCCEEDED':
            return last
        after = last['result']
        mesh_proof = None
        if include_mesh:
            mesh_after = world_adapter._mesh_read()
            if (mesh_before['capture_id'] == mesh_after['capture_id'] or any(
                    snapshot['scene_token'] != current['scene_token'] or snapshot['scene_identity'] != current['identity']
                    for snapshot in (mesh_before, mesh_after))
                    or any(mesh_before[key] != mesh_after[key] for key in MESH_FIELDS)):
                return _failure('REJECTED', 'MESH_CHANGED_DURING_FRAME', 'The world OBJ changed across the captured frame')
            mesh_proof = {'before': world_adapter._mesh_summary(mesh_before), 'after': world_adapter._mesh_summary(mesh_after),
                          'world_arrays_sha256': digest({key: mesh_before[key] for key in MESH_FIELDS})}
            if target is not None:
                _, group = world_adapter._target(current, target)
                mesh_proof['group_binding'] = world_adapter.bind_export_labels(current, mesh_before, target, group)
        if (scene() != current or selected_before != selection.inspect_selection({})
                or before['camera'] != after['camera'] or any(before['view'][key] != after['view'][key]
                for key in ('view_ref', 'mapping_fingerprint', 'view_fingerprint'))):
            return _failure('REJECTED', 'CAMERA_CHANGED_DURING_FRAME', 'The scene, selection, camera or view changed during frame capture')
        path = Path(mkdtemp(prefix='md-mcp-world-frame-')) / 'viewport.png'
        with path.open('xb') as stream:
            stream.write(png)
        image = {**native['image'], 'path': str(path), 'pixel_origin': 'top_left'}
        return {'status': 'SUCCEEDED', 'result': {'verified': True, 'scene_token': current['scene_token'],
                'scene_identity': current['identity'], 'camera': after['camera'], 'view': after['view'],
                'selection': selected_before['selected'], 'selected_names': selected_before['selected_names'],
                'image': image, 'native': native, 'mesh_proof': mesh_proof},
                'error': None, 'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure('REJECTED', 'WORLD_FRAME_CAPTURE_REJECTED', str(exc))


def _read_retained_frame(request_id, token):
    from . import dispatch

    if type(request_id) is not str or re.fullmatch('[0-9a-f]{32}', request_id) is None:
        raise ValueError('Expected a captured frame request ID')
    current = scene()
    receipt = dispatch._RECEIPTS.get(request_id) or {}
    frame = receipt.get('result') or {}
    native = frame.get('native') or {}
    if (receipt.get('operation_id') != request_id or receipt.get('status') != 'SUCCEEDED'
            or receipt.get('requires_recovery') is not False or frame.get('verified') is not True
            or native.get('schema') != SCHEMA or native.get('operation') != 'capture'
            or frame.get('scene_identity') != current['identity']
            or frame.get('scene_token') != current['scene_token'] or token != current['scene_token']):
        raise ValueError('The frame is not a verified capture of the current scene')
    return frame, _verified_png(frame)


def _verified_png(frame):
    image = frame['image']
    path = Path(image['path'])
    if path.is_symlink() or not path.is_file() or not 33 <= path.stat().st_size == image['byte_count'] <= CAPACITY:
        raise ValueError('The retained frame PNG is unavailable or changed')
    png = path.read_bytes()
    if hashlib.sha256(png).hexdigest() != image['sha256']:
        raise ValueError('The retained frame PNG hash differs')
    return png


def observe_rings(params):
    from . import world_ring_observation

    try:
        if set(params) - {'frame_request_id', 'scene_token', 'roi_px'} or not {'frame_request_id', 'scene_token'} <= set(params):
            raise ValueError('Use a current scene token and a completed frame request')
        request_id = params['frame_request_id']
        frame, png = _read_retained_frame(request_id, params['scene_token'])
        observed = world_ring_observation.observe_rings(png, roi_px=params.get('roi_px'),
                                                        expected_png_sha256=frame['image']['sha256'])
        observed.update(verified=True, frame_request_id=request_id, scene_token=frame['scene_token'],
                        view_ref=frame['view']['view_ref'], view_fingerprint=frame['view']['view_fingerprint'],
                        camera_cmt_sha256=frame['camera']['cmt_sha256'], selection=frame.get('selection', []))
        return {'status': 'SUCCEEDED', 'result': observed, 'error': None,
                'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure('REJECTED', 'WORLD_RING_OBSERVATION_REJECTED', str(exc))


def calibrate_outline(params):
    from . import mesh, world_outline_calibration

    try:
        if set(params) != {'frame_request_id', 'scene_token', 'focal_seed_px'}:
            raise ValueError('Use the frame request, its scene token and a physical-pixel focal seed')
        frame, png = _read_retained_frame(params['frame_request_id'], params['scene_token'])
        proof = frame.get('mesh_proof') or {}
        target = (proof.get('group_binding') or {}).get('target') or {}
        selected = frame.get('selection') or []
        if len(selected) != 1 or selected[0]['pattern_uuid'] != target.get('pattern_uuid'):
            raise ValueError('Capture a single selected piece with include_mesh before calibrating')
        retained = proof['before']['export']['obj_asset']
        obj, asset = mesh._asset(Path(retained['path']))
        if asset != retained:
            raise ValueError('The retained world OBJ bytes changed')
        arrays = mesh.parse_obj(obj)
        if digest({key: arrays[key] for key in MESH_FIELDS}) != proof['world_arrays_sha256']:
            raise ValueError('The retained world OBJ does not match the frame geometry')
        group = mesh.extract_group(arrays, target['group_name'])
        pose = frame['camera']['projection_pose']
        result = world_outline_calibration.calibrate_selected_outline(pose, group, png, params['focal_seed_px'])
        result.update(frame_request_id=params['frame_request_id'], scene_token=frame['scene_token'],
                      pattern_uuid=target['pattern_uuid'], view_ref=frame['view']['view_ref'],
                      view_fingerprint=frame['view']['view_fingerprint'], png_sha256=frame['image']['sha256'],
                      camera_cmt_sha256=frame['camera']['cmt_sha256'],
                      world_arrays_sha256=proof['world_arrays_sha256'])
        return {'status': 'SUCCEEDED' if result['verified'] else 'REJECTED', 'result': result,
                'error': None if result['verified'] else {'code': 'OUTLINE_HOLDOUT_NOT_VERIFIED',
                                                         'message': 'Current outline did not pass the independent holdout'},
                'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        return _failure('REJECTED', 'WORLD_OUTLINE_CALIBRATION_REJECTED', str(exc))


OPERATIONS = {'world_frame.capture': capture, 'world_frame.observe_rings': observe_rings,
              'world_frame.calibrate_outline': calibrate_outline}
