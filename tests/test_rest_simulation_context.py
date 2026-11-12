"""Offline simulation-context read contract; no MD or DLL loading."""
import copy
import ctypes
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from md_mcp_plugin.embedded import rest_simulation_context as e

PROCESS = {"pid": 123, "creation_filetime": 456}; TOKEN = "a" * 64


def raw_fixture():
    raw = {"schema": e.SCHEMA, "return_code": 0, "verified": True, "catalog_complete": True, "status": "READ_COMPLETE",
           "process_id": 123, "process_creation_filetime": "456", "effects_started": False, "requires_recovery": False,
           "simulation_running_state_verified": False, "start_stop_dispatch_authorized": False, "RestShape_write_contract_verified": False,
           "main_window_minimized_before": True, "main_window_minimized_after": True, "physical_state_unchanged": True,
           "cursor_position_changed_during_read": False,
           "physical_before": {"cursor": [3, 7], "cursor_valid": True, "foreground": "12", "capture": "0"},
           "physical_after": {"cursor": [3, 7], "cursor_valid": True, "foreground": "12", "capture": "0"},
           "override_instances": [], "override_instance_count": 0,
           "main_window_simulation_declarations": [],
           "actions": [{"object_name": name, "match_count": 1, "reference_unique": True, "triggered": False,
                        "text": "Simulate" if name == "action_SimStop" else name, "enabled": True, "checkable": True,
                        "checked": name == "actionMove_Pattern", "associated_objects": []} for name in e.ACTION_NAMES]}
    raw.update({key: 0 for key in e.ZERO_COUNTS}); return raw


class SimulationContextTests(unittest.TestCase):
    def test_actual_lowlevel_read_boundary_and_no_running_claim(self):
        raw = raw_fixture(); calls = []
        def callback(pid, creation, budget, output, size, written, needed):
            calls.append((pid, creation, budget)); payload = json.dumps(raw).encode()
            ctypes.memmove(output, payload, len(payload))
            ctypes.cast(written, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload)
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload) + 1
            return 0
        def forbidden(*args): raise AssertionError("No runtime API function invocation allowed")
        api = types.ModuleType("utility_api"); api.Simulate = forbidden
        snapshot = {"fingerprints": {"owner": "stable"}, "scene_fingerprint": "b" * 64, "rows": [{"owner": "stable"}], "native": {"PatternList": []}}
        with patch.object(e._base, "_scene", return_value={"scene_token": TOKEN}), \
             patch.object(e._base, "_environment", return_value={"process": PROCESS}), patch.object(e, "_load", return_value=callback), \
             patch.object(e._snapshots, "_native_snapshot", return_value=snapshot), patch.dict(sys.modules, {"utility_api": api}):
            result = e.inspect({"scene_token": TOKEN})
        self.assertEqual(result["status"], "SUCCEEDED", result)
        self.assertEqual(calls, [(123, 456, 750)])
        self.assertFalse(result["result"]["simulation_running_state_verified"])
        self.assertFalse(result["result"]["start_stop_dispatch_authorized"])
        self.assertEqual(result["result"]["runtime_api_metadata"]["function_calls"], 0)

    def test_typed_readonly_and_fixed_actions_rejections(self):
        changes = (("simulation_calls", 1), ("QAction_trigger_calls", True), ("simulation_running_state_verified", True),
                   ("main_window_minimized_after", False), ("override_instance_count", True))
        for key, value in changes:
            raw = raw_fixture(); raw[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): e._validate(raw, 0, PROCESS)
        raw = raw_fixture(); raw["actions"][0]["object_name"] = "caller-selected-action"
        with self.assertRaises(ValueError): e._validate(raw, 0, PROCESS)
        raw = raw_fixture(); raw.update(return_code=1, verified=False, catalog_complete=False, status="READ_NOT_VERIFIED")
        e._validate(raw, 1, PROCESS)
        raw["verified"] = True
        with self.assertRaises(ValueError): e._validate(raw, 1, PROCESS)

    def test_cursor_motion_only_preserves_read_while_other_physical_changes_refuse(self):
        raw = raw_fixture()
        raw['physical_after']['cursor'] = [4, 9]
        raw.update(physical_state_unchanged=False, cursor_position_changed_during_read=True)
        e._validate(raw, 0, PROCESS)
        for key, value in (("foreground", "13"), ("capture", "14"), ("cursor_valid", False)):
            changed = copy.deepcopy(raw)
            changed['physical_after'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                e._validate(changed, 0, PROCESS)

if __name__ == "__main__": unittest.main()
