"""Fixed Rest Override diagnostic. No native work on import or configuration.

No writer exists until a current public instance, typed invocation and actual
solver-rest/selection readback contract are established independently.
"""
from __future__ import annotations

import ctypes
import hashlib
import re
from pathlib import Path
import sys

from . import canvas as _base

CAPACITY = 1048576
HOST_CONTROLS_SCHEMA = "md-qt-rest-host-controls/1"
HOST_CONTROLS_STATE_KEY = "_md_mcp_plugin_rest_host_controls_state"
ACTION_NAMES = ("actionSet_Simulation_Property", "actionSoftSelection", "actionSimulationBrushPinching",
                "actionBrushPinching", "actionPinchBrush", "actionMove_Pattern")
ZERO_COUNTS = ("event_calls", "QAction_trigger_calls", "method_invoke_calls", "QMetaProperty_read_calls",
               "property_write_calls", "OS_input_calls", "focus_calls", "simulation_calls", "widget_create_show_calls")
FALSE_CLAIMS = ("effects_started", "requires_recovery", "RestShape_write_contract_verified",
                "action_dispatch_authorized", "solver_rest_value_read", "model_data_preservation_verified")


def _host_controls_state():
    return _bridge_state(HOST_CONTROLS_STATE_KEY)


def _bridge_state(key):
    state = getattr(sys, key, None)
    if state is None:
        state = {"configuration": None, "library": None, "call": None, "process": None}
        setattr(sys, key, state)
    return state


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_host_controls_bridge(path, sha256):
    """Configure the independent, pinned host controls DLL without loading it."""
    return _install_bridge(_host_controls_state(), "md_qt_rest_host_controls_bridge.dll", path, sha256)


def _install_bridge(state, basename, path, sha256):
    candidate = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None
            or not candidate.is_absolute() or candidate.name != basename
            or candidate.is_symlink() or not candidate.is_file() or _sha(candidate) != sha256):
        raise ValueError("One existing SHA-pinned Rest diagnostic DLL required")
    configuration = {"path": str(candidate.resolve()), "sha256": sha256}
    if state["library"] is not None and state["configuration"] != configuration:
        raise ValueError("Loaded native diagnostics cannot be hot-swapped")
    state["configuration"] = configuration
    return {"configured": True, "loaded": state["library"] is not None, "live_validation": "pending"}


def _load_host_controls(environment):
    return _load_bridge(environment, _host_controls_state(), "mdqt_rest_host_controls")


def _load_bridge(environment, state, prefix):
    config = state["configuration"]
    if config is None or _sha(config["path"]) != config["sha256"]:
        raise ValueError("Root must configure the unchanged reviewed Rest diagnostic DLL")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        abi = getattr(library, prefix + "_abi_version")
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise ValueError("Rest diagnostic C ABI1 required")
        call = getattr(library, prefix + "_inspect")
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(environment["process"]))
    if state["process"] != environment["process"]:
        raise ValueError("Rest diagnostic DLL belongs to another process lifetime")
    return state["call"]


def _validate(raw, code, process, *, schema):
    if (type(raw) is not dict or raw.get("schema") != schema or type(raw.get("return_code")) is not int
            or raw["return_code"] != code or type(raw.get("verified")) is not bool
            or type(raw.get("catalog_complete")) is not bool):
        raise ValueError("Invalid typed Rest diagnostic envelope")
    if any(type(raw.get(key)) is not int or raw[key] != 0 for key in ZERO_COUNTS):
        raise ValueError("Read-only diagnostic cannot report dispatch/property writes")
    if any(raw.get(key) is not False for key in FALSE_CLAIMS):
        raise ValueError("Metadata must not claim Rest invocation or model preservation")
    if (type(raw.get("process_id")) is not int or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])):
        raise ValueError("Diagnostic belongs to another MD lifetime")
    if code != 0:
        if raw["verified"] or raw["catalog_complete"] or raw.get("status") != "READ_NOT_VERIFIED":
            raise ValueError("Rejected diagnostic upgraded to verified")
        return
    if (not raw["verified"] or not raw["catalog_complete"] or raw.get("status") != "READ_COMPLETE"
            or raw.get("physical_state_unchanged") is not True or raw.get("physical_before") != raw.get("physical_after")):
        raise ValueError("Complete read lacks stable physical scope")
    actions, dialogs = raw.get("actions"), raw.get("override_instances")
    if (type(actions) is not list or len(actions) != len(ACTION_NAMES)
            or any(type(row) is not dict or row.get("object_name") != name or row.get("triggered") is not False
                   for row, name in zip(actions, ACTION_NAMES))
            or type(dialogs) is not list or len(dialogs) > 4
            or type(raw.get("override_instance_count")) is not int or raw["override_instance_count"] != len(dialogs)):
        raise ValueError("Fixed actions/Override instance scope differs")
    for row in dialogs:
        if (type(row) is not dict or row.get("class_name") not in ("DialogOverrideSettings", "Marvelous::DialogOverrideSettings")
                or row.get("rest_scope_semantics_verified") is not False or row.get("solver_rest_value_read") is not False
                or row.get("update_reset_authorized") is not False or type(row.get("rest_frame_controls")) is not list
                or len(row["rest_frame_controls"]) > 256):
            raise ValueError("Unexpected Rest instance or semantic claim")
        meta = row.get("metadata", {})
        if (meta.get("declarations_complete") is not True or meta.get("public_declaration_is_callable_proof") is not False
                or type(meta.get("own_methods")) is not list or len(meta["own_methods"]) > 512
                or type(meta.get("own_properties")) is not list or len(meta["own_properties"]) > 128
                or any(method.get("invoked") is not False for method in meta["own_methods"])
                or any(prop.get("value_read") is not False for prop in meta["own_properties"])):
            raise ValueError("Unreviewed property getter/invocation or incomplete metadata")


