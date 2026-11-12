"""Read the current reviewed Cloth vertex context in the embedded MD callback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib
import inspect as signature_tools
import json
import os
from pathlib import Path
import re
import sys

from md_mcp_plugin.errors import PluginError
from . import canvas, common, selection


SCHEMA = 'md-qt-cloth-vertex-context/1'
DLL_NAME = 'md_qt_cloth_vertex_context.dll'
PIN_SHA256 = '267a606fceda81bb63770e0b2a0aa35cc75a3a4dc47f236d79c659458ea5712f'
PACKAGE_MANIFEST = {
    'schema': 'md-mcp-packaged-cloth-vertex-context/1', 'library': DLL_NAME,
    'abi': 1, 'architecture': 'x64', 'qt_version': '6.10.3',
    'sha256': PIN_SHA256, 'revision': PIN_SHA256,
}
CAPACITY = 1024 * 1024
BUDGET_MS = 750
STATE_KEY = '_md_mcp_cloth_vertex_context_read_state'
ZERO_CALL_FIELDS = ('sdk_vertex_getter_calls', 'model_mutation_calls', 'event_dispatch_calls',
                    'action_trigger_calls', 'focus_change_calls', 'global_input_calls')
_WINDOW_CLASS = re.compile(r'Qt(?:[0-9]{1,12})?QWindow(?:Icon|OwnDC|IconOwnDC)?')
_ROOT_CAPTIONS = frozenset(('Marvelous Designer Personal', 'Marvelous Designer Enterprise', 'Marvelous Designer'))
_SYMBOL_TERMS = re.compile(r'selected|selection|vertex|vertices|mesh|clothpositions', re.IGNORECASE)


def _reject(code, message):
    raise PluginError(code, message, effects_started=False)


def _validate_manifest(manifest):
    if (type(manifest) is not dict or type(manifest.get('abi')) is not int
            or any(manifest.get(k) != v for k, v in PACKAGE_MANIFEST.items())):
        _reject('INVALID_PACKAGED_CLOTH_VERTEX_CONTEXT', 'Expected the reviewed packaged Cloth vertex context ABI and SHA')


def _packaged_dll():
    root = Path(__file__).resolve().parents[1] / 'native' / 'cloth_vertex_context'
    manifest_path = root / 'manifest.json'
    if not manifest_path.is_file():
        _reject('PACKAGED_CLOTH_VERTEX_CONTEXT_MISSING', 'The reviewed Cloth vertex context package is unavailable')
    if root.is_symlink() or manifest_path.is_symlink() or not 0 < manifest_path.stat().st_size <= 16384:
        _reject('INVALID_PACKAGED_CLOTH_VERTEX_CONTEXT', 'Packaged manifest must be a bounded local file')
    _validate_manifest(json.loads(manifest_path.read_text(encoding='utf-8-sig')))
    revision = root / PIN_SHA256
    path = revision / DLL_NAME
    if (revision.is_symlink() or path.is_symlink() or not path.is_file()
            or path.resolve(strict=True) != path):
        _reject('INVALID_PACKAGED_CLOTH_VERTEX_CONTEXT', 'Reviewed DLL must reside in its exact packaged revision directory')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != PIN_SHA256:
            _reject('CLOTH_VERTEX_CONTEXT_DLL_CHANGED', 'The packaged DLL differs from its reviewed bytes')
    return path


def _load(process):
    path = _packaged_dll()
    cache = getattr(sys, STATE_KEY, None)
    if cache is None:
        library = ctypes.CDLL(str(path), winmode=0x900)
        abi = library.mdqt_cloth_vertex_context_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            _reject('CLOTH_VERTEX_CONTEXT_ABI_MISMATCH', 'Expected native Cloth vertex context ABI 1')
        read = library.mdqt_cloth_vertex_context_inspect
        read.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_size_t, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                         ctypes.POINTER(ctypes.c_uint32)]
        read.restype = ctypes.c_int
        cache = {'path': path, 'process': dict(process), 'library': library, 'read': read}
        setattr(sys, STATE_KEY, cache)
    if cache['process'] != process or cache['path'] != path:
        _reject('CLOTH_VERTEX_CONTEXT_PROCESS_MISMATCH', 'Loaded reader belongs to another process lifetime or packaged revision')
    return cache['read']


def _enumerate_qt_windows(pid):
    """Filter this PID's Qt top-level windows by an exact MD root caption."""
    user = ctypes.WinDLL('user32.dll', use_last_error=True, winmode=0x800)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes, user.EnumWindows.restype = [callback_type, wintypes.LPARAM], wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.restype = ctypes.c_int
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    matches = []
    scanned = 0

    @callback_type
    def visit(hwnd, _):
        nonlocal scanned
        scanned += 1
        if scanned > 4096:
            return False
        owner = wintypes.DWORD()
        thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if thread and owner.value == pid:
            buffer = ctypes.create_unicode_buffer(256)
            count = user.GetClassNameW(hwnd, buffer, len(buffer))
            if not 0 < count < len(buffer)-1:
                return False
            if _WINDOW_CLASS.fullmatch(buffer.value):
                caption = ctypes.create_unicode_buffer(96)
                size = user.GetWindowTextW(hwnd, caption, len(caption))
                if 0 < size < len(caption)-1 and caption.value in _ROOT_CAPTIONS:
                    matches.append(int(hwnd))
        return True

    if not user.EnumWindows(visit, 0):
        _reject('QT_WINDOW_ENUMERATION_UNAVAILABLE', 'Current-PID Qt top-level window enumeration did not complete')
    return matches


