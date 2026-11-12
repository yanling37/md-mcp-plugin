"""Fixed semantic Pinching Override UI boolean; no solver/brush/Rest execution."""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import re
import sys

from . import canvas as _base
from . import rest_visible_contents as _snapshot_provider
from . import rest_visible_recovery as _paths

SCHEMA = "md-qt-set-pinching-override/1"
STATE_KEY = "_md_mcp_plugin_rest_pinching_override_state"
CAPACITY = 1048576
ZERO_COUNTS = ("QAction_trigger_calls", "event_calls", "OS_input_calls", "focus_calls", "simulation_calls",
               "Brush_action_calls", "UpdateRest_calls", "ResetRest_calls", "show_raise_activate_calls", "arbitrary_property_set_calls")


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {"configuration": None, "library": None, "call": None, "process": None, "blocked": False}
        setattr(sys, STATE_KEY, state)
    return state


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_native_bridge(path, sha256):
    file = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None or not file.is_absolute()
            or file.name != "md_qt_rest_pinching_override_bridge.dll" or file.is_symlink()
            or not file.is_file() or _sha(file) != sha256):
        raise ValueError("Fixed reviewed Pinching Override DLL required")
    config = {"path": str(file.resolve()), "sha256": sha256}; state = _state()
    if state["blocked"] or state["library"] is not None and state["configuration"] != config:
        raise ValueError("Cannot clear an uncertain setting or replace its loaded DLL")
    state["configuration"] = config
    return {"configured": True, "loaded": state["library"] is not None, "solver_rest_writer": False}


def _load(environment):
    state = _state(); config = state["configuration"]
    if state["blocked"]: raise ValueError("An uncertain setting must be reconciled, never replayed")
    if config is None or _sha(config["path"]) != config["sha256"]: raise ValueError("Configure unchanged reviewed DLL")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        version = library.mdqt_pinching_override_abi_version; version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 1: raise ValueError("Pinching Override C ABI1 required")
        call = library.mdqt_set_pinching_override
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(environment["process"]))
    if state["process"] != environment["process"]: raise ValueError("DLL belongs to another MD lifetime")
    return state["call"]


def _params(params):
    if type(params) is not dict or not {"scene_token", "enabled"} <= params.keys() or params.keys() - {"scene_token", "enabled", "budget_ms"}:
        raise ValueError("Only scene_token, enabled bool and bounded budget accepted")
    token, enabled, budget = params["scene_token"], params["enabled"], params.get("budget_ms", 750)
    if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(enabled) is not bool:
        raise ValueError("Current scene token and actual bool required")
    if type(budget) is not int or not 50 <= budget <= 1500: raise ValueError("Integer budget [50,1500] required")
    return token, enabled, budget


def _value(value):
    if (type(value) is not dict or type(value.get("checked")) is not bool
            or type(value.get("check_state")) is not int or value["check_state"] != (2 if value["checked"] else 0)
            or value.get("text") != ("On" if value["checked"] else "Off") or value.get("enabled") is not True
            or value.get("visible") is not True or value.get("class_name") != "CloUICommon::CheckBox"
            or value.get("object_name") != "checkBox"):
        raise ValueError("Typed binary semantic checkbox value differs")
    for key in ("row_owner_path", "label_owner_path", "checkbox_owner_path"): _paths._path(value.get(key))
    row = value["row_owner_path"]
    if value["label_owner_path"][:-1] != row or value["checkbox_owner_path"][:-1] != row:
        raise ValueError("Label/checkbox must share the exact current parent row")


