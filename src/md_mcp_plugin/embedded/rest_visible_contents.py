"""Independent visible-Contents reader and one fixed Simulation Properties command.

No prior Rest DLL is replaced. The command changes the property page only;
it does not open menus, start simulation or write solver Rest.
"""
from __future__ import annotations

import copy
import ctypes
import hashlib
import math
from pathlib import Path
import re
import sys

from . import canvas as _base
from .rest_adapter import _validate_basic_scalar

SCHEMA = "md-qt-rest-visible-contents/1"
OPEN_SCHEMA = "md-qt-rest-open-properties/1"
STATE_KEY = "_md_mcp_plugin_rest_visible_contents_state"
CAPACITY = 1048576
ZERO_COUNTS = ("event_calls", "QAction_trigger_calls", "method_invoke_calls", "QMetaProperty_read_calls",
               "property_write_calls", "OS_input_calls", "focus_calls", "simulation_calls", "widget_create_show_calls")
ACTION_NAMES = ("actionSet_Simulation_Property", "actionSoftSelection", "actionSimulationBrushPinching",
                "actionBrushPinching", "actionPinchBrush", "actionMove_Pattern")


def _state():
    value = getattr(sys, STATE_KEY, None)
    if value is None:
        value = {"configuration": None, "library": None, "calls": {}, "process": None, "blocked": False}
        setattr(sys, STATE_KEY, value)
    return value


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_native_bridge(path, sha256):
    """Root-owned configuration only. No load or old-module replacement."""
    file = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None or not file.is_absolute()
            or file.name != "md_qt_rest_visible_contents_bridge.dll" or file.is_symlink()
            or not file.is_file() or _sha(file) != sha256):
        raise ValueError("One unchanged pinned visible-Contents DLL required")
    config = {"path": str(file.resolve()), "sha256": sha256}
    state = _state()
    if state["blocked"] or state["library"] is not None and state["configuration"] != config:
        raise ValueError("No hot swap or clearing an uncertain command")
    state["configuration"] = config
    return {"configured": True, "loaded": state["library"] is not None, "live_validation": "pending"}


def _load(environment, opening):
    state = _state(); config = state["configuration"]
    if config is None or _sha(config["path"]) != config["sha256"]:
        raise ValueError("Configure the unchanged reviewed visible-Contents DLL")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        version = library.mdqt_rest_visible_contents_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 1:
            raise ValueError("Visible-Contents C ABI1 required")
        calls = {}
        for key, name in ((False, "mdqt_rest_visible_contents_inspect"),
                          (True, "mdqt_rest_visible_contents_open_simulation_properties")):
            call = getattr(library, name)
            call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                             ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
            call.restype = ctypes.c_int; calls[key] = call
        state.update(library=library, calls=calls, process=dict(environment["process"]))
    if state["process"] != environment["process"]:
        raise ValueError("Visible-Contents DLL belongs to another process lifetime")
    return state["calls"][opening]


def _identity(raw, schema, code, process):
    if (type(raw) is not dict or raw.get("schema") != schema or type(raw.get("return_code")) is not int
            or raw["return_code"] != code or type(raw.get("verified")) is not bool
            or type(raw.get("process_id")) is not int or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])):
        raise ValueError("Typed native envelope/process differs")


def _numeric_controls(root):
    controls = root.get("numeric_controls", [])
    if type(controls) is not list or len(controls) > 128:
        raise ValueError("Bounded numeric controls required")
    for control in controls:
        if (control.get("active_visible_page") is not True or control.get("numeric_owner_binding_verified") is not False
                or control.get("write_authorized") is not False or control.get("object", {}).get("visible") is not True):
            raise ValueError("Only active-visible controls, without a native ownership/write claim")
        kind = control.get("standard_control")
        if kind in ("QDoubleSpinBox", "QSpinBox"):
            values = [control.get(k) for k in ("value", "minimum", "maximum", "single_step")]
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                    or not values[1] <= values[0] <= values[2] or values[3] < 0
                    or type(control.get("decimals")) is not int or not 0 <= control["decimals"] <= 323
                    or kind == "QSpinBox" and any(type(v) is not int for v in values)):
                raise ValueError("Invalid numeric precision/range/value")
        elif kind == "QLineEdit":
            if (type(control.get("echo_mode")) is not int or type(control.get("text_read")) is not bool
                    or control["text_read"] is not (control["echo_mode"] == 0)
                    or control["text_read"] and (type(control.get("text")) is not str or len(control["text"]) > 1024)
                    or not control["text_read"] and "text" in control):
                raise ValueError("Only normal-echo bounded line-edit text can be read")
        else:
            raise ValueError("Unexpected numeric/text control")
    return len(controls)


