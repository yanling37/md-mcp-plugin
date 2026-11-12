"""Readonly local-path reader and explicit reconciliation of a cached UI command.

No action export is loaded. Never replays an action or rewrites dispatch receipts.
The host alone validates/releases its parent journal lease after consuming proof.
"""
from __future__ import annotations

import copy
import ctypes
import hashlib
from pathlib import Path
import re
import sys

from . import canvas as _base
from . import rest_visible_contents as _legacy

SCHEMA = "md-qt-rest-contents-local/1"
STATE_KEY = "_md_mcp_plugin_rest_visible_recovery_state"
CAPACITY = 1048576


def _state():
    value = getattr(sys, STATE_KEY, None)
    if value is None:
        value = {"configuration": None, "library": None, "call": None, "process": None}
        setattr(sys, STATE_KEY, value)
    return value


def _digest(value):
    return hashlib.sha256(_base._canonical(value).encode("utf8")).hexdigest()


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_native_bridge(path, sha256):
    """Root-owned new reader configuration; bypasses no old blocked guard."""
    file = Path(path)
    if (type(sha256) is not str or re.fullmatch("[0-9a-f]{64}", sha256) is None or not file.is_absolute()
            or file.name != "md_qt_rest_contents_local_bridge.dll" or file.is_symlink()
            or not file.is_file() or _hash(file) != sha256):
        raise ValueError("One unchanged pinned local-Contents readonly DLL required")
    config = {"path": str(file.resolve()), "sha256": sha256}
    state = _state()
    if state["library"] is not None and state["configuration"] != config:
        raise ValueError("Loaded recovery reader cannot be hot-swapped")
    state["configuration"] = config
    return {"configured": True, "loaded": state["library"] is not None, "old_DLLs_replaced": False}


def _load(environment):
    state = _state(); config = state["configuration"]
    if config is None or _hash(config["path"]) != config["sha256"]:
        raise ValueError("Configure unchanged local-path reader")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        version = library.mdqt_rest_contents_local_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 1: raise ValueError("Local-Contents C ABI1 required")
        call = library.mdqt_rest_contents_local_inspect
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(environment["process"]))
    if state["process"] != environment["process"]: raise ValueError("Reader belongs to another MD lifetime")
    return state["call"]


def _path(path):
    if (type(path) is not list or not 1 <= len(path) <= 64
            or path[0].get("class_name") != "QWidget" or path[0].get("object_name") != "PropertyEditorContents"
            or path[0].get("fixed_contents_root") is not True):
        raise ValueError("Owner path must reach the fixed Contents root, without truncation")
    for row in path[1:]:
        if (type(row.get("class_name")) is not str or type(row.get("object_name")) is not str
                or type(row.get("sibling_object_index")) is not int or row["sibling_object_index"] < 0
                or type(row.get("sibling_widget_index")) is not int or row["sibling_widget_index"] < -1):
            raise ValueError("Current sibling ordinals required for anonymous owner rows")


def _validate_read(raw, code, process):
    if (type(raw) is not dict or raw.get("schema") != SCHEMA
            or raw.get("diagnostic_profile") != "visible_contents_local_owner_paths_v1"
            or raw.get("owner_path_scope") != "fixed_PropertyEditorContents_root_inclusive"):
        raise ValueError("Local-root reader schema/scope required")
    normalized = dict(raw, schema=_legacy.SCHEMA, diagnostic_profile="visible_property_editor_contents_pruned_v1")
    _legacy._validate_read(normalized, code, process)  # Pure JSON validation only; never old install/load/open.
    if code == 0:
        if raw.get("root_local_paths_complete") is not True: raise ValueError("Local owner paths incomplete")
        def walk(value):
            if type(value) is dict:
                for key, item in value.items():
                    if key in ("owner_path", "parent_owner_path", "row_owner_path"): _path(item)
                    else: walk(item)
            elif type(value) is list:
                for item in value: walk(item)
        walk(raw["property_roots"])


def _read(environment, budget):
    call = _load(environment); process = environment["process"]
    buffer = ctypes.create_string_buffer(CAPACITY); written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = call(process["pid"], process["creation_filetime"], budget, buffer, len(buffer), ctypes.byref(written), ctypes.byref(needed))
    if not written.value and code in (-1, -2, -3, -4): raise ValueError("Readonly native preflight rejected")
    if not 0 < written.value < CAPACITY or needed.value != written.value + 1: raise ValueError("Reader bounds differ")
    raw = _base._json(buffer.raw[:written.value].decode("utf8")); _validate_read(raw, code, process)
    return raw