def _root_window(pid):
    if pid != os.getpid():
        _reject('PROCESS_IDENTITY_MISMATCH', 'The Qt root must belong to the current process')
    matches = _enumerate_qt_windows(pid)
    if len(matches) != 1 or not 0 < matches[0] < 1 << 64:
        _reject('QT_ROOT_WINDOW_AMBIGUOUS', f'Expected one current-PID Qt window with an exact MD root caption, observed {len(matches)}')
    return matches[0]


def _sdk_symbols():
    result = {}
    for name in ('pattern_api', 'utility_api'):
        try:
            symbols = vars(importlib.import_module(name))
        except ImportError:
            result[name] = {'available': False, 'symbols': []}
            continue
        matches = [symbol for symbol in sorted(symbols) if not symbol.startswith('_') and _SYMBOL_TERMS.search(symbol)]
        rows = []
        for symbol in matches[:128]:
            value = symbols[symbol]
            try:
                signature = str(signature_tools.signature(value))[:512]
            except (TypeError, ValueError):
                signature = None
            doc = getattr(value, '__doc__', None)
            rows.append({'name': symbol, 'callable': callable(value), 'signature': signature,
                         'docstring': doc[:1024] if isinstance(doc, str) else None})
        result[name] = {'available': True, 'symbols': rows, 'catalog_truncated': len(matches) > 128}
    return result


def _sdk_selection():
    try:
        value = selection.inspect_selection({})
    except PluginError as exc:
        return {'status': 'UNAVAILABLE', 'error': exc.as_dict(), 'vertex_identity_available': False}
    rows, names = value['selected'], value['selected_names']
    row_names = [row['name'] for row in rows]
    return {'status': 'OBSERVED', 'scene_token': value['scene_token'], 'patterns': rows,
            'selected_names': names, 'names_match_flags': all(isinstance(n, str) for n in row_names)
            and sorted(row_names) == sorted(names), 'vertex_identity_available': False}


def _unavailable(reason):
    return {'status': 'UNAVAILABLE', 'reason': reason, 'native_vertex_index': None,
            'pattern_uuid': None, 'topology_fingerprint': None}


def _validate_receipt(raw, code, process, root):
    expected = 'CONTEXT_OBSERVED_NATIVE_VERTEX_UNAVAILABLE' if code == 0 else 'OBSERVATION_UNAVAILABLE'
    if (type(raw) is not dict or raw.get('schema') != SCHEMA or type(raw.get('return_code')) is not int
            or raw['return_code'] != code or raw.get('status') != expected or raw.get('qt_version') != '6.10.3'
            or type(raw.get('process_id')) is not int or raw['process_id'] != process['pid']
            or raw.get('creation_filetime') != str(process['creation_filetime']) or raw.get('root_window') != str(root)
            or raw.get('context_verified') is not (code == 0) or raw.get('raw_qt_pointers_returned') is not False
            or any(type(raw.get(k)) is not int or raw[k] != 0 for k in ZERO_CALL_FIELDS)):
        _reject('INVALID_CLOTH_VERTEX_CONTEXT_RECEIPT', 'Receipt differs from the reviewed readonly process or ABI contract')
    identity = raw.get('native_vertex_identity')
    if (type(identity) is not dict or type(identity.get('reason')) is not str or not identity['reason']
            or identity != _unavailable(identity['reason'])):
        _reject('INVALID_CLOTH_VERTEX_CONTEXT_RECEIPT', 'Reader cannot establish a native vertex identity')
    if code == 0:
        context = raw.get('context')
        if (raw.get('physical_state_unchanged') is not True or type(context) is not dict
                or context.get('mesh_mode') not in ('MeshBox', 'MovePattern', 'Other')
                or type(context.get('world_coordinate_checked')) is not bool):
            _reject('INVALID_CLOTH_VERTEX_CONTEXT_RECEIPT', 'Stable typed Qt context is missing')
        for key in ('canvas_2d', 'canvas_3d'):
            view = context.get(key)
            if (type(view) is not dict or view.get('view_viewport_scene_bound') is not True
                    or type(view.get('qt_selected_item_count')) is not int or not 0 <= view['qt_selected_item_count'] <= 64
                    or type(view.get('qt_selected_items')) is not list or len(view['qt_selected_items']) != view['qt_selected_item_count']):
                _reject('INVALID_CLOTH_VERTEX_CONTEXT_RECEIPT', 'Bounded current Qt selected item observation is missing')