def _validate_read(raw, code, process):
    _identity(raw, SCHEMA, code, process)
    if (raw.get("diagnostic_profile") != "visible_property_editor_contents_pruned_v1"
            or raw.get("traversal_scope") != "visible_PropertyEditorContents_active_widgets_only"
            or any(type(raw.get(k)) is not int or raw[k] != 0 for k in ZERO_COUNTS)
            or any(raw.get(k) is not False for k in ("effects_started", "requires_recovery", "action_entry_authorized",
                "original_page_restoration_contract_verified", "RestShape_write_contract_verified", "solver_rest_value_read",
                "model_data_preservation_verified"))):
        raise ValueError("Visible-Contents read profile or zero-write claims differ")
    if code != 0:
        if raw["verified"] or raw.get("catalog_complete") is not False or raw.get("status") != "READ_NOT_VERIFIED":
            raise ValueError("Incomplete read upgraded to success")
        return
    roots = raw.get("property_roots")
    if (not raw["verified"] or raw.get("catalog_complete") is not True or raw.get("status") != "READ_COMPLETE"
            or raw.get("physical_state_unchanged") is not True or raw.get("physical_before") != raw.get("physical_after")
            or type(roots) is not list or len(roots) != 1 or raw.get("property_root_count") != 1):
        raise ValueError("One qualified current Contents root required")
    root = roots[0]; identity = root.get("root", {})
    if (identity.get("object_name") != "PropertyEditorContents" or identity.get("class_name") != "QWidget"
            or identity.get("visible") is not True or root.get("scan_complete") is not True or root.get("truncated") is not False
            or root.get("write_authorized") is not False or root.get("page_restore_authorized") is not False
            or len(root.get("controls", [])) > 256):
        raise ValueError("Actual QWidget/PropertyEditorContents binding differs")
    count = _numeric_controls(root); row_count = 0
    for model in root.get("item_models", []):
        if (model.get("numeric_active_scope") is not True or model.get("view", {}).get("visible") is not True
                or type(model.get("setData_calls")) is not int or model["setData_calls"] != 0
                or model.get("value_roles") != ["DisplayRole", "EditRole", "CheckStateRole"]
                or model.get("row_limit") != 128 or model.get("column_limit") != 3):
            raise ValueError("Only current bounded basic-role model read accepted")
        rows = model.get("numeric_rows", []); row_count += len(rows)
        for row in rows:
            if (row.get("active_visible_page") is not True or row.get("numeric_owner_binding_verified") is not False
                    or row.get("write_authorized") is not False or len(row.get("cells", [])) > 3):
                raise ValueError("Invalid visible numeric model row")
            for cell in row["cells"]:
                for role in ("display", "edit", "checkstate"):
                    _validate_basic_scalar(cell.get(role))
    if (row_count > 128 or type(raw.get("numeric_model_rows_read")) is not int or raw["numeric_model_rows_read"] != row_count
            or type(raw.get("numeric_standard_controls_read")) is not int or raw["numeric_standard_controls_read"] != count):
        raise ValueError("Numeric read totals differ")


def _validate_open(raw, code, process):
    _identity(raw, OPEN_SCHEMA, code, process)
    calls = raw.get("QAction_trigger_calls")
    if (raw.get("action_object_name") != "actionSet_Simulation_Property"
            or type(calls) is not int or calls not in (0, 1) or raw.get("effects_started") is not bool(calls)
            or any(type(raw.get(k)) is not int or raw[k] != 0 for k in ("event_calls", "OS_input_calls", "focus_calls",
                "simulation_calls", "show_raise_activate_calls", "property_write_calls"))
            or raw.get("RestShape_write_contract_verified") is not False or raw.get("model_data_preservation_verified") is not False
            or raw.get("original_property_page_restore_required") is not False or raw.get("automatic_retry_allowed") is not False):
        raise ValueError("Only the single fixed property-action command is allowed")
    if code != 0:
        if raw["verified"] or raw.get("requires_recovery") is not bool(calls) or raw.get("status") != ("UNKNOWN" if calls else "REJECTED"):
            raise ValueError("Incomplete command must retain its actual effects/unknown state")
        return
    if (calls != 1 or not raw["verified"] or raw.get("requires_recovery") is not False or raw.get("status") != "COMMAND_RETURNED"
            or any(raw.get(k) is not True for k in ("action_returned", "main_window_minimized_before", "main_window_minimized_after",
                "physical_state_unchanged", "current_owner_and_contents_references_preserved", "action_owner_reference_verified"))
            or raw.get("physical_before") != raw.get("physical_after")
            or type(raw.get("no_effect_observed")) is not bool or type(raw.get("property_page_observation_changed")) is not bool
            or raw["no_effect_observed"] is raw["property_page_observation_changed"]):
        raise ValueError("Fixed property command did not prove its actual scope")
    _validate_read(raw.get("before_read"), 0, process); _validate_read(raw.get("after_read"), 0, process)
    expected_same = _page_observation(raw["before_read"]["property_roots"]) == _page_observation(raw["after_read"]["property_roots"])
    if raw["no_effect_observed"] is not expected_same:
        raise ValueError("NoEffect observation contradicts actual page data")