def _validate(raw, code, process, enabled):
    if (type(code) is not int or code not in (0, 1, 2) or type(raw) is not dict or raw.get("schema") != SCHEMA
            or type(raw.get("return_code")) is not int or raw["return_code"] != code
            or type(raw.get("process_id")) is not int or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])
            or type(raw.get("requested_enabled")) is not bool or raw["requested_enabled"] is not enabled
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in ZERO_COUNTS)
            or raw.get("automatic_retry_allowed") is not False or raw.get("solver_rest_write_verified") is not False
            or raw.get("native_faults_contained") is not False or raw.get("direct_UI_setting_only") is not True):
        raise ValueError("Fixed UI-setting contract/process/zero-input facts differ")
    calls = raw.get("button_click_calls")
    if (type(calls) is not int or calls not in (0, 1) or raw.get("effects_started") is not bool(calls)
            or type(raw.get("click_returned")) is not bool or type(raw.get("verified")) is not bool):
        raise ValueError("Actual click count/flags differ")
    if code:
        if (raw["verified"] or raw.get("status") != ("UNKNOWN" if calls else "REJECTED")
                or raw.get("requires_recovery") is not bool(calls) or code != (2 if calls else 1)):
            raise ValueError("Unknown or rejected UI command upgraded to success")
        return
    before, after = raw.get("before"), raw.get("after"); _value(before); _value(after)
    change = before["checked"] is not enabled
    if (raw["verified"] is not True or raw.get("status") != "UI_SETTING_READ_BACK" or raw.get("requires_recovery") is not False
            or raw.get("change_required") is not change or calls != int(change) or raw["click_returned"] is not change
            or raw.get("no_effect_observed") is not (not change) or after["checked"] is not enabled
            or raw.get("original_ui_value") is not before["checked"] or raw.get("semantic_row_binding_verified") is not True
            or raw.get("main_window_minimized_before") is not True or raw.get("main_window_minimized_after") is not True
            or raw.get("physical_state_unchanged") is not True or type(raw.get("physical_before")) is not dict
            or raw["physical_before"] != raw.get("physical_after")
            or any(before[key] != after[key] for key in ("row_owner_path", "label_owner_path", "checkbox_owner_path"))):
        raise ValueError("Requested UI boolean/current row/physical state not read back")


def _call(call, process, enabled, budget):
    buffer = ctypes.create_string_buffer(CAPACITY); written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = call(process["pid"], process["creation_filetime"], int(enabled), budget, buffer, len(buffer), ctypes.byref(written), ctypes.byref(needed))
    if not written.value and code in (-1, -2, -3, -4): return code, None
    if not 0 < written.value < CAPACITY or needed.value != written.value + 1: raise ValueError("Native setting receipt bounds differ")
    raw = _base._json(buffer.raw[:written.value].decode("utf8")); _validate(raw, code, process, enabled)
    return code, raw


def set_pinching_override(params):
    invoked = False; raw = None; state = _state(); preservation = None
    try:
        token, enabled, budget = _params(params)
        if _base._scene()["scene_token"] != token: raise ValueError("Stale current scene token")
        environment = _base._environment(); call = _load(environment)
        before = _snapshot_provider._native_snapshot()
        state["blocked"] = True; invoked = True
        code, raw = _call(call, environment["process"], enabled, budget)
        if raw is None:
            state["blocked"] = False
            return {"status": "REJECTED", "result": {"verified": False, "native_preflight_return_code": code},
                    "effects_started": False, "requires_recovery": False,
                    "error": {"code": "PINCHING_SETTING_PREFLIGHT_REJECTED", "message": "Native zero-effect preflight rejected"}}
        after = _snapshot_provider._native_snapshot()
        preserved = before["scene_fingerprint"] == after["scene_fingerprint"] and before["rows"] == after["rows"]
        same_token = _base._scene()["scene_token"] == token
        preservation = {"native_2d_geometry_unchanged_verified": before["scene_fingerprint"] == after["scene_fingerprint"],
                        "runtime_records_unchanged_verified": before["rows"] == after["rows"],
                        "before_native_2d_fingerprint": before["scene_fingerprint"], "after_native_2d_fingerprint": after["scene_fingerprint"],
                        "scene_token_unchanged": same_token, "full_3d_or_solver_rest_preservation_verified": False}
        verified = code == 0 and preserved and same_token
        known = code in (0, 1) and preserved and same_token
        state["blocked"] = not known
        result = {"verified": verified, "native_setting": raw, "native_preservation": preservation,
                  "scene_token": token, "UI_setting_only": True, "solver_rest_write_verified": False,
                  "original_ui_value": raw.get("original_ui_value"), "restore_value_is_original_ui_value": True}
        return {"status": "SUCCEEDED" if verified else "REJECTED" if known else "UNKNOWN", "result": result,
                "effects_started": raw["effects_started"], "requires_recovery": not known,
                "error": None if verified else {"code": "PINCHING_SETTING_NOT_VERIFIED", "message": raw.get("reason", "Native scope changed")}}
    except Exception as error:
        return {"status": "UNKNOWN" if invoked else "REJECTED", "effects_started": invoked, "requires_recovery": invoked,
                "result": {"verified": False, "native_setting": raw, "native_preservation": preservation, "solver_rest_write_verified": False},
                "error": {"code": "PINCHING_SETTING_NOT_VERIFIED", "message": str(error)}}


OPERATIONS = {
    "rest_pinching_override.set_pinching_override": set_pinching_override,
}
