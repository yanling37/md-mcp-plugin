"""Offline fixed-dock contracts; no MD, Qt execution, DLL load or network."""
import copy
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
try:
    from md_mcp_plugin.embedded import rest_dock_identity as embedded
finally:
    sys.path.pop(0)

PROCESS = {"pid": 1234, "creation_filetime": 5678}
TOKEN = "a" * 64


def action(name="actionSet_Simulation_Property", checkable=False, checked=False):
    return {"object_name": name, "enabled": True, "checkable": checkable, "checked": checked,
            "triggered": False, "associated_objects": [], "reference_unique": True}


def report():
    candidate = {"class_name": "QWidget", "object_name": "PropertyEditorContents", "is_hidden": True,
                 "is_visible": False, "is_visible_to_mainwindow": False, "ancestor_dock_present": True,
                 "ancestor_dock": {"toggle_action": action("qt_dockwidget_toggleViewAction", True),
                                   "toggle_action_checked_matches_not_hidden": True},
                 "ancestor_stacks": [{"page_set_calls": 0, "pages": []}], "direct_widget_children": [],
                 "hidden_numeric_read": False, "toggle_restore_authorized": False}
    raw = {"schema": embedded.SCHEMA, "return_code": 0, "status": "READ_COMPLETE", "verified": True,
           "catalog_complete": True, "process_id": PROCESS["pid"], "process_creation_filetime": str(PROCESS["creation_filetime"]),
           "physical_state_unchanged": True, "physical_before": {}, "physical_after": {},
           "simulation_property_action": action(), "candidates": [candidate], "candidate_count": 1}
    raw.update({k: 0 for k in embedded.COUNTS}); raw.update({k: False for k in embedded.FALSE_FLAGS})
    return raw


class RestDockIdentityTests(unittest.TestCase):
    def test_hidden_candidate_is_identity_only_and_does_not_authorize_toggle(self):
        raw = report(); embedded._validate(raw, 0, PROCESS)
        self.assertFalse(raw["candidates"][0]["is_visible"])
        self.assertTrue(raw["candidates"][0]["ancestor_dock"]["toggle_action"]["checkable"])
        self.assertFalse(raw["toggle_restore_authorized"])
        for key in embedded.COUNTS:
            bad = copy.deepcopy(raw); bad[key] = 1
            with self.subTest(key=key), self.assertRaises(ValueError): embedded._validate(bad, 0, PROCESS)
        bad = copy.deepcopy(raw); bad["hidden_numeric_read"] = True
        with self.assertRaises(ValueError): embedded._validate(bad, 0, PROCESS)

    def test_only_fixed_candidates_and_bounded_association_are_accepted(self):
        raw = report(); raw["candidates"][0]["object_name"] = "unrelated"
        with self.assertRaises(ValueError): embedded._validate(raw, 0, PROCESS)
        raw = report(); raw["simulation_property_action"]["associated_objects"] = [{}] * 65
        with self.assertRaises(ValueError): embedded._validate(raw, 0, PROCESS)
        raw = report(); raw["candidates"][0]["ancestor_stacks"][0]["page_set_calls"] = True
        with self.assertRaises(ValueError): embedded._validate(raw, 0, PROCESS)

    def test_real_buffer_boundary_and_scene_rejection_preserve_raw(self):
        import json
        raw = report(); payload = json.dumps(raw).encode(); calls = []
        def native(pid, creation, budget, output, cap, written, needed):
            calls.append((pid, creation, budget)); ctypes.memmove(output, payload, len(payload))
            ctypes.cast(written, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload)
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload) + 1
            return 0
        state = {"configuration": {"path": "test-dock-not-loaded", "sha256": "b" * 64},
                 "library": None, "call": None, "process": None}
        for changed in (False, True):
            with patch.object(sys, embedded.STATE_KEY, state, create=True), \
                    patch.object(embedded._base, "_scene", side_effect=[{"scene_token": TOKEN}, {"scene_token": "c" * 64 if changed else TOKEN}]), \
                    patch.object(embedded._base, "_environment", return_value={"process": PROCESS}), \
                    patch.object(embedded, "_load", return_value=native):
                result = embedded.inspect({"scene_token": TOKEN})
            self.assertEqual(result["status"], "REJECTED" if changed else "SUCCEEDED")
            data = result["result"]["unverified_native_diagnostic"] if changed else result["result"]
            self.assertEqual(data["candidates"][0]["object_name"], "PropertyEditorContents")
        self.assertEqual(len(calls), 2)

if __name__ == "__main__":
    unittest.main()
