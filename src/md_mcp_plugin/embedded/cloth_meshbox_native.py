"""Fixed native MeshBox controls for one current-project transaction in MD's GUI callback."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import sys

from ..native.cloth_meshbox_entry import binding as entry
from ..native.cloth_meshbox_select import binding as selector
from . import cloth_vertex_context, common


_PACKAGES = {
    'entry': '7c79f6c7f798e89cf9271d94d9c744abc7278e0eb2a0d59f9482a96eaaaa98a7',
    'select': '21f9c4fab4ada626935fa69796b4f3ee21535078e46e485a95cafe03d3fede48',
}
_STATE = '_md_mcp_cloth_meshbox_native_v1'
_PROCESS_ARGS = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_size_t]
_BUDGET_ARG = [ctypes.c_uint32]
_OUTPUT_ARGS = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
_SELECT_ARGS = [ctypes.c_int32] * 4 + [ctypes.c_char_p, ctypes.c_uint32]


def _bounded_integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def check_scope(current_scene, root_window):
    """Require the same saved project, live SDK scene, process lifetime and unique root."""
    if type(current_scene) is not dict or not _bounded_integer(root_window, 1, (1 << 64) - 1):
        raise ValueError('Expected the current MD scene and its exact root window')
    token, identity = current_scene.get('scene_token'), current_scene.get('identity')
    if type(token) is not str or re.fullmatch('[0-9a-f]{64}', token) is None or type(identity) is not dict:
        raise ValueError('Expected a current scene token and declared project identity')
    pid, creation = identity.get('pid'), identity.get('creation_filetime')
    project = identity.get('project_path')
    if (os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8 or identity.get('md_version') != [2026, 1, 237]
            or not _bounded_integer(pid, 1, (1 << 32) - 1) or pid != os.getpid()
            or not _bounded_integer(creation, 1, (1 << 64) - 1) or type(project) is not str):
        raise ValueError('Expected the reviewed 64-bit Windows MD 2026.1.237 process lifetime')
    path = Path(project)
    if not path.is_absolute() or path.suffix.lower() != '.zprj' or not path.is_file():
        raise ValueError('Expected the absolute saved project currently open in MD')
    if common.check_scene_token(token) != current_scene or cloth_vertex_context._root_window(pid) != root_window:
        raise ValueError('Current project, SDK scene or root window changed')
    return token, project, pid, creation, root_window


def _state(scope):
    lifetime = scope[2:4]
    state = getattr(sys, _STATE, None)
    if state is None:
        state = {'lifetime': lifetime, 'loaded': {}, 'transaction': None}
        setattr(sys, _STATE, state)
    if state['lifetime'] != lifetime:
        raise ValueError('Loaded MeshBox providers belong to another process lifetime')
    return state


def _verify_dll(path, expected):
    if (not path.is_absolute() or path.is_symlink() or path.resolve(strict=True) != path
            or not path.is_file() or not 0 < path.stat().st_size <= 4 * 1024 * 1024):
        raise ValueError('Expected a bounded direct reviewed MeshBox DLL')
    with path.open('rb') as stream:
        if file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('MeshBox DLL SHA-256 differs from the reviewed revision')


def _packaged_dll(kind):
    expected = _PACKAGES[kind]
    component = f'cloth_meshbox_{kind}'
    root = Path(__file__).resolve().parents[1] / 'native' / component
    manifest = root / 'manifest.json'
    library = f'md_qt_{component}.dll'
    reference = {'schema': f'md-mcp-packaged-cloth-meshbox-{kind}/1', 'architecture': 'x64',
                 'qt_version': '6.10.3', 'abi': 1, 'library': library, 'revision': expected, 'sha256': expected}
    if (manifest.is_symlink() or manifest.resolve(strict=True) != manifest or not manifest.is_file()
            or not 0 < manifest.stat().st_size <= 16384):
        raise ValueError('Expected a bounded direct MeshBox package manifest')
    metadata = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if type(metadata) is not dict or type(metadata.get('abi')) is not int or metadata != reference:
        raise ValueError('MeshBox manifest does not identify the exact reviewed ABI and SHA-256')
    path = root / expected / library
    _verify_dll(path, expected)
    return path


def _resident_module(basename):
    """Find one copy in this process to preserve the native transaction and input latches."""
    kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
    current = kernel.GetCurrentProcess
    current.argtypes, current.restype = [], wintypes.HANDLE
    modules = (wintypes.HMODULE * 2048)()
    used = wintypes.DWORD()
    enumerate_modules = kernel.K32EnumProcessModules
    enumerate_modules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                 ctypes.POINTER(wintypes.DWORD)]
    enumerate_modules.restype = wintypes.BOOL
    module_size = ctypes.sizeof(wintypes.HMODULE)
    if (not enumerate_modules(current(), modules, ctypes.sizeof(modules), ctypes.byref(used))
            or not 0 < used.value <= ctypes.sizeof(modules) or used.value % module_size):
        raise ValueError('Current process module enumeration did not complete')
    filename = kernel.GetModuleFileNameW
    filename.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    filename.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    matches = []
    for module in modules[:used.value // module_size]:
        length = filename(module, buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise ValueError('Current process module path is unavailable')
        path = Path(buffer.value)
        if path.name.casefold() == basename.casefold():
            matches.append((module, path))
    if len(matches) > 1:
        raise ValueError('Multiple MeshBox DLL copies are resident in this process')
    return matches[0] if matches else None


def _load(state, kind):
    packaged = _packaged_dll(kind)
    resident = _resident_module(packaged.name)
    cached = state['loaded'].get(kind)
    if cached is not None:
        if resident != cached['resident']:
            raise ValueError('The resident reviewed MeshBox module changed')
        return cached['functions']

    path = resident[1] if resident is not None else packaged
    if path != packaged:
        _verify_dll(path, _PACKAGES[kind])
    library = ctypes.CDLL(str(path), winmode=0x900)
    actual = _resident_module(packaged.name)
    if actual != (library._handle, path):
        raise ValueError('Native MeshBox DLL did not load from the verified path')
    abi = getattr(library, f'mdqt_cloth_meshbox_{kind}_abi_version')
    abi.argtypes, abi.restype = [], ctypes.c_uint32
    if abi() != 1:
        raise ValueError('Loaded MeshBox provider does not report ABI 1')
    functions = {}
    for command in (('enter', 'restore') if kind == 'entry' else ('select',)):
        function = getattr(library, f'mdqt_cloth_meshbox_{command}')
        inputs = [ctypes.c_uint64] + _BUDGET_ARG if kind == 'entry' else _BUDGET_ARG + _SELECT_ARGS
        function.argtypes, function.restype = _PROCESS_ARGS + inputs + _OUTPUT_ARGS, ctypes.c_int
        functions[command] = function
    state['loaded'][kind] = {'library': library, 'resident': actual, 'functions': functions}
    return functions


def preflight(current_scene, root_window):
    """Verify both packaged assets and bind their fixed CABI before any mode change."""
    scope = check_scope(current_scene, root_window)
    state = _state(scope)
    if state['transaction'] is not None:
        raise ValueError('The original MeshBox transaction is still held')
    _load(state, 'entry')
    _load(state, 'select')
    check_scope(current_scene, root_window)


def transition(current_scene, root_window, ticket, command, *, budget_ms=1500):
    """Enter or restore the fixed MeshBox mode with the original scene, window and ticket."""
    if (command not in ('enter', 'restore') or not _bounded_integer(ticket, 1, (1 << 64) - 1)
            or not _bounded_integer(budget_ms, 50, 1500)):
        raise ValueError('Expected a fixed transition, nonzero ticket and bounded budget')
    scope = check_scope(current_scene, root_window)
    token, _, pid, creation, hwnd = scope
    state = _state(scope)
    transaction = state['transaction']
    entering = command == 'enter'
    if entering and transaction is not None:
        raise ValueError('The original MeshBox transaction is still held')
    if not entering and (transaction is None or transaction['binding'] != (scope, ticket)
                         or transaction['selector_unresolved']):
        raise ValueError('Restore requires the original scope and ticket with a closed selector')
    function = _load(state, 'entry')[command]
    check_scope(current_scene, root_window)
    if entering:
        transaction = {'binding': (scope, ticket), 'entry_verified': False,
                       'selector_attempted': False, 'selector_unresolved': False}
        state['transaction'] = transaction
    raw = None
    try:
        raw = entry.invoke(function, command, pid, creation, hwnd, ticket, budget_ms)
        check_scope(current_scene, root_window)
        if entering:
            transaction['entry_verified'] = raw['status'] == 'MESHBOX_VERIFIED'
            if raw['status'] == 'REJECTED':
                state['transaction'] = None
        elif raw['return_code'] == 0:
            state['transaction'] = None
        return {'status': raw['status'], 'ticket': str(ticket), 'scene_token': token, 'scene_stable': True,
                'sample_transaction_held': state['transaction'] is not None, 'native': raw}
    except Exception as exc:
        return {'status': 'UNKNOWN', 'ticket': str(ticket), 'scene_token': token, 'scene_stable': False,
                'sample_transaction_held': True, 'native': raw, 'reason': str(exc)}


def select_box(current_scene, root_window, view_mapping, start, end, *, budget_ms=1500):
    """Dispatch one closed fixed-profile box, with actual selection verified by the caller."""
    if not _bounded_integer(budget_ms, 50, 1500):
        raise ValueError('Expected a bounded selector budget')
    mapping, start, end = selector.box_mapping(view_mapping, start, end)
    scope = check_scope(current_scene, root_window)
    _, _, pid, creation, hwnd = scope
    state = _state(scope)
    transaction = state['transaction']
    if (transaction is None or transaction['binding'][0] != scope or not transaction['entry_verified']
            or transaction['selector_attempted']):
        raise ValueError('One box is allowed in the original verified current-project MeshBox transaction')
    function = _load(state, 'select')['select']
    check_scope(current_scene, root_window)
    transaction.update(selector_attempted=True, selector_unresolved=True)
    raw = None
    try:
        raw = selector.invoke(function, pid, creation, hwnd, mapping, start, end, budget_ms)
        check_scope(current_scene, root_window)
        transaction['selector_unresolved'] = raw['requires_recovery']
        return raw
    except Exception as exc:
        return {'schema': selector.SCHEMA, 'status': 'UNKNOWN', 'reason': 'SELECTOR_SCOPE_UNVERIFIED',
                'message': str(exc), 'effects_started': True, 'effects_may_have_started': True,
                'requires_recovery': True, 'native': raw}
