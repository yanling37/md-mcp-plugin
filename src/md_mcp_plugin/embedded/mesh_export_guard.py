"""Keep the synchronous OBJ exporter from activating the MD GUI thread."""
import ctypes
from ctypes import wintypes
import os
import sys

from md_mcp_plugin.errors import PluginError
from .cloth_vertex_context import _root_window


_WH_CBT = 5
_HCBT_ACTIVATE = 5
_ERROR_INVALID_HOOK_HANDLE = 1404
_SWITCH_KEYS = (0x09, 0x12, 0x5B, 0x5C)  # Tab, Alt, either Windows key.
_RETAINED_GUARD = '_md_mcp_mesh_export_guard_detach_failure'


class _Activate(ctypes.Structure):
    _fields_ = [('fMouse', wintypes.BOOL), ('hWndActive', wintypes.HWND)]


def _bind(dll, name, result, *arguments):
    method = getattr(dll, name)
    method.restype = result
    method.argtypes = arguments
    return method


def _guard_error(code, message, **details):
    return PluginError('OBJ_EXPORT_ACTIVATION_' + code, message, effects_started=False, **details)


class _ActivationGuard:
    def __init__(self, pid):
        user = ctypes.WinDLL('user32.dll', use_last_error=True, winmode=0x800)
        kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        hook_proc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        self._window_thread = _bind(user, 'GetWindowThreadProcessId', wintypes.DWORD,
                                   wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        self._get_foreground = _bind(user, 'GetForegroundWindow', wintypes.HWND)
        self._key_state = _bind(user, 'GetAsyncKeyState', ctypes.c_short, ctypes.c_int)
        self._install = _bind(user, 'SetWindowsHookExW', wintypes.HANDLE,
                              ctypes.c_int, hook_proc, wintypes.HMODULE, wintypes.DWORD)
        self._detach = _bind(user, 'UnhookWindowsHookEx', wintypes.BOOL, wintypes.HANDLE)
        self._call_next = _bind(user, 'CallNextHookEx', ctypes.c_ssize_t,
                                wintypes.HANDLE, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        self._callback = hook_proc(self._on_hook)
        self._hook = None
        self._armed = False
        self._callback_failed = False
        self._pid = pid
        self._thread = _bind(kernel, 'GetCurrentThreadId', wintypes.DWORD)()
        gui_thread, gui_pid = self._window_identity(_root_window(pid))
        if gui_pid != pid or not self._thread or gui_thread != self._thread:
            raise _guard_error('THREAD_MISMATCH', 'OBJ export must run on the current MD GUI thread')
        self._receipt = {
            'schema': 'md-obj-export-activation-guard/1', 'applicable': True,
            'process_id': pid, 'gui_thread_id': self._thread,
            'installed': False, 'detached': False,
            'programmatic_activations_vetoed': 0,
            'user_mouse_activations_passed': 0,
            'user_switch_key_activations_passed': 0,
        }

    def _window_identity(self, hwnd):
        if not hwnd:
            return 0, 0
        pid = wintypes.DWORD()
        thread = self._window_thread(hwnd, ctypes.byref(pid))
        return thread, pid.value

    def _on_hook(self, code, target, detail):
        try:
            if (self._armed and code == _HCBT_ACTIVATE and target
                    and self._window_identity(target) == (self._thread, self._pid)):
                if detail and ctypes.cast(detail, ctypes.POINTER(_Activate)).contents.fMouse:
                    self._receipt['user_mouse_activations_passed'] += 1
                elif any(self._key_state(key) & 0x8000 for key in _SWITCH_KEYS):
                    self._receipt['user_switch_key_activations_passed'] += 1
                else:
                    self._receipt['programmatic_activations_vetoed'] += 1
                    return 1
        except Exception:
            self._callback_failed = True
        return self._call_next(self._hook, code, target, detail)

    def __enter__(self):
        previous = getattr(sys, _RETAINED_GUARD, None)
        if previous is not None:
            previous['guard']._remove_hook()
            delattr(sys, _RETAINED_GUARD)
        before = self._window_identity(self._get_foreground())[1]
        self._receipt.update(foreground_pid_before=before, background_at_start=bool(before and before != self._pid))
        self._hook = self._install(_WH_CBT, self._callback, None, self._thread)
        if not self._hook:
            raise _guard_error('INSTALL_FAILED', 'Could not install the MD GUI-thread activation hook',
                               winerror=ctypes.get_last_error())
        self._receipt['installed'] = True
        self._armed = True
        return self

    def _remove_hook(self):
        ctypes.set_last_error(0)
        if not self._detach(self._hook):
            error = ctypes.get_last_error()
            if error != _ERROR_INVALID_HOOK_HANDLE:
                raise _guard_error('DETACH_FAILED', 'Could not remove the disarmed MD GUI-thread activation hook',
                                   winerror=error)
        self._hook = None
        self._receipt['detached'] = True

    def __exit__(self, *_):
        self._armed = False
        try:
            self._remove_hook()
        except PluginError:
            # An undetached Win32 hook must keep its callback alive. It now only forwards events.
            setattr(sys, _RETAINED_GUARD, {'blocked': True, 'guard': self})
            raise

    def receipt(self):
        after = self._window_identity(self._get_foreground())[1]
        self._receipt['foreground_pid_after'] = after
        if self._callback_failed:
            raise _guard_error('CALLBACK_FAILED', 'Could not verify the MD activation hook',
                               activation_guard=self._receipt)
        user_activation = self._receipt['user_mouse_activations_passed'] or self._receipt['user_switch_key_activations_passed']
        if self._receipt['background_at_start'] and after == self._pid and not user_activation:
            raise _guard_error('FOREGROUND_CHANGED', 'MD entered the foreground during OBJ export',
                               activation_guard=self._receipt)
        return self._receipt


def guarded_obj_export(function, path, option, pid):
    if os.name != 'nt':
        return function(path, option), {'schema': 'md-obj-export-activation-guard/1', 'applicable': False}
    with _ActivationGuard(pid) as guard:
        returned = function(path, option)
    return returned, guard.receipt()