def _native_receipt(read, process, root):
    output = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = read(process['pid'], process['creation_filetime'], root, BUDGET_MS, output, CAPACITY,
                ctypes.byref(written), ctypes.byref(needed))
    if code in (-1, -2, -3, -4, -5) and written.value == 0:
        return {'status': 'OBSERVATION_UNAVAILABLE', 'return_code': code, 'needed_bytes': needed.value,
                'context_verified': False, 'native_vertex_identity': _unavailable('NATIVE_PREFLIGHT')}
    if (code not in (0, 1) or not 0 < written.value < CAPACITY or needed.value != written.value+1
            or output[written.value] != b'\0'):
        _reject('INVALID_CLOTH_VERTEX_CONTEXT_RECEIPT', 'Native byte count, terminator or return code differs')
    raw = canvas._json(output.raw[:written.value].decode('utf-8'))
    _validate_receipt(raw, code, process, root)
    return {k: value for k, value in raw.items() if k != 'root_window'}


def inspect(params):
    try:
        if (type(params) is not dict or set(params) != {'scene_token'} or type(params['scene_token']) is not str
                or re.fullmatch('[0-9a-f]{64}', params['scene_token']) is None):
            _reject('INVALID_PARAMETERS', 'A current scene_token is the only parameter')
        token = params['scene_token']
        before = common.check_scene_token(token)
        identity = before['identity']
        process = {key: identity[key] for key in ('pid', 'creation_filetime')}
        environment = canvas._environment()
        if (process != environment['process'] or identity.get('md_version') != [2026, 1, 237]
                or environment.get('md_version') != [2026, 1, 237]):
            _reject('CLOTH_VERTEX_CONTEXT_RUNTIME_UNREVIEWED', 'Expected the reviewed MD 2026.1.237 process and Qt runtime')
        root = _root_window(process['pid'])
        sdk_before = _sdk_selection()
        qt = _native_receipt(_load(process), process, root)
        symbols = _sdk_symbols()
        sdk_after = _sdk_selection()
        scene_stable = common.scene() == before
        root_stable = _root_window(process['pid']) == root
        sdk_observed = sdk_before['status'] == 'OBSERVED'
        sdk_consistent = sdk_before == sdk_after and (not sdk_observed or sdk_before['scene_token'] == token)
        observed = scene_stable and root_stable and sdk_consistent and qt['context_verified'] is True
        result = {'verified': observed, 'context_verified': observed, 'scene_token': token, 'scene_identity': identity,
                  'scene_stable': scene_stable, 'root_window_stable': root_stable,
                  'sdk_pattern_selection_stable': sdk_observed and sdk_consistent,
                  'sdk_pattern_selection_corroborated': sdk_observed and sdk_consistent and sdk_before['names_match_flags'],
                  'qt': qt, 'sdk_pattern_selection_before': sdk_before, 'sdk_pattern_selection_after': sdk_after,
                  'sdk_symbol_metadata': symbols, 'native_vertex_identity': qt['native_vertex_identity'],
                  'native_vertex_mapping_verified': False, 'solver_rest_value_read': False}
        error = None if observed else {'code': 'CLOTH_VERTEX_CONTEXT_UNAVAILABLE',
            'message': qt.get('reason', 'Scene, SDK selection, window or native observation did not stay verified')}
        return {'status': 'SUCCEEDED' if observed else 'UNSUPPORTED', 'result': result, 'error': error,
                'effects_started': False, 'requires_recovery': False}
    except Exception as exc:
        error = exc.as_dict() if isinstance(exc, PluginError) else {'code': getattr(exc, 'code', 'CLOTH_VERTEX_CONTEXT_ERROR'), 'message': str(exc)}
        return {'status': 'REJECTED', 'result': {'verified': False, 'context_verified': False,
                'native_vertex_identity': _unavailable(error['code'])}, 'error': error,
                'effects_started': False, 'requires_recovery': False}


OPERATIONS = {
    'cloth_vertex_context.inspect': inspect,
}
