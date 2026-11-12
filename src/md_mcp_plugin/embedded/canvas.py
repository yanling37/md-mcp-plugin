"""Current-project Qt canvas primitives. No network, injection or OS input.

Only trusted embedded setup may configure a SHA256-pinned DLL. No public tool
accepts a library path, Python source, pointer, HWND or individual press/release.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

SCHEMA = "md-qt-canvas/1"
PREFIX = "mdcanvas1:"
QT_MODULES = ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6OpenGL.dll", "Qt6OpenGLWidgets.dll")
MAX_POINTS = 32
OUTPUT_CAPACITY = 262144
_STATE_KEY = "_md_mcp_plugin_canvas_state"


class CanvasError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _state():
    value = getattr(sys, _STATE_KEY, None)
    if value is None:
        value = {"configuration": None, "library": None, "call": None, "blocked": False}
        setattr(sys, _STATE_KEY, value)
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _hex(value, label):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CanvasError("INVALID_PARAMETERS", f"{label} must be a lowercase SHA256 fingerprint")
    return value


def _budget(value):
    if type(value) is not int or not 50 <= value <= 1500:
        raise CanvasError("INVALID_PARAMETERS", "budget_ms must be an integer in [50,1500]")
    return value


def _kind(value, *, all_views=False):
    if type(value) is not str or value not in (("2d", "3d", "all") if all_views else ("2d", "3d")):
        raise CanvasError("INVALID_PARAMETERS", "view must name a current 2d/3d view")
    return value


def _gesture(params):
    required = {"view", "view_ref", "view_fingerprint", "action", "points", "scene_token"}
    if type(params) is not dict or not required <= params.keys() or params.keys() - required - {"modifiers", "budget_ms"}:
        raise CanvasError("INVALID_PARAMETERS", "A complete referenced click/drag batch is required; unknown keys are rejected")
    kind = _kind(params["view"])
    fingerprint = _hex(params["view_fingerprint"], "view_fingerprint")
    token = _hex(params["scene_token"], "scene_token")
    budget = _budget(params.get("budget_ms", 750))
    action, points = params["action"], params["points"]
    if type(action) is not str or action not in ("click", "drag") or type(points) is not list:
        raise CanvasError("INVALID_PARAMETERS", "action must be click or drag, with a list of logical XY points")
    if not ((action == "click" and len(points) == 1) or (action == "drag" and 2 <= len(points) <= MAX_POINTS)):
        raise CanvasError("INVALID_PARAMETERS", "click needs one point; drag needs 2..32 points including its start")
    path = []
    for point in points:
        if type(point) is not list or len(point) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10_000_000 for v in point
        ):
            raise CanvasError("INVALID_PARAMETERS", "Each point must be finite nonnegative XY in viewport logical pixels")
        path.append([float(v) for v in point])
    if action == "drag" and path[0] == path[-1]:
        raise CanvasError("INVALID_PARAMETERS", "A drag must end at a different logical point")
    modifiers = params.get("modifiers", [])
    if type(modifiers) is not list or any(type(m) is not str or m not in ("shift", "ctrl", "alt") for m in modifiers) or len(set(modifiers)) != len(modifiers):
        raise CanvasError("INVALID_PARAMETERS", "modifiers must contain unique event-local shift, ctrl or alt")
    ref = _decode_ref(params["view_ref"])
    if ref["view"] != kind or ref["scene_token"] != token:
        raise CanvasError("STALE_VIEW_REFERENCE", "view_ref belongs to another view or scene; read canvas_views again")
    return {"operation": "gesture", "view": kind, "native_view_ref": ref["native_view_ref"],
            "view_fingerprint": fingerprint, "action": action, "points": path,
            "modifiers": sorted(modifiers), "budget_ms": budget}, token


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _json(value):
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _encode_ref(native_ref, kind, scene_token):
    data = {"version": 1, "native_view_ref": native_ref, "view": kind, "scene_token": scene_token}
    return PREFIX + base64.urlsafe_b64encode(_canonical(data).encode()).decode().rstrip("=")


def _decode_ref(value):
    try:
        if type(value) is not str or not value.startswith(PREFIX) or len(value) > 2048:
            raise ValueError("Invalid reference prefix or size")
        encoded = value[len(PREFIX):]
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        ref = _json(decoded.decode("utf-8"))
        if type(ref) is not dict or set(ref) != {"version", "native_view_ref", "view", "scene_token"} or type(ref["version"]) is not int or ref["version"] != 1:
            raise ValueError("Invalid reference fields")
        if type(ref["native_view_ref"]) is not str or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", ref["native_view_ref"]) is None:
            raise ValueError("Invalid native reference")
        _kind(ref["view"])
        _hex(ref["scene_token"], "reference scene_token")
        return ref
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise CanvasError("INVALID_VIEW_REFERENCE", "Use a complete view_ref returned by canvas_views") from exc


def install_native_bridge(path: str, sha256: str) -> dict:
    """Trusted embedded bootstrap only; verifies bytes but does not load/call the DLL.

    Root supplies its reviewed build manifest. This helper is not an OPERATIONS
    entry, is not an MCP tool, and cannot clear an uncertain operation/recovery.
    """
    _hex(sha256, "sha256")
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name.lower() != "md_qt_canvas_bridge.dll" or not candidate.is_file():
        raise CanvasError("INVALID_BRIDGE_CONFIGURATION", "An existing absolute md_qt_canvas_bridge.dll is required")
    candidate = candidate.resolve()
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual != sha256:
        raise CanvasError("BRIDGE_HASH_MISMATCH", "Reviewed DLL bytes differ from the supplied build manifest")
    configuration = {"path": str(candidate), "sha256": sha256}
    state = _state()
    if state["blocked"] or state["library"] is not None and state["configuration"] != configuration:
        raise CanvasError("BRIDGE_ALREADY_LOADED", "Do not hot-swap native bridges or clear recovery in a live process")
    state["configuration"] = configuration
    return {"configured": True, "loaded": state["library"] is not None, "sha256": sha256, "live_validation": "pending"}


def _file_version(path):
    version = ctypes.WinDLL("version.dll", use_last_error=True, winmode=0x800)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    ignored = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
    if not size or size > 1048576:
        raise CanvasError("QT_VERSION_UNAVAILABLE", "Qt module file version is unavailable")
    data = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.GetFileVersionInfoW(str(path), 0, size, data) or not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise CanvasError("QT_VERSION_UNAVAILABLE", "Qt module has no fixed file-version record")
    words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if words[0] != 0xFEEF04BD:
        raise CanvasError("QT_VERSION_UNAVAILABLE", "Qt module fixed-version signature differs")
    return [words[2] >> 16, words[2] & 65535, words[3] >> 16, words[3] & 65535]


def _environment():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise CanvasError("UNSUPPORTED_PLATFORM", "Canvas bridge requires the Windows x64 MD embedded process")
    import utility_api
    from .common import process_identity
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x800)
    kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [wintypes.LPCWSTR], wintypes.HMODULE
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetModuleFileNameW.restype = wintypes.DWORD

    def module_path(handle):
        buffer = ctypes.create_unicode_buffer(32768)
        count = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
        if not 0 < count < len(buffer):
            raise CanvasError("MODULE_PATH_UNAVAILABLE", "Cannot read the current process/module path")
        return Path(buffer.value).resolve()

    executable = module_path(None)
    result = {"process": process_identity(), "executable": str(executable),
              "md_version": [utility_api.GetMajorVersion(), utility_api.GetMinorVersion(), utility_api.GetPatchVersion()], "qt_modules": {}}
    for name in QT_MODULES:
        module = kernel.GetModuleHandleW(name)  # No LoadLibrary call for Qt.
        if not module:
            raise CanvasError("QT_MODULE_NOT_LOADED", "Required Qt module is not already loaded: " + name)
        path = module_path(module)
        if path.name.lower() != name.lower() or path.parent != executable.parent:
            raise CanvasError("QT_MODULE_DIRECTORY_MISMATCH", "Qt modules must belong to the current MD executable directory")
        file_version = _file_version(path)
        if file_version[:3] != [6, 10, 3]:
            raise CanvasError("QT_VERSION_MISMATCH", "Only the matching release Qt 6.10.3 ABI is supported")
        result["qt_modules"][name] = {"path": str(path), "file_version": file_version}
    return result


def _load(environment):
    state = _state()
    config = state["configuration"]
    if config is None:
        raise CanvasError("NATIVE_BRIDGE_NOT_CONFIGURED", "Root must configure the reviewed canvas DLL in MD embedded Python")
    if hashlib.sha256(Path(config["path"]).read_bytes()).hexdigest() != config["sha256"]:
        raise CanvasError("BRIDGE_HASH_MISMATCH", "Configured DLL bytes changed")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)  # DLL_LOAD_DIR (0x100) | SYSTEM32 (0x800); Qt already loaded.
        abi = library.mdqt_canvas_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise CanvasError("BRIDGE_ABI_MISMATCH", "Canvas C ABI version must be 1")
        call = library.mdqt_canvas_json
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, loaded_process=dict(environment["process"]))
    if state["loaded_process"] != environment["process"]:
        raise CanvasError("PROCESS_IDENTITY_MISMATCH", "Native bridge belongs to another process lifetime")
    return state["call"]


def _failure(code, message, *, unknown=False):
    return {"status": "UNKNOWN" if unknown else "REJECTED", "result": {"verified": False, "semantic_geometry_verified": False},
            "error": {"code": code, "message": message}, "effects_started": unknown, "requires_recovery": unknown}


def _invoke(request):
    mutation = request["operation"] == "gesture"
    state = _state()
    if mutation and state["blocked"]:
        return _failure("BRIDGE_RECOVERY_REQUIRED", "Previous canvas dispatch requires root recovery; it cannot be retried")
    if state["configuration"] is None:
        result = _failure("NATIVE_BRIDGE_NOT_CONFIGURED", "Root must install the reviewed native build; no fallback is used")
        result["status"] = "UNSUPPORTED"
        return result
    invoked = False
    try:
        environment = _environment()
        call = _load(environment)
        data = _canonical(request).encode("utf-8")
        if len(data) > 16384:
            raise CanvasError("INVALID_PARAMETERS", "Canvas request exceeds 16 KiB")
        output = ctypes.create_string_buffer(OUTPUT_CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        invoked = True
        code = call(environment["process"]["pid"], environment["process"]["creation_filetime"], data, len(data),
                    output, len(output), ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4):
            return _failure({-1: "INVALID_ABI_ARGUMENTS", -2: "PROCESS_IDENTITY_MISMATCH", -3: "GUI_THREAD_REQUIRED", -4: "NATIVE_BRIDGE_BUSY"}[code], "Native entry rejected before event dispatch")
        if not 0 < written.value < len(output) or needed.value != written.value + 1:
            raise CanvasError("INVALID_NATIVE_RECEIPT", "Missing/oversized native receipt; never retry the gesture")
        report = _json(output.raw[:written.value].decode("utf-8"))
        if type(report) is not dict or report.get("schema") != SCHEMA or report.get("return_code") != code:
            raise CanvasError("INVALID_NATIVE_RECEIPT", "Native receipt schema/return code mismatch")
        if report.get("status") not in ("SUCCEEDED", "REJECTED", "PARTIAL", "UNKNOWN") or any(
            type(report.get(key)) is not bool for key in ("effects_started", "requires_recovery", "verified")
        ) or type(report.get("event_calls")) is not int or not 0 <= report["event_calls"] <= MAX_POINTS + 2:
            raise CanvasError("INVALID_NATIVE_RECEIPT", "Native status/counters are incomplete")
        if report["effects_started"] != (report["event_calls"] > 0) or not mutation and report["effects_started"]:
            raise CanvasError("INVALID_NATIVE_RECEIPT", "Native effect counters disagree")
        if report["status"] == "SUCCEEDED":
            if code != 0 or not report["verified"] or report["requires_recovery"]:
                raise CanvasError("INVALID_NATIVE_RECEIPT", "Success receipt lacks its verification contract")
            if mutation:
                proofs = ("effects_started", "press_sent", "release_sent", "release_returned", "paired_release_verified",
                          "physical_state_unchanged", "all_events_nonspontaneous", "dispatch_completed")
                if (report["event_calls"] != len(request["points"]) + 2 or any(report.get(key) is not True for key in proofs)
                        or report.get("press_outstanding") is not False or report.get("semantic_geometry_verified") is not False):
                    raise CanvasError("INVALID_NATIVE_RECEIPT", "Success receipt does not prove a closed event batch")
        elif report["verified"] or code == 0 or report["effects_started"] and not report["requires_recovery"]:
            raise CanvasError("INVALID_NATIVE_RECEIPT", "Failed native receipt contradicts its effect/recovery contract")
        if report["requires_recovery"] or mutation and report["effects_started"] and report["status"] != "SUCCEEDED":
            state["blocked"] = True
        return {"status": report["status"], "result": report, "error": report.get("error"),
                "effects_started": report["effects_started"], "requires_recovery": report["requires_recovery"]}
    except Exception as exc:
        unknown = mutation and invoked
        if unknown:
            state["blocked"] = True
        return _failure(getattr(exc, "code", "NATIVE_BRIDGE_ERROR"), str(exc), unknown=unknown)


def _scene():
    from .common import scene
    return scene()


def _public_view(value, scene_token):
    if type(value) is not dict or value.get("view") not in ("2d", "3d") or type(value.get("native_view_ref")) is not str:
        raise CanvasError("INVALID_NATIVE_RECEIPT", "Native view descriptor is invalid")
    _hex(value.get("view_fingerprint"), "native view_fingerprint")
    public = dict(value)
    public["view_ref"] = _encode_ref(public.pop("native_view_ref"), public["view"], scene_token)
    _decode_ref(public["view_ref"])
    return public


def runtime_capabilities(params):
    if type(params) is not dict or params:
        return _failure("INVALID_PARAMETERS", "runtime_capabilities takes no parameters")
    state = _state()
    value = {"configured": state["configuration"] is not None, "loaded": state["library"] is not None,
             "gesture_blocked_until_recovery": state["blocked"], "qt_required": "6.10.3 release x64",
             "actions": ["click", "drag"], "button": "left", "event_local_modifiers": ["shift", "ctrl", "alt"],
             "max_points": MAX_POINTS, "max_event_calls": MAX_POINTS + 2, "budget_ms_range": [50, 1500],
             "coordinate_space": "viewport_logical_pixels", "semantic_geometry_verified": False,
             "live_validation": "pending", "DLL_loaded_by_this_probe": False}
    try:
        value["environment"] = _environment()
    except (CanvasError, ImportError, OSError, AttributeError) as exc:
        value["environment_error"] = {"code": getattr(exc, "code", "NOT_MD_EMBEDDED"), "message": str(exc)}
    return {"status": "SUCCEEDED", "result": value, "error": None, "effects_started": False, "requires_recovery": False}


def views(params):
    try:
        if type(params) is not dict or params.keys() - {"view", "budget_ms"}:
            raise CanvasError("INVALID_PARAMETERS", "Only view and budget_ms are accepted")
        kind = _kind(params.get("view", "all"), all_views=True)
        budget = _budget(params.get("budget_ms", 750))
        before = _scene()
        response = _invoke({"operation": "views", "budget_ms": budget})
        if response["status"] != "SUCCEEDED":
            return response
        after = _scene()
        if before["scene_token"] != after["scene_token"]:
            raise CanvasError("SCENE_CHANGED_DURING_READ", "Read canvas_views again against a stable current project")
        result = response["result"]
        result["views"] = [_public_view(v, after["scene_token"]) for v in result["views"] if kind == "all" or v.get("view") == kind]
        result["scene_token"] = after["scene_token"]
        result["scene_fingerprint_scope"] = after.get("fingerprint_scope")
        return response
    except Exception as exc:
        return _failure(getattr(exc, "code", "INVALID_NATIVE_RECEIPT"), str(exc))


def gesture(params):
    invoked = False
    response = None
    try:
        request, token = _gesture(params)
        before = _scene()
        if token != before["scene_token"]:
            raise CanvasError("STALE_SCENE", "Read md_scene and canvas_views again before dispatch")
        response = _invoke(request)
        invoked = response["effects_started"]
        if response["status"] == "SUCCEEDED":
            result = response["result"]
            after = _scene()
            result["before"] = _public_view(result["before"], token)
            result["after"] = _public_view(result["after"], after["scene_token"])
            result["scene_token_after"] = after["scene_token"]
            result["scene_fingerprint_scope"] = after.get("fingerprint_scope")
            result["verification_scope"] = "Closed synthetic event batch, public view readback and sampled physical-state stability only"
        elif type(response.get("result")) is dict:
            # Partial receipts may lack a safe scene readback; do not issue fresh refs.
            for key in ("before", "after"):
                if type(response["result"].get(key)) is dict:
                    response["result"][key].pop("native_view_ref", None)
        return response
    except Exception as exc:
        if invoked:
            _state()["blocked"] = True
        failure = _failure(getattr(exc, "code", "CANVAS_READBACK_FAILED"), str(exc), unknown=invoked)
        if invoked and type(response) is dict and type(response.get("result")) is dict:
            evidence = response["result"]
            evidence["native_dispatch_status"] = evidence.pop("status", response["status"])
            evidence["verified"] = False
            evidence["post_dispatch_readback_failed"] = True
            for key in ("before", "after"):
                if type(evidence.get(key)) is dict:
                    evidence[key].pop("native_view_ref", None)
                    evidence[key].pop("view_ref", None)
            failure["result"] = evidence
        return failure


OPERATIONS = {"canvas.runtime_capabilities": runtime_capabilities, "canvas.views": views, "canvas.gesture": gesture}
