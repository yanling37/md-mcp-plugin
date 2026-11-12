"""Offline Fit All contracts using the v5 synthetic C ABI fixtures."""
from __future__ import annotations

import copy
import inspect
import sys
import unittest
from unittest.mock import Mock, patch

import test_scene_adapter_v5 as v5
from md_mcp_plugin.embedded import scene_adapter_v6 as adapter
from md_mcp_plugin.features import scene_adapter_v6 as feature


def params(**extra):
    return {"view_ref": adapter._encode_ref(v5.NATIVE, v5.TOKEN), "view_fingerprint": v5.FP,
            "scene_token": v5.TOKEN, **extra}


def receipt(request, *, selected_count=0):
    raw = v5.receipt(request)
    raw["schema"] = adapter.SCHEMA
    if request["operation"] == "fit_all":
        selected = {"selected_items_count": selected_count, "selection_fingerprint": "d" * 64}
        raw.update(QAction_trigger_calls=1, effects_started=True, events=[], press_sent=False,
                   release_sent=False, press_outstanding=False, fit_all_action_returned=True,
                   fit_action_name=adapter.FIT_ALL_ACTION, fit_all_scope_verified=True,
                   fit_all_extents_verified=False, selection_before=selected, selection_after=copy.deepcopy(selected))
        raw["after"]["mapping"]["scroll_values"] = [0, 120]
        raw["after"]["view_fingerprint"] = "e" * 64
    return raw


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def install(function):
            self.tools[function.__name__] = function
            return function
        return install


