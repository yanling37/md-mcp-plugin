"""Set one scoped Pinching Override flag and restore the property page."""
import ctypes
from hashlib import sha256
import json
import re
import sys
from pathlib import Path

from md_mcp_plugin.errors import PluginError
from . import rest_scope, world_frame
from .common import check_scene_token, digest
from .rest_adapter import CAPACITY, _sha
from .rest_selected_trial import current_mesh, selected_owner


LIBRARY = 'md_qt_scoped_pinching_override.dll'
SCHEMA = 'md-qt-scoped-pinching-override/1'
STATE_KEY = '_md_mcp_plugin_scoped_pinching_override_state'
_COUNTERS = ('OS_input_calls', 'focus_calls', 'simulation_calls', 'rest_calls', 'event_pump_calls')
def _state():
    if not hasattr(sys, STATE_KEY):
        setattr(sys, STATE_KEY, {'blocked': False})
    return getattr(sys, STATE_KEY)


def _load(identity):
    directory = Path(__file__).resolve().parents[1] / 'native' / 'rest_scoped_pinching_override'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    pin = manifest.get('sha256')
    if (type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None
            or manifest.get('schema') != 'md-mcp-packaged-rest-scoped-pinching-override/1'
            or manifest.get('library') != LIBRARY or manifest.get('revision') != pin
            or manifest.get('architecture') != 'x64' or manifest.get('qt_version') != '6.10.3'):
        raise ValueError('Invalid scoped Pinching Override manifest')
    binary = directory / pin / LIBRARY
    if binary.is_symlink() or not binary.is_file() or _sha(binary) != pin:
        raise ValueError('Scoped Pinching Override bridge bytes differ')
    state = _state()
    process = (identity['pid'], identity['creation_filetime'])
    if 'call' in state:
        if state['identity'] != (process, pin):
            raise ValueError('Scoped Pinching Override bridge belongs to a different build or process')
        return state['call']
    library = ctypes.CDLL(str(binary), winmode=0x900)
    abi = library.mdqt_scoped_pinching_override_abi_version
    abi.argtypes, abi.restype = [], ctypes.c_uint32
    if abi() != 1:
        raise ValueError('Scoped Pinching Override ABI1 required')
    call = library.mdqt_scoped_pinching_override
    call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
                     ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    call.restype = ctypes.c_int
    state.update(library=library, call=call, identity=(process, pin))
    return call


def _validate(raw, code, identity, enabled):
    if (code not in (0, 1, 2) or raw.get('schema') != SCHEMA or raw.get('return_code') != code
            or raw.get('status') != ('CLOSED', 'REJECTED', 'UNKNOWN')[code]
            or raw.get('process_id') != identity['pid']
            or raw.get('process_creation_filetime') != str(identity['creation_filetime'])
            or raw.get('requested_enabled') is not enabled or raw.get('requires_recovery') is not (code == 2)
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in _COUNTERS)):
        raise ValueError('Scoped Pinching Override receipt has the wrong identity or effect boundary')
    counts = [raw.get(key) for key in ('action_trigger_calls', 'button_click_calls', 'page_restore_calls')]
    if (any(type(count) is not int or count not in (0, 1) for count in counts)
            or raw.get('effects_started') is not any(counts) or (code == 1 and any(counts))):
        raise ValueError('Scoped Pinching Override receipt has inconsistent actions')
    if code != 0:
        return
    if (raw.get('original_page_restored') is not True or raw.get('physical_endpoints_verified') is not True
            or type(raw.get('original_stack')) is not dict or raw['original_stack'] != raw.get('restored_stack')
            or type(raw.get('flag_transition_verified')) is not bool or raw['flag_transition_verified'] is not bool(counts[1])):
        raise ValueError('The original scoped property page or flag transition was not verified')
    if raw.get('setting_error') is None:
        if (type(raw.get('before_checked')) is not bool or raw.get('after_checked') is not enabled
                or raw.get('checked_after_page_restore') is not enabled
                or raw['flag_transition_verified'] is not (raw['before_checked'] is not enabled)):
            raise ValueError('The scoped Pinching Override setting was not read back')
    elif counts[1] or raw['flag_transition_verified']:
        raise ValueError('An unverified Pinching Override click cannot be closed')


def _invoke(call, identity, enabled, budget):
    output = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = call(identity['pid'], identity['creation_filetime'], int(enabled), budget,
                output, CAPACITY, ctypes.byref(written), ctypes.byref(needed))
    if not written.value and code == -1:
        return code, None
    if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
        raise ValueError('Scoped Pinching Override native receipt is incomplete')
    return code, json.loads(output.raw[:written.value])


def _preserved(current, token, before, paper, native_snapshot, *, read_current, read_mesh):
    after = read_mesh(current)
    paper_after = native_snapshot()
    if (read_current(token) != current or before['capture_id'] == after['capture_id']
            or any(before[key] != after[key] for key in world_frame.MESH_FIELDS)
            or paper['fingerprints'] != paper_after['fingerprints'] or paper['rows'] != paper_after['rows']):
        raise ValueError('Scoped flag operation changed the current scene, OBJ or paper inputs')


def set_scoped(params, *, expected_current, pattern_uuid):
    from .rest_pinching_override import _params, _snapshot_provider

    state = _state()
    result = {'verified': False, 'original_page_restored': False, 'solver_rest_write_verified': False}
    effects = False
    try:
        token, enabled, budget = _params(params)
        if state['blocked']:
            raise ValueError('The previous scoped Pinching Override request is unresolved')
        current = check_scene_token(token)
        if current != expected_current or current['identity']['md_version'] != rest_scope._BUILD:
            raise ValueError('The current Rest project or qualified MD build changed')

        def read_selection():
            return selected_owner(current, pattern_uuid, allow_empty=True)

        identity = current['identity']
        selected = read_selection()
        if enabled and not selected:
            raise ValueError('Select the requested current pattern before enabling the scoped flag')
        before = current_mesh(current)
        paper = _snapshot_provider._native_snapshot()
        call = _load(identity)
        effects = True
        code, raw = _invoke(call, identity, enabled, budget)
        if raw is None:
            effects = False
            raise ValueError('Scoped Pinching Override input was rejected before dispatch')
        result['native_setting'] = raw
        _validate(raw, code, identity, enabled)
        effects = raw['effects_started']
        if code:
            raise ValueError(raw.get('reason') or raw.get('setting_error') or 'Scoped Pinching Override not closed')
        _preserved(current, token, before, paper, _snapshot_provider._native_snapshot,
                   read_current=check_scene_token, read_mesh=current_mesh)
        selection_after = read_selection()
        if selection_after and selection_after != selected:
            raise ValueError('Scoped flag operation unexpectedly created an SDK selection')
        result.update(verified=True, scene_token=token, original_page_restored=True, original_ui_value=raw.get('before_checked'),
                      final_ui_value=raw.get('after_checked'), setting_read_back=raw.get('setting_error') is None,
                      flag_transition_verified=raw['flag_transition_verified'], current_obj_and_paper_unchanged=True,
                      sdk_selection_before=selected, sdk_selection_after=selection_after,
                      sdk_selection_cleared=bool(selected and not selection_after))
        return {'status': 'SUCCEEDED', 'result': result, 'error': None,
                'effects_started': effects, 'requires_recovery': False}
    except Exception as error:
        if effects:
            state['blocked'] = True
        return {'status': 'UNKNOWN' if effects else 'REJECTED', 'result': result,
                'error': {'code': 'SCOPED_PINCHING_OVERRIDE_UNVERIFIED', 'message': str(error)},
                'effects_started': effects, 'requires_recovery': effects}
