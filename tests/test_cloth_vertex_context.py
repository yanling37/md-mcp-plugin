"""Offline product wrapper checks with explicit SDK/window/ABI stand-ins."""
import ctypes
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from md_mcp_plugin.embedded import cloth_vertex_context as probe
from md_mcp_plugin.errors import PluginError
from md_mcp_plugin.features import cloth_vertex_context as feature


TOKEN = 'a'*64
PROCESS = {'pid': os.getpid(), 'creation_filetime': 1234}
SCENE = {'scene_token': TOKEN, 'identity': {**PROCESS, 'md_version': [2026, 1, 237], 'project_path': 'current.zprj'}}
ROOT_WINDOW = 5001
SELECTION = {'scene_token': TOKEN, 'selected_names': ['paper'],
             'selected': [{'pattern_uuid': 'paper-id', 'index': 0, 'name': 'paper', 'selected': True}]}


def receipt(**changed):
    view = dict(view_viewport_scene_bound=True, qt_selected_item_count=0, qt_selected_items=[])
    raw = dict(schema=probe.SCHEMA, return_code=0, status='CONTEXT_OBSERVED_NATIVE_VERTEX_UNAVAILABLE',
        process_id=PROCESS['pid'], creation_filetime=str(PROCESS['creation_filetime']), root_window=str(ROOT_WINDOW),
        qt_version='6.10.3', context_verified=True, physical_state_unchanged=True, raw_qt_pointers_returned=False,
        context=dict(mesh_mode='MeshBox', world_coordinate_checked=True, canvas_2d=view, canvas_3d=view),
        native_vertex_identity=probe._unavailable('NO_QT_SELECTED_ITEMS_EXPOSED'),
        **{key: 0 for key in probe.ZERO_CALL_FIELDS})
    raw.update(changed)
    return raw


def abi_read(raw):
    encoded = json.dumps(raw).encode()
    def read(pid, creation, hwnd, budget, output, capacity, written, needed):
        if (pid, creation, hwnd, budget, capacity) != (PROCESS['pid'], PROCESS['creation_filetime'],
                                                        ROOT_WINDOW, probe.BUDGET_MS, probe.CAPACITY):
            raise AssertionError('ABI fields differ')
        ctypes.memmove(output, encoded+b'\0', len(encoded)+1)
        written._obj.value, needed._obj.value = len(encoded), len(encoded)+1
        return raw['return_code']
    return Mock(side_effect=read)


class ClothVertexContextTests(unittest.TestCase):
    def observe(self, *, sdk_after=SELECTION):
        read = abi_read(receipt())
        symbol = Mock(side_effect=AssertionError('Metadata must never call the vertex symbol'))
        module = SimpleNamespace(GetClothPositions=symbol)
        with patch.object(probe.common, 'check_scene_token', return_value=SCENE), \
             patch.object(probe.common, 'scene', return_value=SCENE), \
             patch.object(probe.canvas, '_environment', return_value={'process': PROCESS, 'md_version': [2026, 1, 237]}), \
             patch.object(probe, '_enumerate_qt_windows', return_value=[ROOT_WINDOW]), \
             patch.object(probe, '_load', return_value=read), \
             patch.object(probe.selection, 'inspect_selection', side_effect=[SELECTION, sdk_after]), \
             patch.object(probe.importlib, 'import_module', return_value=module):
            result = probe.inspect({'scene_token': TOKEN})
        read.assert_called_once()
        symbol.assert_not_called()
        return result

    def test_real_buffer_shape_and_bracketed_observation(self):
        result = self.observe()
        self.assertEqual('SUCCEEDED', result['status'], result)
        self.assertTrue(result['result']['context_verified'])
        self.assertTrue(result['result']['sdk_pattern_selection_corroborated'])
        self.assertEqual('UNAVAILABLE', result['result']['native_vertex_identity']['status'])
        self.assertIsNone(result['result']['native_vertex_identity']['native_vertex_index'])
        self.assertNotIn('root_window', result['result']['qt'])
        self.assertFalse(result['effects_started'])
        self.assertFalse(result['requires_recovery'])

    def test_selection_drift_is_unverified(self):
        result = self.observe(sdk_after={**SELECTION, 'selected': [], 'selected_names': []})
        self.assertEqual('UNSUPPORTED', result['status'])
        self.assertFalse(result['result']['context_verified'])
        self.assertFalse(result['effects_started'])

    def test_no_native_identity_claim_effect_counter_or_wrong_window(self):
        for change in (dict(model_mutation_calls=False), dict(event_dispatch_calls=1),
                       dict(root_window='7002'),
                       dict(native_vertex_identity={**probe._unavailable('NATIVE_PREFLIGHT'), 'native_vertex_index': 9})):
            with self.subTest(change=change), self.assertRaises(PluginError):
                probe._native_receipt(abi_read(receipt(**change)), PROCESS, ROOT_WINDOW)

    def test_public_readonly_params_and_ambiguous_window_gate(self):
        registered = {}
        def tool():
            def register(function):
                registered[function.__name__] = function
                return function
            return register
        context = SimpleNamespace(call=Mock(return_value={}))
        feature.register(SimpleNamespace(tool=tool), context)
        registered['cloth_vertex_context_inspect'](TOKEN)
        context.call.assert_called_once_with('cloth_vertex_context.inspect', {'scene_token': TOKEN}, scene_token=TOKEN)
        with patch.object(probe, '_load') as load:
            rejected = probe.inspect({'scene_token': TOKEN, 'root_window': ROOT_WINDOW})
        self.assertEqual('INVALID_PARAMETERS', rejected['error']['code'])
        load.assert_not_called()
        with patch.object(probe, '_enumerate_qt_windows', return_value=[1, 2]), self.assertRaises(PluginError):
            probe._root_window(PROCESS['pid'])

    def test_enumeration_reads_captions_only_for_current_process_qt_candidates(self):
        pid = PROCESS['pid']
        windows = {ROOT_WINDOW: (pid, 'Qt6103QWindowIcon', 'Marvelous Designer Personal'),
                   5002: (pid, 'Qt6103QWindowIcon', 'Auxiliary dialog'),
                   5003: (pid+1, 'Qt6103QWindowIcon', 'Marvelous Designer Personal'),
                   5004: (pid, 'DifferentClass', 'Marvelous Designer Personal')}
        def enumerate_windows(callback, data):
            return all(callback(hwnd, data) for hwnd in windows)
        def read_owner(hwnd, output):
            output._obj.value = windows[hwnd][0]
            return 1
        def read_text(index):
            def read(hwnd, buffer, capacity):
                value = windows[hwnd][index]
                buffer.value = value[:capacity-1]
                return len(buffer.value)
            return read
        user = SimpleNamespace(EnumWindows=Mock(side_effect=enumerate_windows),
            GetWindowThreadProcessId=Mock(side_effect=read_owner),
            GetClassNameW=Mock(side_effect=read_text(1)), GetWindowTextW=Mock(side_effect=read_text(2)))
        callback_type = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)
        with patch.object(ctypes, 'WinDLL', return_value=user, create=True), \
             patch.object(ctypes, 'WINFUNCTYPE', callback_type, create=True):
            self.assertEqual([ROOT_WINDOW], probe._enumerate_qt_windows(pid))
        self.assertEqual([ROOT_WINDOW, 5002], [call.args[0] for call in user.GetWindowTextW.call_args_list])


if __name__ == '__main__':
    unittest.main()
