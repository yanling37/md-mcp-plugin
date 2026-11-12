"""The reviewed MeshBox mode CABI receipt contract, shared by current-project operations."""
import ctypes
import json

SCHEMA = "md-qt-cloth-meshbox-entry/1"
CAPACITY = 1024 * 1024
ZERO_FIELDS = ("global_input_calls", "event_dispatch_calls", "point_selection_calls", "geometry_setter_calls", "focus_change_calls")


def _scope_mode(scope, expected):
    box = expected == "MeshBox"
    checked = "actionBox_Selecting_Tool" if box else "actionMove_Pattern"
    return (isinstance(scope, dict) and scope.get("mode") == expected and scope.get("mode_group_checked_action") == checked
            and scope.get("mode_group_exclusive") is True and scope.get("world_coordinate_checked") is True
            and scope.get("box_action", {}).get("object_name") == "actionBox_Selecting_Tool"
            and scope.get("box_action", {}).get("checked") is box
            and scope.get("move_action", {}).get("object_name") == "actionMove_Pattern"
            and scope.get("move_action", {}).get("checked") is (not box))


def _validate_native(raw, code, command, pid, creation, window, ticket):
    if (type(raw) is not dict or raw.get("schema") != SCHEMA or raw.get("command") != command or raw.get("qt_version") != "6.10.3"
            or type(raw.get("return_code")) is not int or raw["return_code"] != code or code not in (0, 1, 2)
            or raw.get("process_id") != pid or raw.get("creation_filetime") != str(creation)
            or raw.get("root_window") != str(window) or raw.get("ticket") != str(ticket)
            or any(type(raw.get(key)) is not int or raw[key] != 0 for key in ZERO_FIELDS)
            or raw.get("geometry_preservation_verified") is not False):
        raise ValueError("Native ABI, lifetime or fixed action scope differs")
    calls = raw.get("QAction_trigger_calls")
    if (type(calls) is not int or calls not in (0, 1) or type(raw.get("trigger_returned")) is not bool
            or type(raw.get("transaction_held")) is not bool or raw.get("restore_required") is not raw["transaction_held"]
            or type(raw.get("requires_recovery")) is not bool or raw.get("mode_readback_verified") is not (code == 0)):
        raise ValueError("Native dispatch count or readback state differs")
    if code == 1:
        if raw.get("status") != "REJECTED" or calls or raw["trigger_returned"]:
            raise ValueError("Rejected operation must have zero direct triggers")
        return
    if code == 2:
        if raw.get("status") != "UNKNOWN" or calls != 1 or not raw["transaction_held"] or not raw["requires_recovery"]:
            raise ValueError("Uncertain action must retain its transaction")
        return

    entering = command == "enter"
    expected_status = "MESHBOX_VERIFIED" if entering else "MOVE_RESTORED" if calls else "MOVE_ALREADY_OBSERVED"
    before_mode = "MeshBox" if not entering and calls else "MovePattern"
    after_mode = "MeshBox" if entering else "MovePattern"
    if (raw.get("status") != expected_status or entering and calls != 1
            or raw["trigger_returned"] is not bool(calls) or raw["transaction_held"] is not entering or raw["requires_recovery"]
            or raw.get("original_scope_preserved") is not True or raw.get("physical_state_unchanged") is not True
            or not _scope_mode(raw.get("scope_before"), before_mode) or not _scope_mode(raw.get("scope_after"), after_mode)):
        raise ValueError("Claimed transition must match the original group and actual checked mode")


def invoke(function, command, pid, creation, window, ticket, budget_ms):
    output = ctypes.create_string_buffer(CAPACITY)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    code = function(pid, creation, window, ticket, budget_ms, output, len(output), ctypes.byref(written), ctypes.byref(needed))
    if code in (-1, -2, -3, -4) and written.value == 0 and needed.value == 0:
        return {"status": "REJECTED", "return_code": code, "reason": "NATIVE_PREFLIGHT", "QAction_trigger_calls": 0}
    if not 0 < written.value < CAPACITY or needed.value != written.value + 1 or output[written.value] != b'\x00':
        raise ValueError("No bounded native receipt. Inspect current mode before any follow-up")
    raw = json.loads(output.raw[:written.value].decode("utf-8"))
    _validate_native(raw, code, command, pid, creation, window, ticket)
    return raw

