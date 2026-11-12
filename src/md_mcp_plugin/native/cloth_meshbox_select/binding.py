"""The reviewed MeshBox selector mapping and CABI receipt contract."""
import ctypes
import json
import math


SCHEMA = "md-qt-cloth-meshbox-select/1"
CAPACITY = 1024 * 1024
PROFILE = "widget2d_shift_control_hover_press_move_release"
KINDS = ("hover", "press", "move", "release")
FIELDS = {
    "viewport_size", "device_pixel_ratio", "view_geometry", "viewport_geometry", "view_transform",
    "viewport_transform", "scroll_values", "global_origin", "relative_visible", "enabled",
    "drag_mode", "start_drag_distance",
}
ZERO_FIELDS = (
    "scene_sendEvent_calls", "key_sendEvent_calls", "physical_OS_input_calls", "focus_calls",
    "activation_calls", "event_pump_calls", "QAction_trigger_calls", "model_setter_calls",
    "global_input_cache_setter_calls", "OS_cursor_setter_calls",
)
UNVERIFIED_FIELDS = ("selection_hit_verified", "single_vertex_selection_verified", "geometry_preservation_verified")


def _integers(value, size):
    return type(value) in (list, tuple) and len(value) == size and all(type(x) is int for x in value)


def box_mapping(view_mapping, start, end):
    """Use the mapping of the actual same-mode v5 frame and one planned box."""
    if type(view_mapping) is not dict or set(view_mapping) not in (FIELDS, FIELDS | {"checked_actions"}):
        raise ValueError("Use the exact v5 frame mapping")
    mapping = {key: view_mapping[key] for key in FIELDS}
    for key, count in (("viewport_size", 2), ("view_geometry", 4), ("viewport_geometry", 4),
                       ("scroll_values", 2), ("global_origin", 2)):
        if not _integers(mapping[key], count) or any(not -(2**31) <= x < 2**31 for x in mapping[key]):
            raise ValueError(f"Expected integral current {key}")
        mapping[key] = list(mapping[key])
    for key in ("view_transform", "viewport_transform"):
        values = mapping[key]
        if type(values) not in (list, tuple) or len(values) != 9 or not all(
                type(x) in (int, float) and math.isfinite(x) for x in values):
            raise ValueError(f"Expected finite current {key}")
        mapping[key] = list(values)
    width, height = mapping["viewport_size"]
    threshold, dpr = mapping["start_drag_distance"], mapping["device_pixel_ratio"]
    if (not all(64 <= size <= 16384 for size in (width, height))
            or type(dpr) not in (int, float) or not .5 <= dpr <= 8
            or type(threshold) is not int or not 1 <= threshold < 32
            or type(mapping["drag_mode"]) is not int or mapping["drag_mode"] != 0
            or mapping["relative_visible"] is not True or mapping["enabled"] is not True):
        raise ValueError("Expected a current enabled and visible NoDrag viewport with a bounded threshold")
    if not _integers(start, 2) or not _integers(end, 2):
        raise ValueError("Expected integral start and end coordinates")
    start, end = list(start), list(end)
    if (any(not 2 <= a < b <= limit - 3 for a, b, limit in zip(start, end, (width, height), strict=True))
            or any(not threshold < b - a <= 32 for a, b in zip(start, end, strict=True))):
        raise ValueError("Expected both box sides strictly above startDragDistance and at most 32 logical pixels")
    return mapping, start, end


