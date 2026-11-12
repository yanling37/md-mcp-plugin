"""Fixed setting contract and low-level CABI boundary simulation; never MD/DLL load."""
import copy
import ctypes
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from md_mcp_plugin.embedded import rest_pinching_override as e

TOKEN = "a" * 64
PROCESS = {"pid": 123, "creation_filetime": 456}


def value(checked):
    path = [{"class_name": "QWidget", "object_name": "PropertyEditorContents", "fixed_contents_root": True},
            {"class_name": "MVPropertyWidget", "object_name": "MVCheckbox", "sibling_object_index": 3, "sibling_widget_index": 3}]
    return {"checked": checked, "text": "On" if checked else "Off", "check_state": 2 if checked else 0,
            "enabled": True, "visible": True, "class_name": "CloUICommon::CheckBox", "object_name": "checkBox",
            "row_owner_path": path,
            "label_owner_path": path + [{"class_name": "CloUICommon::Label", "object_name": "label", "sibling_object_index": 0, "sibling_widget_index": 0}],
            "checkbox_owner_path": path + [{"class_name": "CloUICommon::CheckBox", "object_name": "checkBox", "sibling_object_index": 1, "sibling_widget_index": 1}]}


def raw_fixture(before=False, enabled=True):
    change = before is not enabled
    raw = {"schema": e.SCHEMA, "return_code": 0, "status": "UI_SETTING_READ_BACK", "verified": True,
           "requested_enabled": enabled, "process_id": PROCESS["pid"], "process_creation_filetime": str(PROCESS["creation_filetime"]),
           "effects_started": change, "requires_recovery": False, "button_click_calls": int(change), "click_returned": change,
           "automatic_retry_allowed": False, "solver_rest_write_verified": False, "native_faults_contained": False,
           "direct_UI_setting_only": True, "before": value(before), "after": value(enabled), "original_ui_value": before,
           "change_required": change, "no_effect_observed": not change, "semantic_row_binding_verified": True,
           "main_window_minimized_before": True, "main_window_minimized_after": True, "physical_state_unchanged": True,
           "physical_before": {"focus": "unchanged", "capture": "none"}, "physical_after": {"focus": "unchanged", "capture": "none"}}
    raw.update({key: 0 for key in e.ZERO_COUNTS})
    return raw


class PinchingOverrideTests(unittest.TestCase):
    def setUp(self):
        self.previous = getattr(sys, e.STATE_KEY, None)
        setattr(sys, e.STATE_KEY, {"configuration": None, "library": None, "process": None, "call": None, "blocked": False})

    def tearDown(self):
        if self.previous is None: delattr(sys, e.STATE_KEY)
        else: setattr(sys, e.STATE_KEY, self.previous)

    def invoke(self, raw, *, native_changed=False, transport_error=False):
        calls = []
        def callback(pid, creation, enabled, budget, output, cap, written, needed):
            calls.append((pid, creation, enabled, budget))
            if transport_error: raise ValueError("Lost callback receipt after possible UI click")
            payload = json.dumps(raw).encode("utf8")
            ctypes.memmove(output, payload, len(payload))
            ctypes.cast(written, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload)
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload) + 1
            return raw["return_code"]
        snapshots = [{"scene_fingerprint": "b" * 64, "rows": [{"uuid": "same-current-owner"}]}] * 2
        if native_changed: snapshots[1] = {"scene_fingerprint": "c" * 64, "rows": snapshots[0]["rows"]}
        with patch.object(e._base, "_scene", return_value={"scene_token": TOKEN}), \
             patch.object(e._base, "_environment", return_value={"process": PROCESS}), \
             patch.object(e, "_load", return_value=callback), \
             patch.object(e._snapshot_provider, "_native_snapshot", side_effect=snapshots):
            result = e.set_pinching_override({"scene_token": TOKEN, "enabled": raw["requested_enabled"]})
        return result, calls

    def test_enable_restore_and_noop_low_CABI(self):
        for before, enabled in ((False, True), (True, False), (True, True), (False, False)):
            raw = raw_fixture(before, enabled); result, calls = self.invoke(raw)
            self.assertEqual(result["status"], "SUCCEEDED", result)
            self.assertEqual(calls, [(123, 456, int(enabled), 750)])
            self.assertEqual(result["result"]["native_setting"]["button_click_calls"], int(before is not enabled))
            self.assertIs(result["result"]["original_ui_value"], before)
            self.assertFalse(result["requires_recovery"]); self.assertFalse(e._state()["blocked"])

    def test_unknown_click_is_preserved_and_blocks(self):
        raw = raw_fixture(); raw.update(return_code=2, status="UNKNOWN", verified=False, requires_recovery=True,
                                        reason="REQUESTED_BOOLEAN_NOT_READ_BACK")
        result, calls = self.invoke(raw)
        self.assertEqual(result["status"], "UNKNOWN"); self.assertEqual(len(calls), 1)
        self.assertTrue(result["requires_recovery"]); self.assertTrue(e._state()["blocked"])
        self.assertEqual(result["result"]["native_setting"], raw)

    def test_native_scope_mismatch_and_lost_receipt_hold(self):
        for kwargs in ({"native_changed": True}, {"transport_error": True}):
            result, _ = self.invoke(raw_fixture(), **kwargs)
            self.assertEqual(result["status"], "UNKNOWN"); self.assertTrue(e._state()["blocked"])

    def test_semantic_row_typed_boolean_and_physical_rejections(self):
        variants = []
        for key, value_ in (("button_click_calls", True), ("requested_enabled", 1), ("simulation_calls", 1),
                            ("main_window_minimized_after", False), ("click_returned", False)):
            bad = raw_fixture(); bad[key] = value_; variants.append(bad)
        bad = raw_fixture(); bad["after"]["checkbox_owner_path"][1]["sibling_object_index"] = 4; variants.append(bad)
        bad = raw_fixture(); bad["physical_after"]["focus"] = "changed"; variants.append(bad)
        bad = raw_fixture(); bad["after"]["text"] = "Off"; variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(ValueError): e._validate(bad, 0, PROCESS, True)

    def test_strict_public_params_no_paths_or_arbitrary_controls(self):
        for params in ({"scene_token": TOKEN, "enabled": 1}, {"scene_token": TOKEN, "enabled": True, "path": "anything"},
                       {"scene_token": TOKEN, "enabled": True, "budget_ms": True}):
            with self.assertRaises(ValueError): e._params(params)

if __name__ == "__main__": unittest.main()
