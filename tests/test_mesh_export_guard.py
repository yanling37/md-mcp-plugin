"""Exercise the MD OBJ activation fence without installing a Windows hook."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from md_mcp_plugin.embedded import mesh_export_guard as fence
from md_mcp_plugin.errors import PluginError


class MeshExportGuardTests(unittest.TestCase):
    def setUp(self):
        self.foreground = 100
        self.events = []
        self.switch_key = 0
        self.callback = None
        self.user = SimpleNamespace(
            GetWindowThreadProcessId=Mock(side_effect=self.window_thread),
            GetForegroundWindow=Mock(side_effect=lambda: self.foreground),
            GetAsyncKeyState=Mock(side_effect=lambda key: 0x8000 if key == self.switch_key else 0),
            SetWindowsHookExW=Mock(side_effect=self.install),
            UnhookWindowsHookEx=Mock(side_effect=self.detach),
            CallNextHookEx=Mock(return_value=42),
        )
        self.kernel = SimpleNamespace(GetCurrentThreadId=Mock(return_value=456))
        self.scope = SimpleNamespace()
        self.enterContext(patch.object(fence, 'os', SimpleNamespace(name='nt')))
        self.enterContext(patch.object(fence, 'sys', self.scope))
        self.enterContext(patch.object(fence, '_root_window', return_value=200))
        self.enterContext(patch.object(ctypes, 'WinDLL', create=True,
            side_effect=lambda name, **kw: self.user if name == 'user32.dll' else self.kernel))
        self.enterContext(patch.object(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True))
        self.enterContext(patch.object(ctypes, 'set_last_error', create=True))
        self.enterContext(patch.object(ctypes, 'get_last_error', create=True, return_value=5))

    def window_thread(self, hwnd, pid):
        thread, owner = {100: (789, 999), 200: (456, 123), 201: (457, 123)}[hwnd]
        pid._obj.value = owner
        return thread

    def install(self, kind, callback, module, thread):
        self.events.append('install')
        self.assertEqual((kind, module, thread), (5, None, 456))
        self.callback = callback
        return 500

    def detach(self, handle):
        self.events.append('detach')
        self.assertEqual(handle, 500)
        self.callback = None
        return True

    def test_export_scope_vetoes_programmatic_activation_and_passes_user_events(self):
        def export(path, option):
            self.events.append('export')
            event = fence._Activate(False, 100)
            self.assertEqual(self.callback(5, 200, ctypes.addressof(event)), 1)
            self.assertEqual(self.callback(5, 200, 0), 1)
            self.assertEqual(self.callback(5, 201, ctypes.addressof(event)), 42)
            self.assertEqual(self.callback(5, 100, ctypes.addressof(event)), 42)
            self.assertEqual(self.callback(-1, 200, ctypes.addressof(event)), 42)
            event.fMouse = True
            self.assertEqual(self.callback(5, 200, ctypes.addressof(event)), 42)
            event.fMouse = False
            self.switch_key = 0x12
            self.assertEqual(self.callback(5, 200, ctypes.addressof(event)), 42)
            return [path]

        result, receipt = fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertEqual(result, ['mesh.obj'])
        self.assertEqual(self.events, ['install', 'export', 'detach'])
        self.assertEqual(receipt, {
            'schema': 'md-obj-export-activation-guard/1', 'applicable': True,
            'process_id': 123, 'gui_thread_id': 456, 'installed': True, 'detached': True,
            'background_at_start': True, 'foreground_pid_before': 999, 'foreground_pid_after': 999,
            'programmatic_activations_vetoed': 2, 'user_mouse_activations_passed': 1,
            'user_switch_key_activations_passed': 1,
        })

    def test_wrong_thread_and_install_failure_never_call_export(self):
        export = Mock()
        self.kernel.GetCurrentThreadId.return_value = 457
        with self.assertRaises(PluginError) as raised:
            fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertEqual(raised.exception.code, 'OBJ_EXPORT_ACTIVATION_THREAD_MISMATCH')
        self.user.SetWindowsHookExW.assert_not_called()
        self.kernel.GetCurrentThreadId.return_value = 456
        self.user.SetWindowsHookExW.side_effect = None
        self.user.SetWindowsHookExW.return_value = None
        with self.assertRaises(PluginError) as raised:
            fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertEqual(raised.exception.code, 'OBJ_EXPORT_ACTIVATION_INSTALL_FAILED')
        self.user.UnhookWindowsHookEx.assert_not_called()
        export.assert_not_called()

    def test_export_exception_still_detaches(self):
        with self.assertRaisesRegex(RuntimeError, 'export failed'):
            fence.guarded_obj_export(Mock(side_effect=RuntimeError('export failed')), 'mesh.obj', object(), 123)
        self.assertEqual(self.events, ['install', 'detach'])
        self.assertIsNone(self.callback)

    def test_unexplained_foreground_change_cannot_return_a_mesh_success(self):
        def export(path, option):
            self.foreground = 200
            return [path]

        with self.assertRaises(PluginError) as raised:
            fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertEqual(raised.exception.code, 'OBJ_EXPORT_ACTIVATION_FOREGROUND_CHANGED')
        self.assertTrue(raised.exception.details['activation_guard']['detached'])
        self.assertEqual(self.events, ['install', 'detach'])

    def test_detach_failure_disarms_and_retains_callback_until_retry(self):
        self.user.UnhookWindowsHookEx.side_effect = lambda handle: False
        export = Mock(return_value=['mesh.obj'])
        with self.assertRaises(PluginError) as raised:
            fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertEqual(raised.exception.code, 'OBJ_EXPORT_ACTIVATION_DETACH_FAILED')
        self.assertTrue(getattr(self.scope, fence._RETAINED_GUARD)['blocked'])
        self.assertEqual(self.callback(5, 200, 0), 42)
        self.user.UnhookWindowsHookEx.side_effect = self.detach
        _, receipt = fence.guarded_obj_export(export, 'mesh.obj', object(), 123)
        self.assertTrue(receipt['detached'])
        self.assertFalse(hasattr(self.scope, fence._RETAINED_GUARD))
        self.assertEqual(self.events, ['install', 'detach', 'install', 'detach'])