def _page_observation(roots):
    bookkeeping = {"descendants_scanned", "pruned_hidden_widgets", "pruned_nonwidget_children",
                   "pruned_inactive_stack_pages", "emitted_standard_controls", "last_object", "read_failure_reason"}
    return [{key: value for key, value in root.items() if key not in bookkeeping} for root in roots]


def _native_snapshot():
    # Existing readonly reader; no Cut function, index trial, import or project save.
    from .cut import _api, _capture
    return _capture(_api())


def _preservation(before, after):
    from .common import digest
    return {"scope": "all-owner projected native 2D non-baseline geometry plus complete fresh pattern runtime records",
            "native_2d_geometry_unchanged_verified": before["fingerprints"] == after["fingerprints"],
            "runtime_records_unchanged_verified": before["rows"] == after["rows"],
            "unprojected_native_json_equal": before["native"] == after["native"],
            "before_native_2d_fingerprint": before["scene_fingerprint"], "after_native_2d_fingerprint": after["scene_fingerprint"],
            "before_full_native_digest": digest(before["native"]), "after_full_native_digest": digest(after["native"]),
            "full_3d_or_solver_rest_preservation_verified": False}


def _failure(code, message, raw=None, *, unknown=False, status=None):
    result = {"verified": False, "RestShape_write_contract_verified": False}
    if type(raw) is dict:
        result.update(unverified_native_diagnostic=raw, native_diagnostic_is_unverified=True)
    return {"status": status or ("UNKNOWN" if unknown else "REJECTED"), "result": result,
            "effects_started": unknown, "requires_recovery": unknown, "error": {"code": code, "message": message}}


def _semantic_entry_verified(raw, process):
    """The page-entry command can finish while its numeric tail remains unread."""
    if (raw.get("reason") != "AFTER_CONTENTS_NOT_QUALIFIED" or raw.get("action_returned") is not True
            or type(raw.get("QAction_trigger_calls")) is not int or raw["QAction_trigger_calls"] != 1
            or any(raw.get(key) is not True for key in ("main_window_minimized_before", "main_window_minimized_after",
                "physical_state_unchanged", "current_owner_and_contents_references_preserved", "action_owner_reference_verified"))
            or type(raw.get("physical_before")) is not dict or raw["physical_before"] != raw.get("physical_after")):
        return False
    proof = raw.get("native_preservation", {})
    if (proof.get("native_2d_geometry_unchanged_verified") is not True
            or proof.get("runtime_records_unchanged_verified") is not True):
        return False
    _validate_read(raw.get("before_read"), 0, process)
    after = raw.get("after_read", {}); _validate_read(after, 1, process)
    if after.get("reason") != "OWNER_PATH_LIMIT": return False
    roots = after.get("property_roots")
    if type(roots) is not list or len(roots) != 1 or type(after.get("property_root_count")) is not int or after["property_root_count"] != 1:
        return False
    root = roots[0]; identity = root.get("root", {})
    if (identity.get("class_name") != "QWidget" or identity.get("object_name") != "PropertyEditorContents"
            or identity.get("visible") is not True or root.get("write_authorized") is not False
            or root.get("page_restore_authorized") is not False or type(root.get("controls")) is not list
            or len(root["controls"]) > 256):
        return False
    controls = root["controls"]
    labels = [row for row in controls if type(row) is dict and row.get("standard_control") == "QLabel"
              and row.get("visible") is True and type(row.get("text")) is str]
    title = [row for row in labels if row["text"] == "Simulation Properties"]
    pinching = [row for row in labels if row["text"] == "Pinching Simulation Override"]
    return (len(title) == 1 and len(pinching) == 1
            and pinching[0].get("parent", {}).get("class_name") == "MVPropertyWidget"
            and pinching[0].get("parent", {}).get("object_name") == "MVCheckbox"
            and pinching[0].get("parent", {}).get("visible") is True)