def _control_name(value):
    return type(value) is str and len(value) <= 256


def _validate_host_controls(raw, code, process):
    _validate(raw, code, process, schema=HOST_CONTROLS_SCHEMA)
    if code != 0:
        return
    for dialog in raw["override_instances"]:
        controls, count = dialog.get("host_standard_controls"), dialog.get("host_descendants_scanned")
        path = dialog.get("owner_path")
        if (dialog.get("current_mainwindow_in_parent_chain") is not True or type(path) is not list
                or not any(type(row) is dict and row.get("class_name") == "MDMainWindow" and row.get("object_name") == "CLO" for row in path[:-1])
                or type(controls) is not list or type(count) is not int or not len(controls) <= min(count, 128)
                or count > 2047):
            raise ValueError("Bounded current-mainwindow Rest host catalog required")
        for row in controls:
            if type(row) is not dict:
                raise ValueError("Invalid Rest host control")
            role, path = row.get("standard_control"), row.get("path_from_host")
            if (role not in ("QAbstractButton", "QComboBox", "QLabel", "QGroupBox", "QAction")
                    or not _control_name(row.get("class_name")) or not _control_name(row.get("object_name"))
                    or type(path) is not list or not 1 <= len(path) <= 16
                    or any(type(node) is not list or len(node) != 2 or not all(map(_control_name, node)) for node in path)
                    or path[-1] != [row["class_name"], row["object_name"]]
                    or type(row.get("text")) is not str or len(row["text"]) > 1024
                    or any(type(row.get(key)) is not bool for key in ("visible", "enabled", "text_truncated"))):
                raise ValueError("Invalid bounded standard Qt control")
            if role in ("QAbstractButton", "QAction", "QGroupBox") and any(type(row.get(key)) is not bool for key in ("checkable", "checked")):
                raise ValueError("Invalid standard Qt check state")
            if role == "QComboBox":
                items, total, current = (row.get(key) for key in ("items", "item_count", "current_index"))
                if (type(total) is not int or total < 0 or type(current) is not int or not -1 <= current < total
                        or type(items) is not list or len(items) != min(total, 16)
                        or any(type(item) is not str or len(item) > 1024 for item in items)):
                    raise ValueError("Invalid bounded standard Qt combo options")


def _failure(code, message, status="REJECTED"):
    return {"status": status, "result": {"verified": False, "RestShape_write_contract_verified": False},
            "effects_started": False, "requires_recovery": False, "error": {"code": code, "message": message}}


def inspect_host_controls(params):
    return _inspect(params, _host_controls_state, _load_host_controls, _validate_host_controls)


def _inspect(params, state, load, validate):
    try:
        if type(params) is not dict or not {"scene_token"} <= params.keys() or params.keys() - {"scene_token", "budget_ms"}:
            raise ValueError("Only current scene_token and bounded budget_ms are accepted")
        token, budget = params["scene_token"], params.get("budget_ms", 750)
        if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(budget) is not int or not 50 <= budget <= 1500:
            raise ValueError("Current token and integer budget [50,1500] required")
        if state()["configuration"] is None:
            return _failure("NATIVE_BRIDGE_NOT_CONFIGURED", "Root must install the reviewed Rest diagnostic", "UNSUPPORTED")
        before = _base._scene()
        if before["scene_token"] != token:
            return _failure("STALE_SCENE", "Read the current scene again")
        environment = _base._environment()
        call = load(environment)
        output = ctypes.create_string_buffer(CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        process = environment["process"]
        code = call(process["pid"], process["creation_filetime"], budget, output, len(output), ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4):
            return _failure("NATIVE_PREFLIGHT_REJECTED", "ABI/process/GUI thread/busy rejected before diagnostic")
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1:
            raise ValueError("Native output bounds differ")
        raw = _base._json(output.raw[:written.value].decode("utf8"))
        validate(raw, code, process)
        after = _base._scene()
        if before["scene_token"] != after["scene_token"]:
            return _failure("SCENE_CHANGED_DURING_READ", "Diagnostic did not establish a stable current scene")
        raw["scene_token"] = token
        raw["verification_scope"] = "Existing Override instance declarations and standard controls only; no solver-rest value, callable, native selection or full mesh proof"
        return {"status": "SUCCEEDED" if raw["verified"] else "REJECTED", "result": raw, "effects_started": False,
                "requires_recovery": False, "error": None if raw["verified"] else {"code": "READ_NOT_VERIFIED", "message": raw.get("reason", "Incomplete diagnostic")}}
    except Exception as error:
        return _failure("REST_DIAGNOSTIC_FAILED", str(error))


def _validate_basic_scalar(value):
    if (type(value) is not dict or type(value.get("valid")) is not bool or type(value.get("scalar")) is not bool
            or type(value.get("type_id")) is not int or type(value.get("type_name")) is not str):
        raise ValueError("Typed basic Qt scalar descriptor required")
    if value["scalar"]:
        item = value.get("value")
        import math
        if (not value["valid"] or type(item) not in (bool, int, float, str)
                or type(item) in (int, float) and (not math.isfinite(item) or type(item) is int and abs(item) > 9007199254740991)
                or type(item) is str and len(item) > 1024):
            raise ValueError("Only bounded basic finite Qt scalar values are read")
    elif "value" in value:
        raise ValueError("Unsupported custom Qt type cannot carry a converted value")


OPERATIONS = {"rest_adapter.inspect_host_controls": inspect_host_controls}
