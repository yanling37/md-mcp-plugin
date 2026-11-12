"""Fixed simulation-entry metadata, not a simulation controller."""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import re
import sys

from . import canvas as _base
from . import rest_visible_contents as _snapshots

SCHEMA = "md-qt-rest-simulation-context/2"
LIBRARY = "md_qt_rest_simulation_context_v2_bridge.dll"
STATE_KEY = "_md_mcp_plugin_rest_simulation_context_state_v2"
CAPACITY = 1048576
ACTION_NAMES = ("action_SimStop", "action_DrapePattern", "actionSimulationBrushPinching", "actionMove_Pattern")
SYMBOL_NAMES = ("IsSimulation", "IsSimulating", "GetSimulationState", "GetSimulationStatus", "StartSimulation", "StopSimulation", "Simulate")
ZERO_COUNTS = ("event_calls", "QAction_trigger_calls", "button_click_calls", "method_invoke_calls", "QMetaProperty_read_calls",
               "property_write_calls", "OS_input_calls", "focus_calls", "simulation_calls", "show_raise_activate_calls")


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {"configuration": None, "library": None, "call": None, "process": None}; setattr(sys, STATE_KEY, state)
    return state


def _sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_native_bridge(path, sha256):
    file = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None or not file.is_absolute()
            or file.name != LIBRARY or file.is_symlink() or not file.is_file() or _sha(file) != sha256):
        raise ValueError("Fixed reviewed readonly simulation-context DLL required")
    config = {"path": str(file.resolve()), "sha256": sha256}; state = _state()
    if state["library"] is not None and state["configuration"] != config: raise ValueError("Loaded DLL cannot be hot-swapped")
    state["configuration"] = config
    return {"configured": True, "loaded": state["library"] is not None, "simulation_control_enabled": False}


def _load(environment):
    state = _state(); config = state["configuration"]
    if config is None or _sha(config["path"]) != config["sha256"]: raise ValueError("Configure unchanged reviewed DLL")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        version = library.mdqt_rest_simulation_context_abi_version; version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 2: raise ValueError("Simulation-context ABI2 required")
        call = library.mdqt_rest_simulation_context_inspect
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int; state.update(library=library, call=call, process=dict(environment["process"]))
    if state["process"] != environment["process"]: raise ValueError("DLL belongs to another MD lifetime")
    return state["call"]


def _runtime_metadata():
    import utility_api
    namespace = vars(utility_api); rows = []
    for name in SYMBOL_NAMES:
        value = namespace.get(name); doc = getattr(value, "__doc__", None) if name in namespace else None
        rows.append({"name": name, "exists": name in namespace, "callable": callable(value),
                     "doc": doc[:2048] if type(doc) is str else None, "invoked": False})
    return {"symbols": rows, "function_calls": 0, "running_state_getter_contract_verified": False}