def _params(params, reconciliation=False):
    required = {"scene_token", "blocked_request_id"} if reconciliation else {"scene_token"}
    if type(params) is not dict or not required <= params.keys() or params.keys() - required - {"budget_ms"}:
        raise ValueError("Only current token, bounded budget and requested blocked ID accepted")
    token, budget = params["scene_token"], params.get("budget_ms", 750)
    if type(token) is not str or re.fullmatch("[0-9a-f]{64}", token) is None or type(budget) is not int or not 50 <= budget <= 1500:
        raise ValueError("Current token and integer budget [50,1500] required")
    if reconciliation and (type(params["blocked_request_id"]) is not str or re.fullmatch("[0-9a-f]{32}", params["blocked_request_id"]) is None):
        raise ValueError("Blocked request must be lowercase UUID hex")
    return token, budget


def _failure(message, raw=None):
    result = {"verified": False, "legacy_blocked_state_cleared": False}
    if type(raw) is dict: result.update(unverified_native_diagnostic=raw, native_diagnostic_is_unverified=True)
    return {"status": "REJECTED", "result": result, "effects_started": False, "requires_recovery": False,
            "error": {"code": "PROPERTY_RECONCILIATION_NOT_VERIFIED", "message": str(message)}}


def inspect(params):
    raw = None
    try:
        token, budget = _params(params)
        if _base._scene()["scene_token"] != token: raise ValueError("Stale scene token")
        raw = _read(_base._environment(), budget)
        if _base._scene()["scene_token"] != token: raise ValueError("Scene changed during readonly inspection")
        raw["scene_token"] = token
        return {"status": "SUCCEEDED" if raw["verified"] else "REJECTED", "result": raw,
                "effects_started": False, "requires_recovery": False,
                "error": None if raw["verified"] else {"code": "READ_NOT_VERIFIED", "message": raw.get("reason", "Incomplete reader")}}
    except Exception as error: return _failure(error, raw)