def _execute(params, opening):
    raw = None; invoked = False; effects = False
    try:
        if type(params) is not dict or not {"scene_token"} <= params.keys() or params.keys() - {"scene_token", "budget_ms"}:
            raise ValueError("Only scene_token and bounded budget accepted")
        token, budget = params["scene_token"], params.get("budget_ms", 750)
        if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(budget) is not int or not 50 <= budget <= 1500:
            raise ValueError("Current token and integer budget [50,1500] required")
        state = _state()
        if state["configuration"] is None:
            return _failure("VISIBLE_CONTENTS_NOT_CONFIGURED", "Root must configure this independent DLL", status="UNSUPPORTED")
        if opening and state["blocked"]:
            return _failure("COMMAND_RECOVERY_REQUIRED", "Previous uncertain action cannot be retried", unknown=True)
        before = _base._scene()
        if before["scene_token"] != token:
            return _failure("STALE_SCENE", "Read current scene again")
        native_before = _native_snapshot() if opening else None
        environment = _base._environment(); process = environment["process"]; call = _load(environment, opening)
        size = CAPACITY * (2 if opening else 1)
        output = ctypes.create_string_buffer(size); written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        invoked = True
        code = call(process["pid"], process["creation_filetime"], budget, output, size, ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4):
            invoked = False
            return _failure("NATIVE_PREFLIGHT_REJECTED", "ABI/process/GUI thread/busy preflight rejected")
        if not 0 < written.value < size or needed.value != written.value + 1:
            raise ValueError("Native output bounds differ")
        raw = _base._json(output.raw[:written.value].decode("utf8"))
        native_command_receipt = copy.deepcopy(raw) if opening else None
        (_validate_open if opening else _validate_read)(raw, code, process)
        effects = opening and raw["effects_started"]
        if opening and effects:
            native_after = _native_snapshot(); raw["native_preservation"] = _preservation(native_before, native_after)
        after = _base._scene()
        if after["scene_token"] != token:
            raise ValueError("Scene runtime changed during property command/read")
        if opening and raw["verified"]:
            proof = raw["native_preservation"]
            if not proof["native_2d_geometry_unchanged_verified"] or not proof["runtime_records_unchanged_verified"]:
                raise ValueError("Actual native 2D/runtime preservation failed")
        raw["scene_token"] = token
        if opening and code == 2 and _semantic_entry_verified(raw, process):
            result = {"schema": "md-rest-simulation-properties-entry/1", "verified": True, "entry_verified": True,
                      "catalog_complete": False, "numeric_catalog_complete": False,
                      "native_command_receipt": native_command_receipt, "native_preservation": raw["native_preservation"],
                      "scene_token": token, "QAction_trigger_calls": 1, "action_returned": True,
                      "entry_observation_scope": "unique visible Contents / Simulation Properties title / Pinching Override label row",
                      "checkbox_value_or_row_binding_verified": False, "RestShape_write_contract_verified": False}
            return {"status": "SUCCEEDED", "result": result, "effects_started": True, "requires_recovery": False, "error": None}
        if opening and raw.get("requires_recovery"):
            state["blocked"] = True
        return {"status": "SUCCEEDED" if raw["verified"] else ("UNKNOWN" if effects else "REJECTED"), "result": raw,
                "effects_started": effects, "requires_recovery": bool(opening and raw.get("requires_recovery")),
                "error": None if raw["verified"] else {"code": "PROPERTY_COMMAND_NOT_VERIFIED" if opening else "READ_NOT_VERIFIED",
                                                        "message": raw.get("reason", "Incomplete scope")}}
    except Exception as error:
        unknown = bool(opening and (effects or invoked))
        if unknown: _state()["blocked"] = True
        return _failure("PROPERTY_COMMAND_FAILED" if opening else "VISIBLE_CONTENTS_READ_FAILED", str(error), raw, unknown=unknown)


def inspect(params):
    return _execute(params, False)


def open_simulation_properties(params):
    return _execute(params, True)


OPERATIONS = {"rest_visible_contents.inspect": inspect,
              "rest_visible_contents.open_simulation_properties": open_simulation_properties}
