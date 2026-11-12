"""Offline packaging and scene-bound MeshBox provider checks."""
from copy import deepcopy
import json
from pathlib import Path
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import cloth_meshbox_native as native


def _mapping():
    transform = [1, 0, 0, 0, 1, 0, 0, 0, 1]
    return {'viewport_size': [320, 200], 'device_pixel_ratio': 1.0,
            'view_geometry': [0, 0, 320, 200], 'viewport_geometry': [0, 0, 320, 200],
            'view_transform': transform, 'viewport_transform': transform, 'scroll_values': [0, 0],
            'global_origin': [-30000, -30000], 'relative_visible': True, 'enabled': True,
            'drag_mode': 0, 'start_drag_distance': 10}


class MeshBoxNativeTests(unittest.TestCase):
    def setUp(self):
        self.current = {'identity': {'project_path': str(Path.cwd().resolve() / 'another_project.zprj'),
                                     'pid': os.getpid(), 'creation_filetime': 42, 'md_version': [2026, 1, 237]},
                        'scene_token': 'a' * 64, 'patterns': []}
        self.window = 56
        self.ticket = 69
        self.check_scene = self.enterContext(patch.object(native.common, 'check_scene_token', return_value=self.current))
        self.root = self.enterContext(patch.object(native.cloth_vertex_context, '_root_window', return_value=self.window))
        self.enterContext(patch.object(native, 'os', SimpleNamespace(name='nt', getpid=os.getpid)))
        self.enterContext(patch.object(native.Path, 'is_file', return_value=True))
        self.enterContext(patch.object(sys, native._STATE, None, create=True))
        self.functions = {name: object() for name in ('enter', 'restore', 'select')}
        self.load = self.enterContext(patch.object(native, '_load', return_value=self.functions))
        self.mode = self.enterContext(patch.object(native.entry, 'invoke', side_effect=self._mode))
        self.box = self.enterContext(patch.object(native.selector, 'invoke', return_value={
            'status': 'EVENTS_DISPATCHED', 'effects_started': True, 'requires_recovery': False,
        }))

    @staticmethod
    def _mode(_function, command, *_arguments):
        entering = command == 'enter'
        return {'status': 'MESHBOX_VERIFIED' if entering else 'MOVE_RESTORED',
                'transaction_held': entering, 'return_code': 0, 'QAction_trigger_calls': 1}

    def _transition(self, command, *, ticket=None):
        return native.transition(self.current, self.window, self.ticket if ticket is None else ticket, command)

    def _select(self):
        return native.select_box(self.current, self.window, _mapping(), [30, 30], [42, 42])

    def test_current_project_mode_box_restore_calls_original_scope(self):
        native.preflight(self.current, self.window)
        self.assertEqual([call.args[1] for call in self.load.call_args_list], ['entry', 'select'])
        self.assertEqual(self._transition('enter')['status'], 'MESHBOX_VERIFIED')
        self.assertEqual(self._select()['status'], 'EVENTS_DISPATCHED')
        with self.assertRaises(ValueError):
            self._select()
        with self.assertRaises(ValueError):
            self._transition('restore', ticket=70)
        restored = self._transition('restore')
        self.assertEqual(restored['status'], 'MOVE_RESTORED')
        self.assertFalse(restored['sample_transaction_held'])
        self.assertEqual(self.mode.call_args.args[2:], (os.getpid(), 42, self.window, self.ticket, 1500))
        self.box.assert_called_once()

    def test_stale_declared_project_or_window_never_dispatches(self):
        declared = deepcopy(self.current)
        declared['identity']['project_path'] = str(Path.cwd().resolve() / 'stale_project.zprj')
        with self.assertRaises(ValueError):
            native.transition(declared, self.window, self.ticket, 'enter')
        self.root.return_value += 1
        with self.assertRaises(ValueError):
            self._transition('enter')
        self.mode.assert_not_called()
        self.load.assert_not_called()

    def test_unknown_or_stale_after_box_holds_original_transaction(self):
        self._transition('enter')
        self.root.side_effect = [self.window, self.window, self.window + 1]
        result = self._select()
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(result['requires_recovery'])
        self.assertEqual(result['native']['status'], 'EVENTS_DISPATCHED')
        self.root.side_effect = None
        with self.assertRaises(ValueError):
            self._transition('restore')
        with self.assertRaises(ValueError):
            self._transition('enter')
        self.mode.assert_called_once()

    def test_zero_effect_rejection_allows_restore_but_no_second_box(self):
        self._transition('enter')
        self.box.return_value = {'status': 'REJECTED', 'requires_recovery': False, 'effects_started': False}
        self.assertEqual(self._select()['status'], 'REJECTED')
        with self.assertRaises(ValueError):
            self._select()
        self.assertEqual(self._transition('restore')['status'], 'MOVE_RESTORED')


class MeshBoxAssetTests(unittest.TestCase):
    def test_reviewed_package_manifests_and_binary_hashes(self):
        package = Path(native.__file__).resolve().parents[1] / 'native'
        for kind in ('entry', 'select'):
            with self.subTest(kind=kind):
                component = package / f'cloth_meshbox_{kind}'
                manifest = json.loads((component / 'manifest.json').read_text(encoding='utf8'))
                self.assertEqual(manifest['sha256'], native._PACKAGES[kind])
                self.assertEqual(manifest['revision'], manifest['sha256'])
                expected = component / manifest['revision'] / manifest['library']
                if expected.is_file():
                    self.assertEqual(native._packaged_dll(kind), expected)
                else:
                    with self.assertRaises(FileNotFoundError):
                        native._packaged_dll(kind)


if __name__ == '__main__':
    unittest.main()