def _validate(report, code, binding):
    mapping, start, end, pid, creation, hwnd = binding
    if (type(report) is not dict or report.get("schema") != SCHEMA or type(report.get("return_code")) is not int
            or report["return_code"] != code or code not in (0, 1, 2)
            or report.get("status") != {0: "EVENTS_DISPATCHED", 1: "REJECTED", 2: "UNKNOWN"}[code]
            or type(report.get("reason")) is not str or not report["reason"]
            or type(report.get("process_id")) is not int or report["process_id"] != pid
            or report.get("creation_filetime") != str(creation) or report.get("root_window") != str(hwnd)
            or report.get("qt_version") != "6.10.3" or report.get("receiver") != "QOpenGLWidget/widget2d"
            or report.get("profile") != PROFILE or report.get("box_start") != start or report.get("box_end") != end
            or any(type(report.get(key)) is not int or report[key] != 0 for key in ZERO_FIELDS)
            or any(report.get(key) is not False for key in UNVERIFIED_FIELDS)):
        raise ValueError("Native ABI, window binding, event scope or box differs")
    events = report.get("events")
    if type(events) is not list or len(events) != len(KINDS):
        raise ValueError("Missing fixed viewport event observations")
    rows = {}
    for event, kind in zip(events, KINDS, strict=True):
        expected_position = start if kind in ("hover", "press") or kind == "release" and not rows["move"]["sent"] else end
        if (type(event) is not dict or event.get("kind") != kind or event.get("position") != expected_position
                or any(type(event.get(key)) is not bool for key in ("sent", "returned", "accepted", "send_return", "nonspontaneous"))
                or event["returned"] and not event["sent"]
                or (event["accepted"] or event["send_return"]) and not event["returned"]):
            raise ValueError("Native event record differs from the fixed box profile")
        rows[kind] = event
    sent = sum(row["sent"] for row in rows.values())
    if ((rows["press"]["sent"] and not rows["hover"]["returned"])
            or (rows["move"]["sent"] and not rows["press"]["returned"])
            or (rows["release"]["sent"] and not rows["press"]["sent"])
            or any(type(report.get(key)) is not int or report[key] != sent for key in (
                "direct_synthetic_event_calls", "viewport_mouse_sendEvent_calls"))
            or report.get("effects_started") is not bool(sent)
            or report.get("dispatch_sequence_completed") is not (code == 0)
            or type(report.get("requires_recovery")) is not bool
            or type(report.get("release_receiver_scope_verified")) is not bool
            or report.get("paired_release_verified") is not (
                rows["press"]["returned"] and rows["release"]["returned"] and report["release_receiver_scope_verified"])):
        raise ValueError("The dispatch count and closure do not agree")
    if code == 1:
        if sent or report["requires_recovery"] is not (report["reason"] == "PREVIOUS_BATCH_UNRESOLVED"):
            raise ValueError("Rejection must preserve any previous recovery hold and send zero events")
        return report
    if code == 2:
        if not sent or not report["requires_recovery"]:
            raise ValueError("Unknown outcome after dispatch must hold recovery")
        return report

    if (report["requires_recovery"] or sent != 4 or report.get("cpp_exception") is not False
            or report["paired_release_verified"] is not True
            or any(not all(row[key] for key in ("sent", "returned", "accepted", "send_return", "nonspontaneous")) for row in rows.values())
            or report.get("mapping_before") != mapping or report.get("mapping_after") != mapping):
        raise ValueError("Successful dispatch requires four returned events and exact current mapping")
    for phase in ("before", "after"):
        scope, physical = report.get(f"scope_{phase}", {}), report.get(f"physical_{phase}", {})
        if (any(scope.get(key) is not True for key in (
                "verified", "MeshBox_checked", "World_checked", "mesh_group_complete", "world_group_complete"))
                or scope.get("MovePattern_checked") is not False
                or any(physical.get(key) is not True for key in (
                    "verified", "background_inactive_minimized", "foreground_owner_known", "foreground_is_not_MD",
                    "OS_thread_focus_absent", "OS_capture_absent", "Qt_mouse_grabber_absent", "Qt_keyboard_grabber_absent",
                    "no_modal_or_popup"))):
            raise ValueError("MeshBox/World and background physical guards must remain verified")
    stages = report.get("stages", {})
    for stage in ("before", "after_hover", "after_press", "after_move", "after_release"):
        if any(stages.get(stage, {}).get(key) is not True for key in (
                "verified", "same_scope", "same_receiver", "same_mapping", "same_physical", "within_budget")):
            raise ValueError(f"Missing stable verified {stage} observation")
    if any(stages[stage].get("scene_item_grabber_absent") is not True for stage in ("before", "after_hover", "after_release")):
        raise ValueError("Scene input must be closed before and after the gesture")
    return report


def invoke(function, pid, creation_filetime, titleframe_hwnd, mapping, start, end, budget_ms):
    encoded = json.dumps(mapping, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > 8192:
        raise ValueError("Current view mapping exceeds the native input bound")
    output = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    try:
        code = function(pid, creation_filetime, titleframe_hwnd, budget_ms, *start, *end, encoded, len(encoded),
                        output, len(output), ctypes.byref(written), ctypes.byref(needed))
        if code in (-1, -2, -3, -4) and not written.value and not needed.value:
            return {"schema": SCHEMA, "status": "REJECTED", "reason": "NATIVE_PREFLIGHT",
                    "return_code": code, "effects_started": False, "requires_recovery": False,
                    "direct_synthetic_event_calls": 0, "viewport_mouse_sendEvent_calls": 0}
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1 or output[written.value] != b"\x00":
            raise ValueError("No complete bounded native receipt")
        raw = json.loads(output.raw[:written.value].decode("utf-8"))
        return _validate(raw, code, (mapping, start, end, pid, creation_filetime, titleframe_hwnd))
    except Exception as exc:
        return {"schema": SCHEMA, "status": "UNKNOWN", "reason": "NATIVE_RECEIPT_UNVERIFIED", "message": str(exc),
                "effects_started": True, "effects_may_have_started": True, "requires_recovery": True}