def _validate(raw, code, process):
    if (type(code) is not int or code not in (0, 1) or type(raw) is not dict or raw.get("schema") != SCHEMA
            or type(raw.get("return_code")) is not int or raw["return_code"] != code
            or type(raw.get("process_id")) is not int or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])
            or type(raw.get("verified")) is not bool or type(raw.get("catalog_complete")) is not bool
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in ZERO_COUNTS)
            or any(raw.get(key) is not False for key in ("effects_started", "requires_recovery", "simulation_running_state_verified",
                "start_stop_dispatch_authorized", "RestShape_write_contract_verified"))):
        raise ValueError("Readonly simulation metadata header/counts differ")
    if code:
        if raw["verified"] or raw["catalog_complete"] or raw.get("status") != "READ_NOT_VERIFIED": raise ValueError("Incomplete catalog upgraded")
        return
    before, after = raw.get("physical_before"), raw.get("physical_after")
    if (raw["verified"] is not True or raw["catalog_complete"] is not True or raw.get("status") != "READ_COMPLETE"
            or raw.get("main_window_minimized_before") is not True or raw.get("main_window_minimized_after") is not True
            or type(before) is not dict or type(after) is not dict
            or any(state.get("cursor_valid") is not True or type(state.get("cursor")) is not list
                   or len(state["cursor"]) != 2 or any(type(value) is not int for value in state["cursor"])
                   for state in (before, after))
            or dict(before, cursor=after["cursor"]) != after
            or raw.get("physical_state_unchanged") is not (before == after)
            or raw.get("cursor_position_changed_during_read") is not (before["cursor"] != after["cursor"])):
        raise ValueError("Minimized physical scope differs")
    actions = raw.get("actions")
    if type(actions) is not list or len(actions) != len(ACTION_NAMES) or tuple(row.get("object_name") for row in actions) != ACTION_NAMES:
        raise ValueError("Only fixed simulation actions admitted")
    for row in actions:
        count = row.get("match_count")
        if (type(count) is not int or count < 0 or row.get("triggered") is not False
                or type(row.get("reference_unique")) is not bool or row["reference_unique"] is not (count == 1)):
            raise ValueError("Action identity/count facts differ")
        if not row["reference_unique"]: continue
        if any(type(row.get(key)) is not bool for key in ("enabled", "checkable", "checked")) or type(row.get("text")) is not str:
            raise ValueError("Action states must be typed observations")
        associated = row.get("associated_objects")
        if type(associated) is not list or len(associated) > 64: raise ValueError("Associated-object bound differs")
        group = row.get("group")
        if group is not None and (type(group) is not dict or type(group.get("members")) is not list or len(group["members"]) > 64):
            raise ValueError("Fixed action-group bound differs")
    instances = raw.get("override_instances")
    if (type(instances) is not list or len(instances) > 4 or type(raw.get("override_instance_count")) is not int
            or raw["override_instance_count"] != len(instances)):
        raise ValueError("Actual Override instance count differs")
    for instance in instances:
        if instance.get("class_name") not in ("Marvelous::DialogOverrideSettings", "DialogOverrideSettings"):
            raise ValueError("Unexpected dialog class")


def inspect(params):
    raw = None
    try:
        if type(params) is not dict or not {"scene_token"} <= params.keys() or params.keys() - {"scene_token", "budget_ms"}:
            raise ValueError("Only current scene token and bounded budget required")
        token, budget = params["scene_token"], params.get("budget_ms", 750)
        if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(budget) is not int or not 50 <= budget <= 1500:
            raise ValueError("Current lowercase token and integer budget [50,1500] required")
        if _base._scene()["scene_token"] != token: raise ValueError("Stale current scene token")
        before = _snapshots._native_snapshot(); environment = _base._environment(); process = environment["process"]; call = _load(environment)
        buffer = ctypes.create_string_buffer(CAPACITY); written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        code = call(process["pid"], process["creation_filetime"], budget, buffer, len(buffer), ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4): raise ValueError("Zero-effect native preflight rejected")
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1: raise ValueError("Native receipt bounds differ")
        raw = _base._json(buffer.raw[:written.value].decode("utf8")); _validate(raw, code, process)
        symbols = _runtime_metadata(); after = _snapshots._native_snapshot()
        preservation = _snapshots._preservation(before, after)
        if (not preservation["native_2d_geometry_unchanged_verified"] or not preservation["runtime_records_unchanged_verified"]
                or _base._scene()["scene_token"] != token): raise ValueError("Current native2D/runtime scope changed during readonly probe")
        raw.update(scene_token=token, runtime_api_metadata=symbols, native_preservation=preservation)
        return {"status": "SUCCEEDED" if raw["verified"] else "REJECTED", "result": raw, "effects_started": False, "requires_recovery": False,
                "error": None if raw["verified"] else {"code": "SIMULATION_CONTEXT_NOT_VERIFIED", "message": raw.get("reason", "Read incomplete")}}
    except Exception as error:
        return {"status": "REJECTED", "result": {"verified": False, "unverified_native_diagnostic": raw},
                "effects_started": False, "requires_recovery": False,
                "error": {"code": "SIMULATION_CONTEXT_NOT_VERIFIED", "message": str(error)}}


OPERATIONS = {"rest_simulation_context.inspect": inspect}