def _cached(request_id):
    from . import dispatch
    value = dispatch._RECEIPTS.get(request_id); fingerprint = dispatch._PAYLOADS.get(request_id)
    if type(value) is not dict or type(fingerprint) is not str or re.fullmatch("[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("Original embedded dispatch receipt/payload fingerprint is absent")
    snapshot = copy.deepcopy(value)
    return snapshot, _digest(snapshot), fingerprint


def _validate_cached(receipt, request_id, process):
    if (receipt.get("protocol") != "md-mcp-plugin/1" or receipt.get("operation_id") != request_id
            or receipt.get("status") != "UNKNOWN" or receipt.get("effects_started") is not True
            or receipt.get("requires_recovery") is not True
            or receipt.get("error", {}).get("code") != "PROPERTY_COMMAND_NOT_VERIFIED"):
        raise ValueError("Original receipt is not the matching cached unknown property command")
    raw = receipt.get("result"); _legacy._validate_open(raw, 2, process)
    if (raw.get("reason") != "AFTER_CONTENTS_NOT_QUALIFIED" or raw.get("action_returned") is not True
            or raw.get("QAction_trigger_calls") != 1
            or any(raw.get(k) is not True for k in ("main_window_minimized_before", "main_window_minimized_after",
                "physical_state_unchanged", "current_owner_and_contents_references_preserved", "action_owner_reference_verified"))
            or raw.get("physical_before") != raw.get("physical_after")):
        raise ValueError("Original known action return/physical/ref conditions are not closed")
    _legacy._validate_read(raw.get("before_read"), 0, process)
    after = raw.get("after_read", {}); _legacy._validate_read(after, 1, process)
    if after.get("reason") != "OWNER_PATH_LIMIT": raise ValueError("Only the known owner-path diagnostic failure is reconciled")
    proof = raw.get("native_preservation", {})
    if (proof.get("native_2d_geometry_unchanged_verified") is not True or proof.get("runtime_records_unchanged_verified") is not True
            or proof.get("before_native_2d_fingerprint") != proof.get("after_native_2d_fingerprint")
            or type(proof.get("after_native_2d_fingerprint")) is not str
            or re.fullmatch("[0-9a-f]{64}", proof["after_native_2d_fingerprint"]) is None
            or type(raw.get("scene_token")) is not str or re.fullmatch("[0-9a-f]{64}", raw["scene_token"]) is None):
        raise ValueError("Original Native2D/runtime preservation is not established")
    roots = after.get("property_roots")
    if type(roots) is not list or len(roots) != 1 or type(roots[0].get("controls")) is not list:
        raise ValueError("Original partial property-page data is absent")
    return raw


def _project(row, numeric=False):
    keys = ("standard_control", "value", "minimum", "maximum", "single_step", "decimals", "read_only", "prefix", "suffix",
            "text", "echo_mode", "text_read", "acceptable_input", "labels") if numeric else (
            "class_name", "object_name", "standard_control", "text", "checkable", "checked", "enabled", "visible", "count", "current_index", "parent")
    return {key: row[key] for key in keys if key in row}


def _match_partial(original, current):
    before = original["after_read"]["property_roots"][0]; after = current["property_roots"][0]
    for key, numeric in (("controls", False), ("numeric_controls", True)):
        old = before.get(key, []); new = after.get(key, [])
        if type(old) is not list or len(new) < len(old) or [_project(x, numeric) for x in old] != [_project(x, numeric) for x in new[:len(old)]]:
            raise ValueError("Current page does not preserve the original dynamically captured partial prefix")


def _pinching(current):
    controls = current["property_roots"][0]["controls"]
    labels = [row for row in controls if row.get("standard_control") == "QLabel" and row.get("text") == "Pinching Simulation Override"]
    titles = [row for row in controls if row.get("standard_control") == "QLabel" and row.get("text") == "Simulation Properties"]
    if len(labels) != 1 or len(titles) != 1: raise ValueError("Current Simulation Properties/Override page identity is not unique")
    label = labels[0]; parent_path = label.get("parent_owner_path"); _path(parent_path)
    buttons = [row for row in controls if row.get("standard_control") == "QAbstractButton" and row.get("checkable") is True
               and row.get("parent_owner_path") == parent_path]
    if len(buttons) != 1 or type(buttons[0].get("checked")) is not bool: raise ValueError("Override checkbox row binding is not unique")
    button = buttons[0]
    return {"label": label["text"], "checked": button["checked"], "enabled": button.get("enabled"), "text": button.get("text"),
            "control_class": button.get("class_name"), "row_owner_path": parent_path,
            "label_owner_path": label.get("owner_path"), "checkbox_owner_path": button.get("owner_path"), "write_authorized": False}


def reconcile_open(params):
    current = None
    try:
        token, budget = _params(params, True); request_id = params["blocked_request_id"]
        if _base._scene()["scene_token"] != token: raise ValueError("Current scene token is stale")
        environment = _base._environment(); process = environment["process"]
        receipt, receipt_sha, payload_sha = _cached(request_id)
        original = _validate_cached(receipt, request_id, process)
        if original["scene_token"] != token: raise ValueError("Current scope/runtime differs from the original completed command")
        legacy = _legacy._state(); old_config = copy.deepcopy(legacy.get("configuration")); old_process = copy.deepcopy(legacy.get("process"))
        previous = legacy.get("reconciliations", {}).get(request_id)
        already = (type(previous) is dict and previous.get("verified") is True and previous.get("original_cached_receipt_sha256") == receipt_sha)
        if (legacy.get("process") != process or legacy.get("library") is None or type(legacy.get("blocked")) is not bool
                or not legacy["blocked"] and not already):
            raise ValueError("Matching original loaded/blocked legacy execution state is unavailable")
        blocked_before = legacy["blocked"]
        native_before = _legacy._native_snapshot()
        current = _read(environment, budget)
        if (current.get("verified") is not True or current.get("main_window_minimized_before") is not True
                or current.get("main_window_minimized_after") is not True): raise ValueError("Fresh complete minimized readonly page is not established")
        native_after = _legacy._native_snapshot()
        target = original["native_preservation"]["after_native_2d_fingerprint"]
        if (native_before["scene_fingerprint"] != target or native_after["scene_fingerprint"] != target
                or native_before["rows"] != native_after["rows"] or _base._scene()["scene_token"] != token):
            raise ValueError("Current Native2D/runtime does not match original after scope")
        _match_partial(original, current); pinching = _pinching(current)
        now, now_sha, now_payload = _cached(request_id)
        if now_sha != receipt_sha or now_payload != payload_sha: raise ValueError("Original cache changed during reconciliation")
        if legacy.get("configuration") != old_config or legacy.get("process") != old_process or legacy.get("blocked") is not blocked_before:
            raise ValueError("Legacy execution state changed during reconciliation")
        proof = {"verified": True, "recovered_operation_id": request_id, "original_status": "UNKNOWN", "original_UNKNOWN_preserved": True,
                 "original_requires_recovery_preserved": True, "original_cached_receipt_sha256": receipt_sha,
                 "original_payload_fingerprint": payload_sha, "original_trigger_calls": 1, "replayed_trigger_calls": 0,
                 "new_native_action_calls": 0, "native_2d_runtime_matches_original_after": True,
                 "original_partial_controls_preserved": True, "original_partial_control_count": len(original["after_read"]["property_roots"][0]["controls"]),
                 "current_read": current, "pinching_override": pinching, "scene_token": token,
                 "legacy_blocked_state_before": blocked_before, "legacy_blocked_state_after": False,
                 "legacy_blocked_state_cleared": blocked_before, "already_reconciled": already,
                 "host_parent_lease_released_by_this_module": False, "RestShape_write_contract_verified": False,
                 "full_3d_or_solver_rest_preservation_verified": False}
        # Last step only: metadata bookkeeping, never original receipt/cache/lease/DLL changes.
        legacy.setdefault("reconciliations", {})[request_id] = copy.deepcopy(proof)
        legacy["blocked"] = False
        return {"status": "SUCCEEDED", "result": proof, "effects_started": False, "requires_recovery": False, "error": None}
    except Exception as error: return _failure(error, current)


OPERATIONS = {"rest_visible_recovery.inspect": inspect, "rest_visible_recovery.reconcile_open": reconcile_open}
