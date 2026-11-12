"""Trusted current-2D adapter. No native work occurs on import/configuration.

No public parameter accepts a DLL, HWND, pointer, PNG path, QAction name or code.
The core must explicitly install a pinned bridge and an internal artifact root.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import uuid

from . import canvas as _base

SCHEMA = "md-qt-scene-adapter/6"
PREFIX = "mdscene6:"
PROFILE = "bezier_handle_alt_move_v1"
UNSNAPPED_PROFILE = "bezier_handle_alt_move_unsnapped_v1"
PRECISE_PROFILE = "bezier_handle_precise_alt_move_unsnapped_v1"
ZOOM_PROFILE = "zoom_in_at_hover_then_viewport_vertical_minus120_v1"
ZOOM_OUT_PROFILE = "zoom_out_at_hover_then_viewport_vertical_plus120_v1"
FIT_ALL_ACTION = "actionShow_All_Patterns_in_Window"
TOOLS = ("transform_pattern", "pattern_edit", "edit_curve_point")
OUTPUT_CAPACITY = 1048576
STATE_KEY = "_md_mcp_plugin_scene_adapter_r6_state"


class AdapterError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _state():
    for key in ("_md_mcp_plugin_scene_adapter_state", "_md_mcp_plugin_scene_adapter_r3_state",
                "_md_mcp_plugin_scene_adapter_r4_state", "_md_mcp_plugin_scene_adapter_r5_state"):
        previous = getattr(sys, key, None)
        if previous is not None and previous.get("blocked"):
            raise AdapterError("EARLIER_ADAPTER_REQUIRES_RECOVERY", "Earlier native input has an unresolved outcome")
    value = getattr(sys, STATE_KEY, None)
    if value is None:
        value = {"configuration": None, "library": None, "call": None, "blocked": False}
        setattr(sys, STATE_KEY, value)
    return value


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _params(value, required=(), optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise AdapterError("INVALID_PARAMETERS", "Unexpected/missing fixed adapter parameter")


def _hex(value, label):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AdapterError("INVALID_PARAMETERS", label + " must be a SHA256 token")
    return value


def _budget(value):
    if type(value) is not int or not 50 <= value <= 1500:
        raise AdapterError("INVALID_PARAMETERS", "budget_ms must be an integer in [50,1500]")
    return value


def _xy(value):
    if type(value) is not list or len(value) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 10000000 for x in value):
        raise AdapterError("INVALID_PARAMETERS", "Finite nonnegative logical XY pairs required")
    return [float(x) for x in value]


def install_native_bridge(path, sha256, *, artifact_root=None):
    """Internal bootstrap only; never an operation/tool and never loads a DLL."""
    _hex(sha256, "sha256")
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.name != "md_qt_scene_adapter_v6_bridge.dll" or candidate.is_symlink() or not candidate.is_file() or _hash(candidate) != sha256:
        raise AdapterError("INVALID_BRIDGE_CONFIGURATION", "One existing pinned scene-adapter DLL required")
    root = None
    if artifact_root is not None:
        root = Path(artifact_root)
        if not root.is_absolute() or root.is_symlink():
            raise AdapterError("INVALID_ARTIFACT_ROOT", "Core must supply one absolute owned artifact directory")
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise AdapterError("INVALID_ARTIFACT_ROOT", "Core artifact directory required")
    configuration = {"path": str(candidate.resolve()), "sha256": sha256, "artifact_root": str(root) if root else None}
    state = _state()
    if state["blocked"] or state["library"] is not None and state["configuration"] != configuration:
        raise AdapterError("BRIDGE_ALREADY_LOADED", "No hot swap or clearing uncertain execution")
    state["configuration"] = configuration
    return {"configured": True, "loaded": state["library"] is not None, "capture_root_configured": root is not None,
            "sha256": sha256, "live_validation": "pending"}


def _environment():
    return _base._environment()


def _scene():
    return _base._scene()


def _load(environment):
    state = _state()
    config = state["configuration"]
    if config is None or _hash(config["path"]) != config["sha256"]:
        raise AdapterError("BRIDGE_NOT_CONFIGURED_OR_CHANGED", "Configure the reviewed scene-adapter DLL first")
    if state["library"] is None:
        library = ctypes.CDLL(config["path"], winmode=0x900)
        version = library.mdqt_scene_adapter_abi_version
        version.argtypes, version.restype = [], ctypes.c_uint32
        if version() != 6:
            raise AdapterError("ABI_MISMATCH", "Scene adapter Fit All revision ABI6 required")
        call = library.mdqt_scene_adapter_json
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.c_uint32,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, loaded_process=dict(environment["process"]))
    if state["loaded_process"] != environment["process"]:
        raise AdapterError("PROCESS_IDENTITY_MISMATCH", "Adapter belongs to another MD process lifetime")
    return state["call"]


def _failure(code, message, *, unknown=False):
    return {"status": "UNKNOWN" if unknown else "REJECTED", "result": {"verified": False, "semantic_geometry_verified": False},
            "effects_started": unknown, "requires_recovery": unknown, "error": {"code": code, "message": message}}


def _validate_report(raw, code, request):
    if type(raw) is not dict or raw.get("schema") != SCHEMA or type(raw.get("return_code")) is not int or raw["return_code"] != code:
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Schema/typed return code mismatch")
    if raw.get("status") not in ("SUCCEEDED", "REJECTED", "PARTIAL", "UNKNOWN") or any(type(raw.get(k)) is not bool for k in ("verified", "effects_started", "requires_recovery")):
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Typed native status required")
    calls, triggers = raw.get("event_calls"), raw.get("QAction_trigger_calls")
    operation = request["operation"]
    max_triggers = 4 if operation in ("bezier_drag_unsnapped", "bezier_drag_precise_unsnapped") else 1
    max_events = 5 if operation == "bezier_drag_precise_unsnapped" else 4
    if type(calls) is not int or not 0 <= calls <= max_events or type(triggers) is not int or not 0 <= triggers <= max_triggers or raw["effects_started"] != bool(calls or triggers):
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Actual input/trigger count mismatch")
    mutation = operation in ("bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped", "set_tool", "fit_selected", "fit_all", "zoom_in_at", "zoom_out_at", "cut_selected")
    if not mutation and (calls or triggers) or operation not in ("bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped", "zoom_in_at", "zoom_out_at") and calls:
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Unexpected input outside the fixed operation")
    if raw["status"] != "SUCCEEDED":
        if raw["verified"] or code == 0 or raw["effects_started"] and not raw["requires_recovery"]:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Failed receipt contradicts effects/recovery")
        return
    if code != 0 or raw["operation"] != operation or not raw["verified"] or raw["requires_recovery"] or raw.get("physical_state_unchanged") is not True:
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Success lacks complete scope/readback")
    if operation != "inspect":
        before = raw.get("before")
        if (type(before) is not dict or before.get("native_view_ref") != request["native_view_ref"]
                or before.get("view_fingerprint") != request["view_fingerprint"]):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Receipt belongs to a different current view")
    for flag in ("semantic_geometry_verified", "native_mm_mapping_verified", "native_handle_hit_verified", "native_cut_partition_verified"):
        if raw.get(flag) is not False:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Adapter cannot establish " + flag)
    if operation in ("bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped"):
        events = raw.get("events")
        precise = operation == "bezier_drag_precise_unsnapped"
        recipe = (("hover", 0, 0, 0), ("press", 1, 1, 0), ("move", 0, 1, 0x08000000), ("release", 1, 0, 0x08000000))
        positions = [request["start"], request["start"], request["end"], request["end"]]
        if precise:
            activation = _xy(raw.get("activation_point"))
            threshold = raw.get("start_drag_distance_before")
            distance = raw.get("activation_distance_logical")
            size = raw["before"].get("mapping", {}).get("viewport_size")
            if (type(threshold) is not int or not 0 <= threshold <= 65535
                    or type(distance) is not int or distance != max(12, min(32, threshold + 2))
                    or type(raw["before"].get("mapping", {}).get("start_drag_distance")) is not int
                    or raw["before"].get("mapping", {}).get("start_drag_distance") != threshold
                    or raw.get("activation_rule") != "max12_min32_startDragDistance_plus2_along_start_to_end"
                    or raw.get("activation_point_rule_verified") is not True or raw.get("pointer_drag_activation_verified") is not False
                    or type(size) is not list or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
                    or any(not 0 <= activation[i] < size[i] or not 0 <= request["end"][i] < size[i] for i in (0, 1))):
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Bounded internal activation point/rule required")
            dx, dy = (request["end"][i] - request["start"][i] for i in (0, 1))
            ax, ay = (activation[i] - request["start"][i] for i in (0, 1))
            if (abs(math.hypot(ax, ay) - distance) > 1e-6 or ax*dx + ay*dy <= 0
                    or abs(ax*dy - ay*dx) > 1e-6 * math.hypot(dx, dy)):
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Activation must lie along requested start鈫抏nd direction")
            recipe = (("hover", 0, 0, 0), ("press", 1, 1, 0), ("activation_move", 0, 1, 0x08000000),
                      ("final_move", 0, 1, 0x08000000), ("release", 1, 0, 0x08000000))
            positions = [request["start"], request["start"], activation, request["end"], request["end"]]
        profile = PRECISE_PROFILE if precise else UNSNAPPED_PROFILE if operation == "bezier_drag_unsnapped" else PROFILE
        if calls != len(recipe) or (operation == "bezier_drag" and triggers != 0) or type(events) is not list or len(events) != len(recipe) or raw.get("profile") != profile:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Exactly the selected fixed phase profile required")
        for index, (row, (stage, button, buttons, modifiers)) in enumerate(zip(events, recipe)):
            position = positions[index]
            if (type(row) is not dict or row.get("stage") != stage or row.get("sent") is not True or row.get("returned") is not True
                    or any(type(row.get(k)) is not int or row[k] != v for k, v in (("button", button), ("buttons", buttons), ("modifiers", modifiers)))
                    or row.get("viewport_logical") != position):
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Phase coordinates/buttons/modifiers differ")
        if any(raw.get(k) is not True for k in ("press_sent", "release_sent", "release_returned", "paired_release_verified", "all_events_nonspontaneous", "dispatch_completed")) or raw.get("press_outstanding") is not False:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Closed paired release not proved")
        if precise:
            for index, row in enumerate(events):
                if (any(type(row.get(key)) is not list or len(row[key]) != 2
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in row[key])
                        for key in ("scene", "last_scene", "button_down_scene"))
                        or row.get("button_down_scene") != events[0].get("scene")
                        or row.get("last_scene") != events[max(0, index-1)].get("scene")
                        or type(row.get("scene")) is not list or len(row["scene"]) != 2):
                    raise AdapterError("INVALID_NATIVE_RECEIPT", "Five-phase last/down coordinates must remain a closed sequence")
        if operation in ("bezier_drag_unsnapped", "bezier_drag_precise_unsnapped"):
            snap = raw.get("snap_settings")
            if (type(snap) is not dict or snap.get("schema") != "md-fixed-bezier-unsnapped-actions-v1"
                    or any(snap.get(k) is not True for k in ("disabled_before_gesture", "restoration_attempted", "original_states_restored"))
                    or any(type(snap.get(k)) is not int or snap[k] != 0 for k in ("setChecked_calls", "setEnabled_calls"))
                    or type(snap.get("direct_trigger_calls")) is not int or snap["direct_trigger_calls"] != triggers
                    or type(snap.get("actions")) is not list or len(snap["actions"]) != 2):
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Fixed snap-off and original-state restoration proof required")
            total = 0
            names = (("action_ActivateSnapping", "Snap to Pattern"), ("action_Smart_Guide", "Smart Guide"))
            for row, (name, text) in zip(snap["actions"], names):
                if (type(row) is not dict or row.get("object_name") != name or row.get("text") != text
                        or type(row.get("before_checked")) is not bool or row.get("off_checked") is not False
                        or row.get("after_checked") is not row["before_checked"]
                        or type(row.get("disable_trigger_calls")) is not int or row["disable_trigger_calls"] != int(row["before_checked"])
                        or type(row.get("restore_trigger_calls")) is not int or row["restore_trigger_calls"] not in (0, 1)):
                    raise AdapterError("INVALID_NATIVE_RECEIPT", "Snap action name/state/typed trigger count differs")
                group = row.get("group")
                if group is not None and (type(group) is not dict or group.get("class_name") != "QActionGroup"
                        or type(group.get("object_name")) is not str or len(group["object_name"]) > 256
                        or group.get("exclusive") is not False or group.get("current_owner_parent") is not True):
                    raise AdapterError("INVALID_NATIVE_RECEIPT", "Snap group must be absent or current-owner nonexclusive")
                total += row["disable_trigger_calls"] + row["restore_trigger_calls"]
            if total != triggers:
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Snap trigger sum differs")
            gesture = raw.get("gesture_observation_before")
            if type(gesture) is not dict or gesture.get("native_view_ref") != request["native_view_ref"]:
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Fresh snap-off gesture observation required")
            target_names = {name for name, _ in names}
            def strip(mapping):
                if type(mapping) is not dict or type(mapping.get("checked_actions")) is not list:
                    raise AdapterError("INVALID_NATIVE_RECEIPT", "Checked-action baseline missing")
                result = dict(mapping)
                result["checked_actions"] = [a for a in result["checked_actions"] if type(a) is dict and a.get("object_name") not in target_names]
                return result
            initial_map = raw["before"].get("mapping")
            off_map = gesture.get("mapping")
            final_map = raw.get("after", {}).get("mapping")
            if strip(initial_map) != strip(off_map) or initial_map != final_map:
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Original mapping restored and snap-off observation must differ only by fixed flags")
            for row in snap["actions"]:
                name = row["object_name"]
                if (any(a.get("object_name") == name for a in off_map["checked_actions"])
                        or any(a.get("object_name") == name for a in initial_map["checked_actions"]) is not row["before_checked"]):
                    raise AdapterError("INVALID_NATIVE_RECEIPT", "Actual checked-action snapshots disagree with the snap receipt")
    elif operation in ("zoom_in_at", "zoom_out_at"):
        events = raw.get("events")
        expected_profile = ZOOM_OUT_PROFILE if operation == "zoom_out_at" else ZOOM_PROFILE
        expected_delta = [0, 120] if operation == "zoom_out_at" else [0, -120]
        if calls != 2 or triggers != 0 or type(events) is not list or len(events) != 2 or raw.get("wheel_profile") != expected_profile:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Exactly scene hover plus one fixed viewport wheel required")
        expected = (("zoom_hover", "GraphicsSceneMouseMove", "QGraphicsScene/QtView2D"),
                    ("zoom_wheel", "Wheel", "QOpenGLWidget/widget2d"))
        previous_end = -1
        for row, (stage, event_type, receiver) in zip(events, expected):
            if (type(row) is not dict or row.get("stage") != stage or row.get("event_type") != event_type
                    or row.get("receiver") != receiver or row.get("viewport_logical") != request["point"]
                    or any(row.get(k) is not True for k in ("sent", "returned", "nonspontaneous", "nonspontaneous_after"))
                    or any(type(row.get(k)) is not bool for k in ("send_return", "accepted"))
                    or any(type(row.get(k)) is not int or row[k] != v for k, v in (("buttons", 0), ("modifiers", 0), ("source", 3)))
                    or type(row.get("elapsed_before_ms")) is not int or type(row.get("elapsed_after_ms")) is not int
                    or not 0 <= row["elapsed_before_ms"] <= row["elapsed_after_ms"]
                    or previous_end > row["elapsed_before_ms"]):
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Fixed zoom row/point/buttons/source/timing mismatch")
            previous_end = row["elapsed_after_ms"]
        hover, wheel = events
        if (type(hover.get("button")) is not int or hover["button"] != 0
                or wheel.get("angle_delta") != expected_delta or type(wheel.get("angle_delta")) is not list
                or any(type(v) is not int for v in wheel["angle_delta"])
                or wheel.get("pixel_delta") != [0, 0] or type(wheel.get("pixel_delta")) is not list
                or any(type(v) is not int for v in wheel["pixel_delta"])
                or type(wheel.get("phase")) is not int or wheel["phase"] != 0 or wheel.get("inverted") is not False):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Wheel must match the operation's fixed sign/vertical/no-pixel/no-phase profile")
        counts = {"zoom_hover_sendEvent_calls": 1, "viewport_wheel_sendEvent_calls": 1,
                  "bezier_scene_event_calls": 0, "scene_wheel_sendEvent_calls": 0, "scene_ungrab_calls": 0}
        flags = ("hover_stage_scope_verified", "dispatch_completed", "wheel_scope_verified", "all_events_nonspontaneous",
                 "local_technical_cleanup_verified", "current_2d_refs_verified", "scrollbar_ref_identities_preserved",
                 "view_size_DPR_origin_mode_preserved", "intentional_view_and_scroll_value_changes_allowed")
        if (any(type(raw.get(k)) is not int or raw[k] != v for k, v in counts.items())
                or any(raw.get(k) is not True for k in flags)
                or any(raw.get(k) is not False for k in ("press_sent", "release_sent", "press_outstanding", "zoom_effect_verified",
                       "zoom_direction_verified", "zoom_magnification_verified", "zoom_pivot_verified",
                       "geometry_preserved_verified_by_bridge", "visible_view_restoration_verified", "automatic_inverse_or_retry_allowed"))):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Zoom scope/cleanup/counts or unsupported claims differ")
        for key in ("tools_before", "tools_after"):
            tool = raw.get(key)
            if type(tool) is not dict or tool.get("verified") is not True or type(tool.get("current_mode")) is not int or tool["current_mode"] != 2:
                raise AdapterError("INVALID_NATIVE_RECEIPT", "Zoom requires the same current Edit Curve Point mode")
        before_map, after_map = raw["before"].get("mapping"), raw.get("after", {}).get("mapping")
        if type(before_map) is not dict or type(after_map) is not dict:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Complete before/after mapping metadata required")
        allowed = {"view_transform", "viewport_transform", "scroll_values"}
        if ({k: v for k, v in before_map.items() if k not in allowed} != {k: v for k, v in after_map.items() if k not in allowed}
                or raw["after"].get("native_view_ref") != request["native_view_ref"]):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Wheel changed fixed current viewport scope")
    elif operation == "set_tool":
        current = raw.get("tools_after", {})
        expected = TOOLS.index(request["tool"])
        if calls or triggers not in (0, 1) or current.get("verified") is not True or type(current.get("current_mode")) is not int or current["current_mode"] != expected or raw.get("command_outcome_verified") is not True or raw.get("already_current") is not (triggers == 0):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Fixed tool state not proved")
    elif operation == "fit_selected":
        if calls or triggers != 1 or raw.get("fit_selected_action_returned") is not True or raw.get("fit_action_name") != "actionShow_Selected_Patterns_in_Window":
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Exactly one fixed 2D Fit Selected action required")
    elif operation == "fit_all":
        if (calls or triggers != 1 or raw.get("events") != [] or raw.get("fit_action_name") != FIT_ALL_ACTION
                or any(raw.get(key) is not True for key in ("fit_all_action_returned", "fit_all_scope_verified"))
                or any(raw.get(key) is not False for key in ("press_sent", "release_sent", "press_outstanding", "fit_all_extents_verified"))):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Exactly one closed 2D Fit All action required")
        after = raw.get("after")
        if (type(after) is not dict or after.get("native_view_ref") != request["native_view_ref"]
                or before.get("scene_item_grabber_absent") is not True or after.get("scene_item_grabber_absent") is not True):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Fit All requires the same ungrabbed 2D view")
        before_map, after_map = before.get("mapping"), after.get("mapping")
        allowed = {"view_transform", "viewport_transform", "scroll_values"}
        if (type(before_map) is not dict or type(after_map) is not dict
                or {k: v for k, v in before_map.items() if k not in allowed} != {k: v for k, v in after_map.items() if k not in allowed}):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Fit All changed fixed viewport scope")
        before_selection = raw.get("selection_before")
        if type(before_selection) is not dict or raw.get("selection_after") != before_selection:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Fit All changed the public 2D selection")
    elif operation == "cut_selected":
        if calls or triggers != 1 or raw.get("selected_cut_action_returned") is not True:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Exactly one selected Cut return required")
    elif operation == "capture":
        proof_flags = ("paint_event_sent", "paint_event_returned", "paint_nonspontaneous", "capture_recovery_verified",
                       "original_Qt_context_surface_restored", "original_WGL_context_dc_restored",
                       "original_framebuffer_bindings_restored", "source_metadata_before_after_verified",
                       "app_GL_observed_state_unchanged", "owned_resources_cleanup_verified",
                       "owned_name_invalidation_verified", "dual_copies_bit_exact_equal_every_pixel",
                       "dual_baseline_owned_coverage_verified")
        counts = {"viewport_makeCurrent_calls": 1, "direct_QPaintEvent_send_calls": 1,
                  "owned_framebuffer_objects_created": 2, "owned_framebuffer_objects_deleted": 2,
                  "owned_texture_objects_created": 1, "owned_texture_objects_deleted": 1,
                  "owned_live_object_count": 0, "DSA_read_calls": 4,
                  "DSA_baseline_read_calls": 2, "DSA_copy_read_calls": 2, "GL_owned_color_blit_calls": 2}
        if (raw.get("capture_method") != "one_viewport_Paint_then_owned_DSA_renderbuffer_resolve_v2"
                or any(raw.get(k) is not True for k in proof_flags)
                or raw.get("owned_resource_cleanup_failed") is not False
                or any(type(raw.get(k)) is not int or raw[k] != v for k, v in counts.items())
                or raw.get("pixel_origin") != "top_left_after_CPU_vertical_flip"):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Paint/owned resolve, dual bytes and GL restoration are required")
        if (any(raw.get(k) is not False for k in ("GL_state_entirely_unchanged_certified",
                    "source_object_write_freedom_certified", "pixels_freshness_verified", "pixels_scene_content_verified"))
                or raw.get("normal_Qt_MD_draw_cache_effects_allowed") is not True
                or any(type(raw.get(k)) is not int or raw[k] != 0 for k in
                       ("direct_resolve_source_object_writer_calls", "new_context_calls", "new_surface_calls"))):
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Capture cannot upgrade its GL/content scope")
    elif operation == "inspect" and raw.get("metadata_catalog_complete") is not True:
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Complete bounded metadata required")


def _invoke(request):
    operation = request["operation"]
    mutation = operation in ("bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped", "set_tool", "fit_selected", "fit_all", "zoom_in_at", "zoom_out_at", "cut_selected")
    state, invoked = _state(), False
    if state["configuration"] is None:
        result = _failure("NATIVE_BRIDGE_NOT_CONFIGURED", "Root must configure the packaged, pinned scene adapter")
        result["status"] = "UNSUPPORTED"
        return result
    if mutation and state["blocked"]:
        return _failure("BRIDGE_RECOVERY_REQUIRED", "Previous uncertain dispatch requires root recovery")
    try:
        environment = _environment()
        call = _load(environment)
        data = _base._canonical(request).encode("utf-8")
        if len(data) > 16384:
            raise AdapterError("INVALID_PARAMETERS", "Native request exceeds 16KiB")
        output = ctypes.create_string_buffer(OUTPUT_CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        invoked = True
        code = call(environment["process"]["pid"], environment["process"]["creation_filetime"], data, len(data), output, len(output), ctypes.byref(written), ctypes.byref(needed))
        if not written.value and code in (-1, -2, -3, -4):
            return _failure("NATIVE_PREFLIGHT_REJECTED", "ABI/process/thread/busy preflight rejected before any dispatch")
        if not 0 < written.value < len(output) or needed.value != written.value + 1:
            raise AdapterError("INVALID_NATIVE_RECEIPT", "Missing/oversized receipt; never replay to resize")
        raw = _base._json(output.raw[:written.value].decode("utf-8"))
        _validate_report(raw, code, request)
        if raw["requires_recovery"]:
            state["blocked"] = True
        return {"status": raw["status"], "result": raw, "error": raw.get("error"),
                "effects_started": raw["effects_started"], "requires_recovery": raw["requires_recovery"]}
    except Exception as exc:
        unknown = (mutation or operation == "capture") and invoked
        if unknown:
            state["blocked"] = True
        return _failure(getattr(exc, "code", "SCENE_ADAPTER_ERROR"), str(exc), unknown=unknown)


def _encode_ref(native, token):
    value = {"version": 1, "view": "2d", "native_view_ref": native, "scene_token": token}
    return PREFIX + base64.urlsafe_b64encode(_base._canonical(value).encode()).decode().rstrip("=")


def _decode_ref(text, token):
    try:
        if type(text) is not str or not text.startswith(PREFIX) or len(text) > 2048:
            raise ValueError("Invalid scene reference")
        value = text[len(PREFIX):]
        obj = _base._json(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True).decode())
        if (type(obj) is not dict or set(obj) != {"version", "view", "native_view_ref", "scene_token"}
                or type(obj["version"]) is not int or obj["version"] != 1 or obj["view"] != "2d" or obj["scene_token"] != token
                or type(obj["native_view_ref"]) is not str or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", obj["native_view_ref"]) is None):
            raise ValueError("Stale or malformed 2D scene reference")
        return obj["native_view_ref"]
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise AdapterError("STALE_VIEW_REFERENCE", "Use a fresh scene_adapter_inspect_2d reference") from exc


def _publish(value, token):
    if type(value) is not dict or value.get("view") != "2d" or type(value.get("native_view_ref")) is not str:
        raise AdapterError("INVALID_NATIVE_RECEIPT", "Current 2D view descriptor required")
    _hex(value.get("view_fingerprint"), "view_fingerprint")
    result = dict(value)
    result["view_ref"] = _encode_ref(result.pop("native_view_ref"), token)
    _decode_ref(result["view_ref"], token)
    return result


def runtime_capabilities(params):
    try:
        _params(params)
        state = _state()
        return {"status": "SUCCEEDED", "result": {"configured": state["configuration"] is not None,
                "loaded": state["library"] is not None, "blocked_until_recovery": state["blocked"],
                "capture_root_configured": bool(state["configuration"] and state["configuration"]["artifact_root"]),
                "qt_required": "6.10.3 release Windows x64", "live_validation": "pending",
                "fixed_profile": PROFILE, "unsnapped_profile": UNSNAPPED_PROFILE, "precise_profile": PRECISE_PROFILE,
                "zoom_profile": ZOOM_PROFILE, "zoom_out_profile": ZOOM_OUT_PROFILE,
                "fit_all_action": FIT_ALL_ACTION,
                "tools": list(TOOLS), "native_mm_mapping_verified": False,
                "native_handle_hit_verified": False, "native_cut_partition_verified": False,
                "DLL_loaded_by_this_probe": False}, "error": None, "effects_started": False, "requires_recovery": False}
    except Exception as exc:
        return _failure(getattr(exc, "code", "INVALID_PARAMETERS"), str(exc))


def _request(operation, params):
    extra = {"bezier_drag": {"start", "end"}, "bezier_drag_unsnapped": {"start", "end"}, "bezier_drag_precise_unsnapped": {"start", "end"}, "zoom_in_at": {"point"}, "zoom_out_at": {"point"}, "set_tool": {"tool"}, "fit_selected": set(), "fit_all": set(), "cut_selected": {"selection_fingerprint"}, "capture": set()}[operation]
    _params(params, {"view_ref", "view_fingerprint", "scene_token"} | extra, {"budget_ms"})
    token = _hex(params["scene_token"], "scene_token")
    request = {"operation": operation, "budget_ms": _budget(params.get("budget_ms", 750)),
               "native_view_ref": _decode_ref(params["view_ref"], token),
               "view_fingerprint": _hex(params["view_fingerprint"], "view_fingerprint")}
    if operation in ("bezier_drag", "bezier_drag_unsnapped", "bezier_drag_precise_unsnapped"):
        start, end = _xy(params["start"]), _xy(params["end"])
        if start == end:
            raise AdapterError("INVALID_PARAMETERS", "Nonzero logical drag required")
        request.update(start=start, end=end, profile=PRECISE_PROFILE if operation == "bezier_drag_precise_unsnapped" else UNSNAPPED_PROFILE if operation == "bezier_drag_unsnapped" else PROFILE)
    elif operation in ("zoom_in_at", "zoom_out_at"):
        request["point"] = _xy(params["point"])
    elif operation == "set_tool":
        if type(params["tool"]) is not str or params["tool"] not in TOOLS:
            raise AdapterError("INVALID_PARAMETERS", "Only the three fixed 2D tools exist")
        request["tool"] = params["tool"]
    elif operation == "cut_selected":
        request["selection_fingerprint"] = _hex(params["selection_fingerprint"], "selection_fingerprint")
    return request, token


def inspect_2d(params):
    try:
        _params(params, optional={"budget_ms"})
        before = _scene()
        response = _invoke({"operation": "inspect", "budget_ms": _budget(params.get("budget_ms", 750))})
        if response["status"] == "SUCCEEDED":
            after = _scene()
            if before["scene_token"] != after["scene_token"]:
                raise AdapterError("SCENE_CHANGED_DURING_READ", "Read a stable current scene again")
            for key in ("before", "after"):
                response["result"][key] = _publish(response["result"][key], after["scene_token"])
            response["result"]["scene_token"] = after["scene_token"]
        return response
    except Exception as exc:
        return _failure(getattr(exc, "code", "ADAPTER_READ_FAILED"), str(exc))


def _execute(operation, params):
    effects = False
    try:
        request, token = _request(operation, params)
        if _scene()["scene_token"] != token:
            raise AdapterError("STALE_SCENE", "Read current scene and 2D reference again")
        path = None
        if operation == "capture":
            config = _state()["configuration"]
            if config is None or not config["artifact_root"]:
                raise AdapterError("CORE_ARTIFACT_ROOT_REQUIRED", "Core must configure its internal capture directory")
            configured_root = Path(config["artifact_root"])
            if configured_root.is_symlink():
                raise AdapterError("INVALID_ARTIFACT_ROOT", "Configured artifact root became a link")
            root = configured_root.resolve(strict=True)
            if root != configured_root or not root.is_dir():
                raise AdapterError("INVALID_ARTIFACT_ROOT", "Configured artifact root changed")
            folder = root / uuid.uuid4().hex
            folder.mkdir(exist_ok=False)
            path = folder / "viewport2d.png"
            request["png_path"] = str(path)
        response = _invoke(request)
        effects = response["effects_started"]
        if response["status"] != "SUCCEEDED":
            return response
        result = response["result"]
        if operation == "capture":
            image = result.get("image")
            if (type(image) is not dict or Path(image.get("path", "")).resolve(strict=True) != path or path.is_symlink()
                    or not 24 <= path.stat().st_size <= 67108864 or _hash(path) != image.get("sha256")):
                raise AdapterError("INVALID_CAPTURE_ASSET", "Capture must bind exactly this internally allocated PNG")
            data = path.read_bytes()
            if (data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                    or struct.unpack(">II", data[16:24]) != (image.get("width"), image.get("height"))
                    or type(image.get("byte_count")) is not int or image["byte_count"] != len(data)
                    or image.get("view_fingerprint") != request["view_fingerprint"]
                    or image.get("native_view_ref") != request["native_view_ref"]):
                raise AdapterError("INVALID_CAPTURE_ASSET", "PNG header/dimensions/view/SHA do not agree")
            image.pop("native_view_ref", None)
            image["view_ref"] = params["view_ref"]
        after = _scene()
        if operation in ("capture", "zoom_in_at", "zoom_out_at") and after["scene_token"] != token:
            raise AdapterError("SCENE_CHANGED_DURING_READ", "View observation/zoom did not preserve the current native scene token")
        if operation == "fit_all" and after["scene_token"] != token:
            raise AdapterError("SCENE_CHANGED_DURING_FIT_ALL", "Current native scene token changed during Fit All")
        result["before"] = _publish(result["before"], token)
        result["after"] = _publish(result["after"], after["scene_token"])
        if operation in ("bezier_drag_unsnapped", "bezier_drag_precise_unsnapped"):
            result["gesture_observation_before"] = _publish(result["gesture_observation_before"], token)
        result["scene_token_after"] = after["scene_token"]
        result["verification_scope"] = "Current public scene metadata/image or fixed dispatch/cleanup only; semantic hit, mm mapping and geometry require independent native readback"
        result["fulfilled"] = False
        return response
    except Exception as exc:
        if effects:
            _state()["blocked"] = True
        return _failure(getattr(exc, "code", "ADAPTER_READBACK_FAILED"), str(exc), unknown=effects)


def capture_2d(params):
    return _execute("capture", params)


def bezier_drag(params):
    return _execute("bezier_drag", params)


def bezier_drag_unsnapped(params):
    return _execute("bezier_drag_unsnapped", params)


def bezier_drag_precise_unsnapped(params):
    return _execute("bezier_drag_precise_unsnapped", params)


def set_tool(params):
    return _execute("set_tool", params)


def fit_selected(params):
    from .selection import inspect_selection
    selected = inspect_selection({})
    if len(selected['selected']) != 1 or selected['scene_token'] != params.get('scene_token'):
        return _failure('SINGLE_PATTERN_SELECTION_REQUIRED', 'Select one current pattern UUID before fitting the 2D view')
    return _execute('fit_selected', params)


def fit_all(params):
    return _execute("fit_all", params)


def cut_selected(params):
    return _execute("cut_selected", params)


def zoom_in_at(params):
    return _execute("zoom_in_at", params)


def zoom_out_at(params):
    return _execute("zoom_out_at", params)


OPERATIONS = {"scene_adapter_v6.runtime_capabilities": runtime_capabilities, "scene_adapter_v6.inspect": inspect_2d,
              "scene_adapter_v6.capture": capture_2d, "scene_adapter_v6.bezier_drag": bezier_drag,
              "scene_adapter_v6.bezier_drag_unsnapped": bezier_drag_unsnapped,
              "scene_adapter_v6.bezier_drag_precise_unsnapped": bezier_drag_precise_unsnapped,
              "scene_adapter_v6.set_tool": set_tool, "scene_adapter_v6.fit_selected": fit_selected,
              "scene_adapter_v6.fit_all": fit_all,
              "scene_adapter_v6.zoom_in_at": zoom_in_at, "scene_adapter_v6.zoom_out_at": zoom_out_at,
              "scene_adapter_v6.cut_selected": cut_selected}
