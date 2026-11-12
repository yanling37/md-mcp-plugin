"""Independent fixed dock identity reader. No loading/MD work at import time."""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import re
import sys

from . import canvas as _base

SCHEMA = "md-qt-rest-dock-identity/1"
STATE_KEY = "_md_mcp_plugin_rest_dock_identity_state"
CAPACITY = 1048576
COUNTS = ("event_calls", "QAction_trigger_calls", "method_invoke_calls", "property_write_calls", "simulation_calls",
          "OS_input_calls", "focus_calls", "widget_create_show_calls", "message_handler_changes")
FALSE_FLAGS = ("effects_started", "requires_recovery", "RestShape_write_contract_verified", "toggle_restore_authorized", "hidden_numeric_read")
CLASSES = ("PropertyEditorWidget", "Marvelous::PropertyEditorWidget", "SimulationPropertyWidget",
           "Marvelous::SimulationPropertyWidget", "SimulationProperties", "Marvelous::SimulationProperties")


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {"configuration": None, "library": None, "call": None, "process": None}
        setattr(sys, STATE_KEY, state)
    return state


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_native_bridge(path, sha256):
    """Root-owned configuration only, no DLL load and no R1/R2/R3 changes."""
    file = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None or not file.is_absolute()
            or file.name != "md_qt_rest_dock_identity_bridge.dll" or file.is_symlink()
            or not file.is_file() or _hash(file) != sha256):
        raise ValueError("One unchanged pinned dock identity DLL required")
    config = {"path": str(file.resolve()), "sha256": sha256}
    state = _state()
    if state["library"] is not None and state["configuration"] != config:
        raise ValueError("Loaded dock diagnostics cannot be hot-swapped")
    state["configuration"] = config
    return {"configured": True, "loaded": state["library"] is not None, "live_validation": "pending"}


def _load(environment):
    state = _state(); config = state["configuration"]
    if config is None or _hash(config["path"]) != config["sha256"]:
        raise ValueError("Configure the unchanged reviewed dock identity DLL")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        abi = library.mdqt_rest_dock_identity_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise ValueError("Dock identity C ABI1 required")
        call = library.mdqt_rest_dock_identity_inspect
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(environment["process"]))
    if state["process"] != environment["process"]:
        raise ValueError("Dock identity belongs to another process lifetime")
    return state["call"]


def _action(row):
    if (type(row) is not dict or row.get("triggered") is not False or type(row.get("associated_objects")) is not list
            or len(row["associated_objects"]) > 64
            or any(type(row.get(k)) is not bool for k in ("enabled", "checkable", "checked"))):
        raise ValueError("Typed bounded action association metadata required")


def _validate(raw, code, process):
    if (type(raw) is not dict or raw.get("schema") != SCHEMA or type(raw.get("return_code")) is not int or raw["return_code"] != code
            or type(raw.get("verified")) is not bool or type(raw.get("catalog_complete")) is not bool
            or type(raw.get("process_id")) is not int or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])):
        raise ValueError("Dock identity envelope/process differs")
    if any(type(raw.get(k)) is not int or raw[k] != 0 for k in COUNTS) or any(raw.get(k) is not False for k in FALSE_FLAGS):
        raise ValueError("Identity probe cannot dispatch/read hidden numeric/claim a write or toggle restoration")
    candidates = raw.get("candidates", [])
    if (type(candidates) is not list or len(candidates) > 16 or type(raw.get("candidate_count")) is not int
            or raw["candidate_count"] != len(candidates)):
        raise ValueError("Fixed candidate count differs")
    if code != 0:
        if raw["verified"] or raw["catalog_complete"] or raw.get("status") != "READ_NOT_VERIFIED":
            raise ValueError("Incomplete identity upgraded to verified")
        return
    if (not raw["verified"] or not raw["catalog_complete"] or raw.get("status") != "READ_COMPLETE"
            or raw.get("physical_state_unchanged") is not True or raw.get("physical_before") != raw.get("physical_after")):
        raise ValueError("Complete read needs stable physical scope")
    action = raw.get("simulation_property_action", {})
    if action.get("object_name") != "actionSet_Simulation_Property" or action.get("triggered") is not False:
        raise ValueError("Only the fixed Simulation Properties action is read")
    if action.get("reference_unique") is True:
        _action(action)
    for row in candidates:
        if (row.get("object_name") != "PropertyEditorContents" and row.get("class_name") not in CLASSES
                or row.get("hidden_numeric_read") is not False or row.get("toggle_restore_authorized") is not False
                or any(type(row.get(k)) is not bool for k in ("is_hidden", "is_visible", "is_visible_to_mainwindow", "ancestor_dock_present"))
                or type(row.get("direct_widget_children")) is not list or len(row["direct_widget_children"]) > 32):
            raise ValueError("Unexpected fixed dock candidate or scope")
        if row["ancestor_dock_present"]:
            dock = row.get("ancestor_dock", {})
            _action(dock.get("toggle_action"))
            if type(dock.get("toggle_action_checked_matches_not_hidden")) is not bool:
                raise ValueError("Typed Qt dock toggle equality observation required")
        for stack in row.get("ancestor_stacks", []):
            if type(stack.get("page_set_calls")) is not int or stack["page_set_calls"] != 0 or len(stack.get("pages", [])) > 64:
                raise ValueError("Only bounded stack identity reads allowed")


def _failure(code, message, raw=None, status="REJECTED"):
    result = {"verified": False, "toggle_restore_authorized": False, "RestShape_write_contract_verified": False}
    if type(raw) is dict:
        result.update(unverified_native_diagnostic=raw, native_diagnostic_is_unverified=True)
    return {"status": status, "result": result, "effects_started": False, "requires_recovery": False,
            "error": {"code": code, "message": message}}


def inspect(params):
    raw = None
    try:
        if type(params) is not dict or not {"scene_token"} <= params.keys() or params.keys() - {"scene_token", "budget_ms"}:
            raise ValueError("Only scene_token and bounded budget accepted")
        token, budget = params["scene_token"], params.get("budget_ms", 750)
        if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(budget) is not int or not 50 <= budget <= 1500:
            raise ValueError("Current scene token and integer budget [50,1500] required")
        if _state()["configuration"] is None:
            return _failure("DOCK_IDENTITY_NOT_CONFIGURED", "Root must configure this independent diagnostic", status="UNSUPPORTED")
        before = _base._scene()
        if before["scene_token"] != token:
            return _failure("STALE_SCENE", "Read the current scene again")
        environment = _base._environment(); process = environment["process"]
        call = _load(environment)
        output = ctypes.create_string_buffer(CAPACITY); written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        code = call(process["pid"], process["creation_filetime"], budget, output, len(output), ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4):
            return _failure("NATIVE_PREFLIGHT_REJECTED", "ABI/process/GUI thread/busy preflight rejected")
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise ValueError("Dock native output bounds differ")
        raw = _base._json(output.raw[:written.value].decode("utf8")); _validate(raw, code, process)
        after = _base._scene()
        if after["scene_token"] != token:
            return _failure("SCENE_CHANGED_DURING_READ", "Dock read did not establish stable scene", raw)
        raw["scene_token"] = token
        return {"status": "SUCCEEDED" if raw["verified"] else "REJECTED", "result": raw,
                "effects_started": False, "requires_recovery": False,
                "error": None if raw["verified"] else {"code": "READ_NOT_VERIFIED", "message": raw.get("reason", "Incomplete dock read")}}
    except Exception as error:
        return _failure("DOCK_IDENTITY_FAILED", str(error), raw)


OPERATIONS = {"rest_dock_identity.inspect": inspect}