class FitAllContracts(unittest.TestCase):
    def setUp(self):
        state = {"configuration": {"path": "offline fixture"}, "library": None, "call": None, "blocked": False}
        patcher = patch.object(sys, adapter.STATE_KEY, state, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        scene = patch.object(adapter, "_scene", return_value={"scene_token": v5.TOKEN})
        self.scene = scene.start()
        self.addCleanup(scene.stop)
        cabi = self.cabi = v5.FakeCABI(receipt)
        for name, value in (("_environment", {"process": {"pid": 1234, "creation_filetime": 5678}}), ("_load", cabi)):
            patcher = patch.object(adapter, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fit_all_dispatches_once_with_empty_or_multiple_selection(self):
        with patch("md_mcp_plugin.embedded.selection.inspect_selection", side_effect=AssertionError("No SDK singleton gate")):
            for selected_count in (0, 2):
                self.cabi.callback = lambda request: receipt(request, selected_count=selected_count)
                reply = adapter.fit_all(params())
                self.assertEqual(reply["status"], "SUCCEEDED", reply)
                result = reply["result"]
                self.assertEqual(result["selection_after"]["selected_items_count"], selected_count)
                self.assertEqual(result["QAction_trigger_calls"], 1)
                self.assertEqual(result["event_calls"], 0)
                self.assertFalse(result["fit_all_extents_verified"])
                self.assertFalse(result["fulfilled"])
                self.assertEqual(result["after"]["view_fingerprint"], "e" * 64)
                self.assertEqual(result["scene_token_after"], v5.TOKEN)
        expected = {"operation": "fit_all", "budget_ms": 750, "native_view_ref": v5.NATIVE, "view_fingerprint": v5.FP}
        self.assertEqual(self.cabi.calls, [expected, expected])

    def test_fit_selected_keeps_its_singleton_gate_and_native_receipt(self):
        with patch("md_mcp_plugin.embedded.selection.inspect_selection", return_value={"selected": [], "scene_token": v5.TOKEN}):
            self.assertEqual(adapter.fit_selected(params())["error"]["code"], "SINGLE_PATTERN_SELECTION_REQUIRED")
        self.assertFalse(self.cabi.calls)
        with patch("md_mcp_plugin.embedded.selection.inspect_selection", return_value={"selected": [{}], "scene_token": v5.TOKEN}):
            reply = adapter.fit_selected(params())
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        self.assertEqual(reply["result"]["fit_action_name"], "actionShow_Selected_Patterns_in_Window")
        self.assertEqual(self.cabi.calls[0]["operation"], "fit_selected")

    def test_rejects_caller_action_shortcut_and_stale_references_before_dispatch(self):
        for extra in ({"action_name": adapter.FIT_ALL_ACTION}, {"shortcut": ")"}, {"budget_ms": True},
                      {"view_ref": v5.params()["view_ref"]}, {"view_fingerprint": "invalid"}):
            with self.subTest(extra=extra):
                reply = adapter.fit_all(params(**extra))
                self.assertEqual(reply["status"], "REJECTED", reply)
                self.assertFalse(reply["effects_started"])
        self.scene.return_value = {"scene_token": "9" * 64}
        self.assertEqual(adapter.fit_all(params())["error"]["code"], "STALE_SCENE")
        self.assertFalse(self.cabi.calls)

    def test_wrong_native_action_or_unclosed_receipt_blocks_without_replay(self):
        mutations = (
            lambda raw: raw.update(fit_action_name="actionZoom_Extents_All_3D"),
            lambda raw: raw.update(QAction_trigger_calls=True),
            lambda raw: raw.update(event_calls=1),
            lambda raw: raw.update(physical_state_unchanged=False),
            lambda raw: raw.update(fit_all_scope_verified=False),
            lambda raw: raw.update(fit_all_extents_verified=True),
            lambda raw: raw["after"].update(native_view_ref="00000000-0000-0000-0000-000000000000"),
            lambda raw: raw["after"].update(scene_item_grabber_absent=False),
            lambda raw: raw["after"]["mapping"].update(device_pixel_ratio=2.0),
            lambda raw: raw["selection_after"].update(selected_items_count=2),
        )
        for change in mutations:
            with self.subTest(case=mutations.index(change)):
                def corrupt(request):
                    raw = receipt(request)
                    change(raw)
                    return raw

                self.cabi.callback = corrupt
                adapter._state()["blocked"] = False
                before = len(self.cabi.calls)
                reply = adapter.fit_all(params())
                self.assertEqual(reply["status"], "UNKNOWN", reply)
                self.assertTrue(reply["requires_recovery"])
                self.assertEqual(adapter.fit_all(params())["error"]["code"], "BRIDGE_RECOVERY_REQUIRED")
                self.assertEqual(len(self.cabi.calls), before + 1)

    def test_native_action_unavailable_is_a_zero_effect_rejection(self):
        def unavailable(request):
            raw = receipt(request)
            raw.update(return_code=-5, status="REJECTED", verified=False, effects_started=False, QAction_trigger_calls=0,
                       error={"code": "FIT_ALL_ACTION_UNAVAILABLE"})
            return raw

        self.cabi.callback = unavailable
        reply = adapter.fit_all(params())
        self.assertEqual(reply["error"]["code"], "FIT_ALL_ACTION_UNAVAILABLE")
        self.assertFalse(reply["effects_started"])
        self.assertFalse(adapter._state()["blocked"])
        self.assertEqual(len(self.cabi.calls), 1)

    def test_partial_trigger_blocks_followup(self):
        def partial(request):
            raw = receipt(request)
            raw.update(return_code=-10, status="PARTIAL", verified=False, requires_recovery=True,
                       error={"code": "POST_OPERATION_SCOPE_CHANGED"})
            return raw

        self.cabi.callback = partial
        reply = adapter.fit_all(params())
        self.assertEqual(reply["status"], "PARTIAL", reply)
        self.assertTrue(reply["requires_recovery"])
        self.assertEqual(adapter.fit_all(params())["error"]["code"], "BRIDGE_RECOVERY_REQUIRED")
        self.assertEqual(len(self.cabi.calls), 1)

    def test_scene_drift_after_trigger_is_unknown_and_never_retried(self):
        self.scene.side_effect = [{"scene_token": v5.TOKEN}, {"scene_token": "9" * 64}]
        reply = adapter.fit_all(params())
        self.assertEqual(reply["error"]["code"], "SCENE_CHANGED_DURING_FIT_ALL")
        self.assertEqual(reply["status"], "UNKNOWN")
        self.assertTrue(adapter._state()["blocked"])
        self.assertEqual(len(self.cabi.calls), 1)

    def test_v6_cannot_bypass_v5_recovery(self):
        with patch.object(sys, v5.embedded.STATE_KEY, {"blocked": True}, create=True):
            reply = adapter.fit_all(params())
            self.assertEqual(reply["error"]["code"], "EARLIER_ADAPTER_REQUIRES_RECOVERY")
        self.assertFalse(self.cabi.calls)

    def test_host_preserves_v5_signatures_and_adds_one_journaled_tool(self):
        old, new, context = Registry(), Registry(), Mock()
        v5.feature.register(old, context)
        feature.register(new, context)
        mapped = {key.replace("_v5_", "_v6_"): value for key, value in old.tools.items()}
        self.assertEqual(set(new.tools) - mapped.keys(), {"scene_adapter_v6_fit_all"})
        self.assertEqual({key.replace("_v5.", "_v6.") for key in v5.embedded.OPERATIONS} | {"scene_adapter_v6.fit_all"},
                         set(adapter.OPERATIONS))
        for name, previous in mapped.items():
            self.assertEqual(inspect.signature(previous), inspect.signature(new.tools[name]))
        request_id = "f" * 32
        new.tools["scene_adapter_v6_fit_all"](**params(), request_id=request_id)
        context.call.assert_called_once_with("scene_adapter_v6.fit_all", params(budget_ms=750), mutation=True,
                                             scene_token=v5.TOKEN, request_id=request_id)


if __name__ == "__main__":
    unittest.main()
