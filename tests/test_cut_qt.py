"""Offline Qt-provider contracts; real native partition pure, synthetic SDK/UI.

No MD, Qt, DLL, listener or network use. No supplied generic selector is tested
as actual line hit: all semantic callbacks below are explicitly test fixtures.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from md_mcp_plugin.embedded import cut_qt as cut
from md_mcp_plugin.features import cut_qt as feature

spec = importlib.util.spec_from_file_location("_cut_fixture_for_qt_provider", ROOT / "tests/test_cut.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
TOKEN, AFTER_TOKEN = "a" * 64, "d" * 64
VIEW_FP, SELECTION_FP = "b" * 64, "c" * 64
NATIVE_REF = "12345678-1234-1234-1234-123456789abc"


class CutQtContracts(unittest.TestCase):
    def setUp(self):
        saved = getattr(sys, cut.STATE_KEY, None)
        if hasattr(sys, cut.STATE_KEY):
            delattr(sys, cut.STATE_KEY)
        def restore():
            if saved is None:
                if hasattr(sys, cut.STATE_KEY):
                    delattr(sys, cut.STATE_KEY)
            else:
                setattr(sys, cut.STATE_KEY, saved)
        self.addCleanup(restore)
        self.sdk = fixture.SDK()
        self.reads, self.selects, self.verifies = 0, [], []
        self.changed = False
        api = patch.dict(sys.modules, {"pattern_api": self.sdk})
        api.start()
        self.addCleanup(api.stop)
        scene = patch.object(cut, "scene", side_effect=lambda: {
            "scene_token": AFTER_TOKEN if self.changed else TOKEN,
            "identity": {"project_path": "offline-test.zprj"}, "patterns": cut._native.pattern_rows(),
        })
        scene.start()
        self.addCleanup(scene.stop)
        inspector = patch.object(cut._scene, "inspect_2d", side_effect=self.inspect_adapter)
        self.inspector = inspector.start()
        self.addCleanup(inspector.stop)
        action = patch.object(cut._scene, "cut_selected", side_effect=self.action)
        self.action_mock = action.start()
        self.addCleanup(action.stop)
        cut.install_native_reader(self.reader, name="synthetic native JSON", evidence="Existing test_cut SDK fixture; not live MD")

    def reader(self):
        self.reads += 1
        return copy.deepcopy(self.sdk.native)

    def inspect_adapter(self, params):
        return {"status": "SUCCEEDED", "effects_started": False, "requires_recovery": False,
                "result": {"verified": True, "scene_token": TOKEN,
                           "after": {"view_ref": cut._scene._encode_ref(NATIVE_REF, TOKEN), "view_fingerprint": VIEW_FP},
                           "selection_after": {"selected_items_count": 1, "selection_fingerprint": SELECTION_FP},
                           "tools_after": {"verified": True, "current_mode": 1}}}

    def selector(self, plan):
        self.selects.append(copy.deepcopy(plan))
        return {"synthetic_selection_sequence": len(self.selects)}

    def verifier(self, plan, receipt, inspection):
        self.verifies.append(copy.deepcopy(inspection))
        # These fixtures assert an artificial semantic binding. Production must
        # supply an actually validated native line→selection getter/algorithm.
        return {**{k: plan[k] for k in ("pattern_uuid", "shape_uuid", "line_uuid", "topology_fingerprint")},
                **inspection, "selection_nonce": plan["selection_nonce"]}

    def install(self, selector=None, verifier=None):
        cut.install_line_selector(selector or self.selector, verifier or self.verifier,
                                  name="synthetic semantic selector", evidence="Offline callback only; not a generic picker")

    def action(self, params):
        self.sdk.native = copy.deepcopy(self.sdk.after)
        self.changed = True
        return {"status": "SUCCEEDED", "effects_started": True, "requires_recovery": False,
                "result": {"verified": True, "QAction_trigger_calls": 1, "selected_cut_action_returned": True}}

    def prepare_params(self):
        snapshot = cut._capture_current(self.sdk)
        return {"pattern_uuid": "source", "shape_uuid": "inside", "line_uuid": "cut_line",
                "topology_fingerprint": cut._native._owner(snapshot, "source")[2], "scene_token": TOKEN}

    def invoke(self, name, params):
        return cut.OPERATIONS["cut_qt." + name](params)

    def prepare(self):
        result = self.invoke("prepare", self.prepare_params())
        self.assertEqual(result["status"], "SUCCEEDED", result)
        return result["selection_ref"]

    def execute(self, ref, **changes):
        return self.invoke("execute", {"selection_ref": ref, "scene_token": TOKEN,
                                       "require_surface_preservation": False, **changes})

    def test_no_default_selector_and_no_public_certificate_installation(self):
        result = self.invoke("prepare", self.prepare_params())
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(result["error"]["code"], "NO_SEMANTIC_LINE_SELECTOR")
        self.assertEqual(result["Qt_Cut_trigger_calls"], 0)
        self.assertFalse(self.selects)
        self.action_mock.assert_not_called()
        self.assertNotIn("cut_qt.install_line_selector", cut.OPERATIONS)
        bad = self.invoke("prepare", {**self.prepare_params(), "verified": True, "pixel": [3, 4]})
        self.assertEqual(bad["error"]["code"], "INVALID_PARAMETERS")

    def test_inspect_resolves_native_UUID_and_PI_without_ordinal_getter(self):
        result = self.invoke("inspect", {"pattern_uuid": "source"})
        self.assertEqual(result["status"], "SUCCEEDED", result)
        ref = result["shape_refs"][0]
        self.assertEqual(ref["export_line_ids"], ["cut_line"])
        self.assertTrue(ref["line_uuids"][0].startswith("@native-id:"))
        self.assertTrue(ref["target_geometry_ready"])
        self.assertEqual(ref["runtime_binding"]["runtime_child_index"], 7)
        self.assertEqual(ref["runtime_binding"]["runtime_line_index"], 3)
        self.assertFalse(ref["runtime_binding"]["is_cut_ordinal"])
        self.assertFalse(result["semantic_selection_ready"])
        self.assertEqual(self.sdk.getters, 0)
        self.action_mock.assert_not_called()

    def test_native_partition_positive_uses_one_Cut_and_fresh_reverification(self):
        self.install()
        ref = self.prepare()
        self.assertRegex(ref, r"^cutqt1:[0-9a-f]{32}$")
        self.assertNotIn("PatternList", repr(cut._state()["tickets"][ref]))
        result = self.execute(ref)
        self.assertEqual(result["status"], "SUCCEEDED", result)
        self.assertTrue(result["readback"]["native_partition_observed"])
        self.assertTrue(result["fulfilled"])
        self.assertFalse(result["requires_recovery"])
        self.assertEqual(result["scene_token_after"], AFTER_TOKEN)
        self.assertEqual({q["pattern_uuid"] for q in result["new_pattern_refs"]}, {"source", "created"})
        self.assertEqual(len(self.selects), 1)
        self.assertEqual(len(self.verifies), 2)
        self.assertEqual(self.action_mock.call_count, 1)
        self.assertEqual(self.sdk.calls, [])
        self.assertEqual(self.sdk.getters, 0)
        self.assertFalse(result["surface_preservation"]["3d"])
        self.assertFalse(result["surface_preservation"]["rest"])

    def test_cubic_partition_uses_native_controls_not_picker_fit(self):
        self.sdk.native, self.sdk.after = fixture.fixture(curved=True)
        self.install()
        result = self.execute(self.prepare())
        self.assertEqual(result["status"], "SUCCEEDED", result)
        self.assertAlmostEqual(result["readback"]["checks"]["area_error_mm2"], 0)
        self.assertEqual(len(result["target"]["curve"]["points_mm"]), 4)

    def test_surface_required_default_and_bool_types_reject_zero_Cut(self):
        self.install()
        ref = self.prepare()
        result = self.invoke("execute", {"selection_ref": ref, "scene_token": TOKEN})
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(result["error"]["code"], "SURFACE_PRESERVATION_UNSUPPORTED")
        self.assertIn(ref, cut._state()["tickets"])
        self.assertEqual(self.execute(ref, require_surface_preservation=0)["error"]["code"], "INVALID_BOOLEAN")
        self.action_mock.assert_not_called()

    def test_unknown_refs_expiry_and_replay_reject_without_Cut(self):
        self.install()
        self.assertEqual(self.execute("cutqt1:" + "f" * 32)["error"]["code"], "STALE_SELECTION_REFERENCE")
        ref = self.prepare()
        cut._state()["tickets"][ref]["expires"] = -1
        self.assertEqual(self.execute(ref)["error"]["code"], "STALE_SELECTION_REFERENCE")
        ref = self.prepare()
        self.execute(ref)
        self.assertEqual(self.execute(ref)["error"]["code"], "STALE_SELECTION_REFERENCE")
        self.assertEqual(self.action_mock.call_count, 1)

    def test_scene_topology_and_selection_changes_zero_Cut(self):
        self.install()
        ref = self.prepare()
        result = self.execute(ref, scene_token="e" * 64)
        self.assertEqual(result["error"]["code"], "STALE_SCENE")
        ref = self.prepare()
        self.sdk.native["PatternList"][1]["ShapeInfo"]["LineList"][0]["PointList"][0]["Position"]["x"] += .1
        self.assertEqual(self.execute(ref)["error"]["code"], "SCENE_CHANGED_BEFORE_CUT")
        self.sdk.native, self.sdk.after = fixture.fixture()
        ref = self.prepare()
        original = self.inspect_adapter({})
        original["result"]["selection_after"]["selection_fingerprint"] = "e" * 64
        self.inspector.side_effect = lambda params: original
        self.assertEqual(self.execute(ref)["error"]["code"], "STALE_LINE_SELECTION")
        self.action_mock.assert_not_called()

    def test_wrong_semantic_line_and_plain_verified_flags_never_issue_ref(self):
        self.install(verifier=lambda *args: {"verified": True})
        result = self.invoke("prepare", self.prepare_params())
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["error"]["code"], "SEMANTIC_LINE_SELECTION_UNPROVEN")
        self.assertTrue(result["requires_recovery"])
        self.assertNotIn("selection_ref", result)
        self.assertEqual(result["Qt_Cut_trigger_calls"], 0)
        self.assertEqual(self.invoke("prepare", self.prepare_params())["error"]["code"], "CUT_PROVIDER_RECOVERY_REQUIRED")
        self.action_mock.assert_not_called()

    def test_other_owner_or_changed_verifier_denied_at_execute(self):
        self.install()
        ref = self.prepare()
        def wrong(plan, receipt, inspection):
            result = self.verifier(plan, receipt, inspection)
            result["pattern_uuid"] = "partner"
            return result
        cut._state()["selector"]["verify"] = wrong
        result = self.execute(ref)
        self.assertEqual(result["error"]["code"], "SEMANTIC_LINE_SELECTION_UNPROVEN")
        self.assertFalse(result["effects_started"])
        self.action_mock.assert_not_called()

    def test_adapter_exception_after_effect_still_reads_independent_partition(self):
        self.install()
        ref = self.prepare()
        def crash(params):
            self.action(params)
            raise RuntimeError("synthetic handler failure after partition")
        self.action_mock.side_effect = crash
        reads = self.reads
        result = self.execute(ref)
        self.assertGreater(self.reads, reads)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertTrue(result["readback"]["native_partition_observed"])
        self.assertFalse(result["fulfilled"])
        self.assertIsNone(result["Qt_Cut_trigger_calls"])
        self.assertTrue(cut._state()["blocked"])
        self.assertEqual(self.execute(ref)["error"]["code"], "CUT_PROVIDER_RECOVERY_REQUIRED")
        self.assertEqual(self.action_mock.call_count, 1)

    def test_zero_input_action_rejection_still_reads_after_and_is_safe(self):
        self.install()
        ref = self.prepare()
        self.action_mock.side_effect = lambda params: {"status": "REJECTED", "effects_started": False, "requires_recovery": False,
                                                       "result": {"QAction_trigger_calls": 0, "verified": False}}
        reads = self.reads
        result = self.execute(ref)
        self.assertGreater(self.reads, reads)
        self.assertEqual(result["status"], "REJECTED", result)
        self.assertEqual(result["outcome"], "zero_cut_preflight_rejected")
        self.assertFalse(result["requires_recovery"])
        self.assertEqual(result["Qt_Cut_trigger_calls"], 0)

    def test_action_return_without_partition_and_after_failure_hold(self):
        self.install()
        ref = self.prepare()
        self.action_mock.side_effect = lambda params: {"status": "SUCCEEDED", "effects_started": True, "requires_recovery": False,
            "result": {"verified": True, "selected_cut_action_returned": True, "QAction_trigger_calls": 1}}
        result = self.execute(ref)
        self.assertEqual(result["status"], "PARTIAL", result)
        self.assertFalse(result["readback"]["native_partition_observed"])
        self.assertTrue(result["requires_recovery"])
        self.assertFalse(result["fulfilled"])

    def test_malformed_adapter_payload_and_post_read_exception_never_wash_effects(self):
        self.install()
        ref = self.prepare()
        self.action_mock.side_effect = lambda params: {"status": "SUCCEEDED", "result": None}
        result = self.execute(ref)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertTrue(result["requires_recovery"])
        self.assertIsNone(result["Qt_Cut_trigger_calls"])
        cut._state()["blocked"] = False  # Test-only reset; not a public recovery route.
        self.install()
        ref = self.prepare()
        def fail_after(params):
            self.action(params)
            cut._state()["reader"]["call"] = Mock(side_effect=OSError("synthetic failed native after reader"))
            return {"status": "SUCCEEDED", "effects_started": True, "requires_recovery": False,
                    "result": {"verified": True, "selected_cut_action_returned": True, "QAction_trigger_calls": 1}}
        self.action_mock.side_effect = fail_after
        result = self.execute(ref)
        self.assertEqual(result["error"]["code"], "POST_CUT_READBACK_FAILED")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertTrue(result["effects_started"])
        self.assertEqual(result["Qt_Cut_trigger_calls"], 1)

    def test_contradictory_or_boolean_action_counts_cannot_be_zero_or_success(self):
        for status, effects, count in (("REJECTED", False, 1), ("SUCCEEDED", True, True)):
            cut._state()["blocked"] = False
            self.install()
            ref = self.prepare()
            self.action_mock.side_effect = lambda params: {"status": status, "effects_started": effects, "requires_recovery": False,
                "result": {"verified": status == "SUCCEEDED", "selected_cut_action_returned": True, "QAction_trigger_calls": count}}
            result = self.execute(ref)
            self.assertEqual(result["status"], "UNKNOWN", result)
            self.assertTrue(result["requires_recovery"])
            self.assertFalse(result["fulfilled"])

    def test_owner_selection_or_missing_native_reader_does_not_authorize_line(self):
        self.install()
        actual = self.inspect_adapter({})
        actual["result"]["selection_after"]["selected_items_count"] = 0
        self.inspector.side_effect = lambda params: actual
        result = self.invoke("prepare", self.prepare_params())
        self.assertEqual(result["error"]["code"], "SINGLE_LINE_SELECTION_NOT_ESTABLISHED")
        self.assertEqual(result["Qt_Cut_trigger_calls"], 0)
        self.action_mock.assert_not_called()
        cut._state()["blocked"] = False
        cut.install_native_reader(None)
        with patch.object(cut._native, "_capture", side_effect=cut._native._CutError("NATIVE_SNAPSHOT_UNAVAILABLE", "Synthetic unavailable public reader")):
            result = self.invoke("prepare", {"pattern_uuid": "source", "shape_uuid": "inside", "line_uuid": "cut_line",
                "scene_token": TOKEN, "topology_fingerprint": "sha256:" + "a" * 64})
        self.assertEqual(result["error"]["code"], "NATIVE_SNAPSHOT_UNAVAILABLE")
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertFalse(result["effects_started"])
        self.action_mock.assert_not_called()

    def test_insufficient_shape_PI_or_endpoint_binding_zero_selector_and_Cut(self):
        self.install()
        for mutation in ("line", "endpoint", "PI", "additional"):
            if "GetPatternInputInformation" in vars(self.sdk):
                del self.sdk.GetPatternInputInformation
            self.sdk.native, self.sdk.after = fixture.fixture()
            params = self.prepare_params()
            if mutation == "line":
                params["line_uuid"] = "other-line"
            elif mutation == "endpoint":
                self.sdk.native["PatternList"][0]["InternalLineList"][1]["LineList"][0]["PointList"][0]["Position"]["x"] = 3
            elif mutation == "PI":
                self.sdk.GetPatternInputInformation = lambda index: {"Pattern InputInformation": []}
            else:
                added = copy.deepcopy(self.sdk.native["PatternList"][0]["InternalLineList"][1])
                added["ID"] = "second-internal"
                self.sdk.native["PatternList"][0]["InternalLineList"].append(added)
            result = self.invoke("prepare", params)
            self.assertFalse(result["verified"], (mutation, result))
            self.assertFalse(result["effects_started"])
            self.assertEqual(result["error"]["code"], {"line": "STALE_LINE_REFERENCE", "endpoint": "CUT_ENDPOINTS_UNVERIFIED",
                                                      "PI": "RUNTIME_BINDING_UNAVAILABLE", "additional": "OTHER_INTERNALS_UNVERIFIED"}[mutation])
        self.assertFalse(self.selects)
        self.action_mock.assert_not_called()

    def test_native_reader_data_cannot_replace_actual_runtime_UUIDs(self):
        altered = copy.deepcopy(self.sdk.native)
        altered["PatternList"][0]["ID"] = "invented-owner"
        cut.install_native_reader(lambda: altered, name="bad synthetic reader", evidence="Negative UUID domain fixture")
        result = self.invoke("inspect", {"pattern_uuid": "source"})
        self.assertEqual(result["error"]["code"], "SNAPSHOT_UUID_MISMATCH")
        self.action_mock.assert_not_called()

    def test_core_hooks_clear_tickets_but_cannot_clear_recovery(self):
        self.install()
        ref = self.prepare()
        self.install()
        self.assertEqual(self.execute(ref)["error"]["code"], "STALE_SELECTION_REFERENCE")
        cut._state()["blocked"] = True
        self.install()
        result = self.invoke("capabilities", {})
        self.assertTrue(result["blocked_until_recovery"])
        self.assertFalse(result["generic_semantic_selection_live_verified"])
        self.assertEqual(self.invoke("prepare", self.prepare_params())["Qt_Cut_trigger_calls"], 0)

    def test_feature_only_exposes_closed_routes_and_durable_mutation_args(self):
        class Registry:
            def __init__(self):
                self.tools = {}
            def tool(self):
                def decorator(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return decorator
        registry, context = Registry(), Mock()
        feature.register(registry, context)
        self.assertEqual(len(registry.tools), 4)
        values = self.prepare_params()
        registry.tools["cut_qt_prepare_line_selection"](**values, request_id="f" * 32)
        self.assertEqual(context.call.call_args.args, ("cut_qt.prepare", values))
        self.assertEqual(context.call.call_args.kwargs, {"mutation": True, "scene_token": TOKEN, "request_id": "f" * 32})
        registry.tools["cut_qt_execute_selected"]("cutqt1:" + "f" * 32, TOKEN, "e" * 32)
        self.assertIs(context.call.call_args.args[1]["require_surface_preservation"], True)
        with self.assertRaises(ValueError):
            registry.tools["cut_qt_execute_selected"]("caller-certified-true", TOKEN, "e" * 32, False)


if __name__ == "__main__":
    unittest.main()
