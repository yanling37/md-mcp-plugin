"""Offline C ABI/transport contracts. Never loads a DLL, MD or a listener."""
from __future__ import annotations

import ctypes
import copy
import hashlib
import importlib
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
embedded = importlib.import_module("md_mcp_plugin.embedded.scene_adapter_v4")
feature = importlib.import_module("md_mcp_plugin.features.scene_adapter_v4")
TOKEN, FP = "a" * 64, "b" * 64
NATIVE = "12345678-1234-1234-1234-123456789abc"


def view():
    return {"view": "2d", "native_view_ref": NATIVE, "view_fingerprint": FP,
            "mapping_fingerprint": "c" * 64,
            "mapping": {"viewport_size": [947, 513], "device_pixel_ratio": 1.5, "checked_actions": [], "start_drag_distance": 10},
            "scene_item_grabber_absent": True}


def params(**extra):
    return {"view_ref": embedded._encode_ref(NATIVE, TOKEN), "view_fingerprint": FP,
            "scene_token": TOKEN, **extra}


def receipt(request):
    op = request["operation"]
    if op == "bezier_drag_precise_unsnapped":
        raw = receipt(dict(request, operation="bezier_drag_unsnapped"))
        start, end = request["start"], request["end"]
        dx, dy = end[0]-start[0], end[1]-start[1]
        activation = [start[0]+12*dx/math.hypot(dx, dy), start[1]+12*dy/math.hypot(dx, dy)]
        raw.update(operation=op, profile=embedded.PRECISE_PROFILE, event_calls=5,
                   activation_point=activation, activation_distance_logical=12, start_drag_distance_before=10,
                   activation_rule="max12_min32_startDragDistance_plus2_along_start_to_end",
                   activation_point_rule_verified=True, pointer_drag_activation_verified=False)
        activation_row = copy.deepcopy(raw["events"][2])
        activation_row.update(stage="activation_move", viewport_logical=activation)
        raw["events"][2]["stage"] = "final_move"
        raw["events"].insert(2, activation_row)
        for index, row in enumerate(raw["events"]):
            row.update(scene=copy.deepcopy(row["viewport_logical"]), button_down_scene=copy.deepcopy(start),
                       last_scene=copy.deepcopy(start if not index else raw["events"][index-1]["scene"]))
        return raw
    raw = {"schema": embedded.SCHEMA, "return_code": 0, "operation": op, "status": "SUCCEEDED",
           "verified": True, "effects_started": False, "requires_recovery": False,
           "event_calls": 0, "QAction_trigger_calls": 0, "physical_state_unchanged": True,
           "semantic_geometry_verified": False, "native_mm_mapping_verified": False,
           "native_handle_hit_verified": False, "native_cut_partition_verified": False,
           "before": view(), "after": view(), "metadata_catalog_complete": True,
           "metadata": [{"role": "current_2d_scene", "properties": [], "public_methods": [
               {"signature": "zoomIn()", "invoked": False}], "public_declaration_is_callable_proof": False}],
           "zoom_fit_frame_actions": [{"object_name": "observedOnlyFit", "text": "Fit", "enabled": True,
                                       "shortcut": "F", "triggered": False}]}
    if op in ("bezier_drag", "bezier_drag_unsnapped"):
        raw.update(event_calls=4, effects_started=True, profile=embedded.PROFILE,
                   press_sent=True, release_sent=True, release_returned=True,
                   paired_release_verified=True, all_events_nonspontaneous=True,
                   dispatch_completed=True, press_outstanding=False)
        raw["events"] = [{"stage": s, "button": b, "buttons": bs, "modifiers": m,
                          "sent": True, "returned": True,
                          "viewport_logical": request["start"] if i < 2 else request["end"]}
                         for i, (s, b, bs, m) in enumerate((
                             ("hover", 0, 0, 0), ("press", 1, 1, 0),
                             ("move", 0, 1, 0x08000000), ("release", 1, 0, 0x08000000)))]
        if op == "bezier_drag_unsnapped":
            raw.update(profile=embedded.UNSNAPPED_PROFILE, QAction_trigger_calls=4)
            names = (("action_ActivateSnapping", "Snap to Pattern"), ("action_Smart_Guide", "Smart Guide"))
            original = [{"class_name": "QAction", "object_name": name, "text": text, "enabled": True} for name, text in names]
            raw["before"]["mapping"]["checked_actions"] = copy.deepcopy(original)
            raw["after"]["mapping"]["checked_actions"] = copy.deepcopy(original)
            raw["gesture_observation_before"] = view()
            raw["gesture_observation_before"]["view_fingerprint"] = "f" * 64
            raw["snap_settings"] = {"schema": "md-fixed-bezier-unsnapped-actions-v1",
                "disabled_before_gesture": True, "restoration_attempted": True, "original_states_restored": True,
                "direct_trigger_calls": 4, "setChecked_calls": 0, "setEnabled_calls": 0,
                "actions": [{"object_name": name, "text": text, "before_checked": True, "off_checked": False,
                             "after_checked": True, "disable_trigger_calls": 1, "restore_trigger_calls": 1, "group": None}
                            for name, text in names]}
    elif op == "zoom_in_at":
        raw.update(event_calls=2, effects_started=True, wheel_profile=embedded.ZOOM_PROFILE,
                   zoom_hover_sendEvent_calls=1, viewport_wheel_sendEvent_calls=1,
                   bezier_scene_event_calls=0, scene_wheel_sendEvent_calls=0, scene_ungrab_calls=0,
                   press_sent=False, release_sent=False, press_outstanding=False,
                   tools_before={"verified": True, "current_mode": 2}, tools_after={"verified": True, "current_mode": 2})
        raw.update({key: True for key in ("hover_stage_scope_verified", "dispatch_completed", "wheel_scope_verified",
                   "all_events_nonspontaneous", "local_technical_cleanup_verified", "current_2d_refs_verified",
                   "scrollbar_ref_identities_preserved", "view_size_DPR_origin_mode_preserved",
                   "intentional_view_and_scroll_value_changes_allowed")})
        raw.update({key: False for key in ("zoom_effect_verified", "zoom_direction_verified", "zoom_magnification_verified",
                   "zoom_pivot_verified", "geometry_preserved_verified_by_bridge", "visible_view_restoration_verified",
                   "automatic_inverse_or_retry_allowed")})
        common = {"viewport_logical": request["point"], "buttons": 0, "modifiers": 0, "source": 3,
                  "sent": True, "returned": True, "send_return": False, "accepted": True,
                  "nonspontaneous": True, "nonspontaneous_after": True}
        raw["events"] = [dict(common, stage="zoom_hover", event_type="GraphicsSceneMouseMove",
                              receiver="QGraphicsScene/QtView2D", button=0, elapsed_before_ms=1, elapsed_after_ms=2),
                         dict(common, stage="zoom_wheel", event_type="Wheel", receiver="QOpenGLWidget/widget2d",
                              angle_delta=[0, -120], pixel_delta=[0, 0], phase=0, inverted=False,
                              elapsed_before_ms=3, elapsed_after_ms=4)]
        raw["after"]["mapping"]["scroll_values"] = [0, 60]
        raw["after"]["view_fingerprint"] = "e" * 64
    elif op == "fit_selected":
        raw.update(QAction_trigger_calls=1, effects_started=True, fit_selected_action_returned=True,
                   fit_action_name="actionShow_Selected_Patterns_in_Window")
    elif op == "set_tool":
        raw.update(QAction_trigger_calls=1, effects_started=True, already_current=False,
                   command_outcome_verified=True,
                   tools_after={"verified": True, "current_mode": embedded.TOOLS.index(request["tool"])})
    elif op == "cut_selected":
        raw.update(QAction_trigger_calls=1, effects_started=True, selected_cut_action_returned=True)
    elif op == "capture":
        raw.update(capture_method="one_viewport_Paint_then_owned_DSA_renderbuffer_resolve_v2",
                   pixel_origin="top_left_after_CPU_vertical_flip", owned_resource_cleanup_failed=False,
                   GL_state_entirely_unchanged_certified=False, source_object_write_freedom_certified=False,
                   pixels_freshness_verified=False, pixels_scene_content_verified=False,
                   normal_Qt_MD_draw_cache_effects_allowed=True, direct_resolve_source_object_writer_calls=0,
                   new_context_calls=0, new_surface_calls=0)
        raw.update({key: True for key in ("paint_event_sent", "paint_event_returned", "paint_nonspontaneous",
                   "capture_recovery_verified", "original_Qt_context_surface_restored", "original_WGL_context_dc_restored",
                   "original_framebuffer_bindings_restored", "source_metadata_before_after_verified",
                   "app_GL_observed_state_unchanged", "owned_resources_cleanup_verified", "owned_name_invalidation_verified",
                   "dual_copies_bit_exact_equal_every_pixel", "dual_baseline_owned_coverage_verified")})
        raw.update(viewport_makeCurrent_calls=1, direct_QPaintEvent_send_calls=1,
                   owned_framebuffer_objects_created=2, owned_framebuffer_objects_deleted=2,
                   owned_texture_objects_created=1, owned_texture_objects_deleted=1, owned_live_object_count=0,
                   DSA_read_calls=4, DSA_baseline_read_calls=2, DSA_copy_read_calls=2, GL_owned_color_blit_calls=2)
    return raw


class FakeCABI:
    """Synthetic low-level reply through real ctypes buffers; never a native call."""
    def __init__(self, callback=receipt):
        self.callback, self.calls = callback, []

    def __call__(self, pid, creation, data, length, output, capacity, written, needed):
        request = json.loads(data[:length])
        self.calls.append(request)
        raw = self.callback(request)
        payload = json.dumps(raw, allow_nan=False).encode()
        ctypes.memmove(output, payload, len(payload))
        ctypes.cast(written, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload)
        ctypes.cast(needed, ctypes.POINTER(ctypes.c_uint32))[0] = len(payload) + 1
        return raw["return_code"]


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0\0\0" * width) * height)) + chunk(b"IEND", b""))


class SceneAdapterContracts(unittest.TestCase):
    def setUp(self):
        self.saved = getattr(sys, embedded.STATE_KEY, None)
        if hasattr(sys, embedded.STATE_KEY):
            delattr(sys, embedded.STATE_KEY)
        self.addCleanup(self.restore)
        temp_parent = ROOT / "tests" / ".scene_adapter_v4_temp"
        temp_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scene_patch = patch.object(embedded, "_scene", return_value={"scene_token": TOKEN})
        self.scene = self.scene_patch.start()
        self.addCleanup(self.scene_patch.stop)

    def restore(self):
        if self.saved is None:
            if hasattr(sys, embedded.STATE_KEY):
                delattr(sys, embedded.STATE_KEY)
        else:
            setattr(sys, embedded.STATE_KEY, self.saved)

    def bind(self, callback=receipt):
        bridge = self.root / "md_qt_scene_adapter_v4_bridge.dll"
        bridge.write_bytes(b"offline fake; never load")
        embedded.install_native_bridge(str(bridge), hashlib.sha256(bridge.read_bytes()).hexdigest(), artifact_root=str(self.root / "captures"))
        cabi = FakeCABI(callback)
        for target, replacement in (("_environment", {"process": {"pid": 1234, "creation_filetime": 5678}}), ("_load", cabi)):
            p = patch.object(embedded, target, return_value=replacement)
            p.start()
            self.addCleanup(p.stop)
        return cabi

    def test_configuration_and_capabilities_do_not_load_or_probe(self):
        with patch.object(ctypes, "CDLL", side_effect=AssertionError("No load")), patch.object(embedded, "_environment", side_effect=AssertionError("No probe")):
            self.bind()
            result = embedded.runtime_capabilities({})
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertFalse(result["result"]["DLL_loaded_by_this_probe"])
        self.assertFalse(result["result"]["native_mm_mapping_verified"])
        self.assertNotIn("scene_adapter_v4.install_native_bridge", embedded.OPERATIONS)

    def test_unconfigured_is_unsupported_without_load(self):
        with patch.object(embedded, "_load", side_effect=AssertionError("No load")):
            reply = embedded.bezier_drag(params(start=[21.25, 37], end=[27.25, 38]))
        self.assertEqual(reply["status"], "UNSUPPORTED")
        self.assertFalse(reply["effects_started"])

    def test_inspect_keeps_metadata_declarations_and_dynamic_view(self):
        cabi = self.bind()
        reply = embedded.inspect_2d({})
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        value = reply["result"]
        self.assertEqual(value["before"]["mapping"]["viewport_size"], [947, 513])
        self.assertEqual(value["before"]["mapping"]["device_pixel_ratio"], 1.5)
        self.assertNotIn("native_view_ref", value["before"])
        self.assertEqual(embedded._decode_ref(value["before"]["view_ref"], TOKEN), NATIVE)
        self.assertFalse(value["metadata"][0]["public_declaration_is_callable_proof"])
        self.assertFalse(value["zoom_fit_frame_actions"][0]["triggered"])
        self.assertEqual(cabi.calls, [{"operation": "inspect", "budget_ms": 750}])

    def test_fixed_profile_binds_every_phase_and_requires_native_after_readback(self):
        cabi = self.bind()
        self.scene.side_effect = [{"scene_token": TOKEN}, {"scene_token": "e" * 64}]
        reply = embedded.bezier_drag(params(start=[21.25, 37], end=[27.25, 38]))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        raw = reply["result"]
        self.assertEqual([r["modifiers"] for r in raw["events"]], [0, 0, 0x08000000, 0x08000000])
        self.assertEqual(raw["scene_token_after"], "e" * 64)
        self.assertFalse(raw["native_handle_hit_verified"])
        self.assertFalse(raw["fulfilled"])
        self.assertEqual(cabi.calls[0]["profile"], embedded.PROFILE)
        self.assertEqual(len(cabi.calls), 1)

    def test_stale_scope_and_unbounded_input_reject_before_CABI(self):
        cabi = self.bind()
        inputs = [params(start=[True, 2], end=[3, 4]), params(start=[float("nan"), 2], end=[3, 4]),
                  params(start=[1, 2], end=[3, 4], modifiers=["alt"]),
                  params(start=[1, 2], end=[3, 4], budget_ms=True),
                  params(start=[1, 2], end=[3, 4], scene_token="e" * 64)]
        for value in inputs:
            self.assertEqual(embedded.bezier_drag(value)["status"], "REJECTED")
        self.scene.return_value = {"scene_token": "f" * 64}
        self.assertEqual(embedded.bezier_drag(params(start=[1, 2], end=[3, 4]))["error"]["code"], "STALE_SCENE")
        self.assertFalse(cabi.calls)

    def test_phase_or_view_receipt_mismatch_blocks_further_dispatch(self):
        def wrong(request):
            value = receipt(request)
            value["events"][1]["modifiers"] = 0x08000000
            return value
        cabi = self.bind(wrong)
        reply = embedded.bezier_drag(params(start=[1, 2], end=[3, 4]))
        self.assertEqual(reply["status"], "UNKNOWN")
        self.assertTrue(reply["requires_recovery"])
        self.assertEqual(embedded.set_tool(params(tool="pattern_edit"))["error"]["code"], "BRIDGE_RECOVERY_REQUIRED")
        self.assertEqual(len(cabi.calls), 1)

    def test_fixed_tools_change_once_or_validate_already_current(self):
        cabi = self.bind()
        for tool in embedded.TOOLS:
            reply = embedded.set_tool(params(tool=tool))
            self.assertEqual(reply["status"], "SUCCEEDED", reply)
            self.assertEqual(reply["result"]["QAction_trigger_calls"], 1)
        def already(request):
            value = receipt(request)
            value.update(QAction_trigger_calls=0, effects_started=False, already_current=True)
            return value
        cabi.callback = already
        self.assertEqual(embedded.set_tool(params(tool="edit_curve_point"))["status"], "SUCCEEDED")
        self.assertEqual(embedded.set_tool(params(tool="actionAnything"))["status"], "REJECTED")
        self.assertEqual(len(cabi.calls), 4)

    def test_selected_cut_never_upgrades_partition_or_handle_claims(self):
        cabi = self.bind()
        reply = embedded.cut_selected(params(selection_fingerprint="c" * 64))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        self.assertFalse(reply["result"]["native_cut_partition_verified"])
        def forged(request):
            value = receipt(request)
            value["native_cut_partition_verified"] = True
            return value
        cabi.callback = forged
        self.assertEqual(embedded.cut_selected(params(selection_fingerprint="c" * 64))["status"], "UNKNOWN")

    def test_capture_path_is_internal_and_png_matches_view_and_bytes(self):
        def capture(request):
            value = receipt(request)
            path = Path(request["png_path"])
            self.assertEqual(path.name, "viewport2d.png")
            self.assertEqual(path.parent.parent, self.root / "captures")
            data = png(3, 2)
            path.write_bytes(data)
            value["image"] = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "byte_count": len(data),
                              "width": 3, "height": 2, "format": "PNG", "native_view_ref": NATIVE, "view_fingerprint": FP}
            return value
        cabi = self.bind(capture)
        reply = embedded.capture_2d(params())
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        self.assertEqual(reply["result"]["image"]["view_ref"], params()["view_ref"])
        self.assertNotIn("native_view_ref", reply["result"]["image"])
        self.assertEqual(embedded.capture_2d(params(png_path="arbitrary.png"))["status"], "REJECTED")
        self.assertEqual(len(cabi.calls), 1)

    def test_capture_rejects_wrong_sha_and_does_not_retry(self):
        def capture(request):
            value = receipt(request)
            path = Path(request["png_path"])
            data = png(1, 1)
            path.write_bytes(data)
            value["image"] = {"path": str(path), "sha256": "0" * 64, "byte_count": len(data), "width": 1, "height": 1,
                              "format": "PNG", "native_view_ref": NATIVE, "view_fingerprint": FP}
            return value
        cabi = self.bind(capture)
        self.assertEqual(embedded.capture_2d(params())["error"]["code"], "INVALID_CAPTURE_ASSET")
        self.assertEqual(len(cabi.calls), 1)

    def test_capture_requires_context_bindings_owned_cleanup_and_dual_resolve(self):
        cabi = self.bind()
        for key, value in (("original_WGL_context_dc_restored", False),
                           ("original_framebuffer_bindings_restored", False),
                           ("owned_resources_cleanup_verified", False),
                           ("owned_live_object_count", 1), ("DSA_copy_read_calls", 1)):
            embedded._state()["blocked"] = False
            def corrupt(request):
                raw = receipt(request)
                raw[key] = value
                return raw
            cabi.callback = corrupt
            reply = embedded.capture_2d(params())
            self.assertEqual(reply["status"], "UNKNOWN", (key, reply))
            self.assertTrue(reply["requires_recovery"])
            self.assertTrue(embedded._state()["blocked"])
        self.assertEqual(len(cabi.calls), 5)

    def test_missing_or_partial_receipt_is_not_replayed(self):
        cabi = self.bind()
        def no_output(*args):
            return -8
        with patch.object(embedded, "_load", return_value=no_output):
            reply = embedded.bezier_drag(params(start=[1, 2], end=[3, 4]))
        self.assertEqual(reply["status"], "UNKNOWN")
        self.assertTrue(embedded._state()["blocked"])
        self.assertFalse(cabi.calls)

    def test_tool_wrong_after_state_and_other_view_receipts_reject(self):
        for corruption in ("tool", "view"):
            embedded._state()["blocked"] = False
            def corrupt(request):
                value = receipt(request)
                if corruption == "tool":
                    value["tools_after"]["current_mode"] = 99
                else:
                    value["before"]["native_view_ref"] = "aaaaaaaa-1234-1234-1234-123456789abc"
                return value
            if corruption == "tool":
                cabi = self.bind(corrupt)
            else:
                cabi.callback = corrupt
            self.assertEqual(embedded.set_tool(params(tool="pattern_edit"))["status"], "UNKNOWN")

    def test_host_registers_only_closed_routes_and_forwards_mutation_journal(self):
        class Registry:
            def __init__(self):
                self.tools = {}
            def tool(self):
                def install(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return install
        registry, context = Registry(), Mock()
        context.call.return_value = {"status": "OFFLINE"}
        feature.register(registry, context)
        self.assertEqual(len(registry.tools), 10)
        registry.tools["scene_adapter_v4_bezier_drag"](**params(start=[1, 2], end=[3, 4]), request_id="d" * 32)
        self.assertEqual(context.call.call_args.kwargs, {"mutation": True, "scene_token": TOKEN, "request_id": "d" * 32})
        self.assertNotIn("png_path", context.call.call_args.args[1])
        with self.assertRaises(ValueError):
            registry.tools["scene_adapter_v4_set_tool"](**params(tool="arbitrary QAction"), request_id="d" * 32)

    def test_zoom_is_exact_hover_then_viewport_minus120_and_not_a_hit_or_scale(self):
        cabi = self.bind()
        reply = embedded.zoom_in_at(params(point=[123.25, 207.5]))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        raw = reply["result"]
        self.assertEqual(raw["event_calls"], 2)
        self.assertEqual(raw["bezier_scene_event_calls"], 0)
        self.assertEqual(raw["viewport_wheel_sendEvent_calls"], 1)
        self.assertEqual(raw["events"][1]["angle_delta"], [0, -120])
        self.assertEqual(raw["after"]["mapping"]["scroll_values"], [0, 60])
        self.assertFalse(raw["zoom_magnification_verified"])
        self.assertFalse(raw["zoom_pivot_verified"])
        self.assertFalse(raw["fulfilled"])
        self.assertEqual(cabi.calls, [{"operation": "zoom_in_at", "budget_ms": 750,
                                      "native_view_ref": NATIVE, "view_fingerprint": FP, "point": [123.25, 207.5]}])

    def test_zoom_cannot_accept_other_delta_key_or_retry_recipe(self):
        cabi = self.bind()
        for bad in (params(point=[3, 4], delta=-120), params(point=[3, 4], modifiers=["ctrl"]),
                    params(point=[3, 4], ticks=36), params(point=[True, 4]), params(point=[3, float("inf")])):
            self.assertEqual(embedded.zoom_in_at(bad)["status"], "REJECTED")
        self.assertFalse(cabi.calls)

    def test_zoom_wrong_sequence_delta_scope_and_bool_counts_are_unknown(self):
        cabi = self.bind()
        for mutation in ("delta", "receiver", "scope", "count", "mode"):
            embedded._state()["blocked"] = False
            def corrupt(request):
                raw = receipt(request)
                if mutation == "delta": raw["events"][1]["angle_delta"] = [0, 120]
                elif mutation == "receiver": raw["events"][1]["receiver"] = "QGraphicsScene/QtView2D"
                elif mutation == "scope": raw["after"]["mapping"]["device_pixel_ratio"] = 2
                elif mutation == "count": raw["viewport_wheel_sendEvent_calls"] = True
                else: raw["tools_after"]["current_mode"] = 1
                return raw
            cabi.callback = corrupt
            self.assertEqual(embedded.zoom_in_at(params(point=[3, 4]))["status"], "UNKNOWN", mutation)
            self.assertTrue(embedded._state()["blocked"])
        self.assertEqual(len(cabi.calls), 5)

    def test_zoom_native_token_change_and_partial_hover_never_retry(self):
        cabi = self.bind()
        self.scene.side_effect = [{"scene_token": TOKEN}, {"scene_token": "e" * 64}]
        result = embedded.zoom_in_at(params(point=[3, 4]))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(len(cabi.calls), 1)
        self.assertTrue(result["requires_recovery"])
        embedded._state()["blocked"] = False
        self.scene.side_effect = None
        def partial(request):
            raw = receipt(request)
            raw.update(status="PARTIAL", return_code=-10, verified=False, requires_recovery=True,
                       event_calls=1, viewport_wheel_sendEvent_calls=0)
            raw["events"] = raw["events"][:1]
            return raw
        cabi.callback = partial
        reply = embedded.zoom_in_at(params(point=[3, 4]))
        self.assertEqual(reply["status"], "PARTIAL", reply)
        self.assertEqual(reply["result"]["event_calls"], 1)
        self.assertEqual(embedded.zoom_in_at(params(point=[3, 4]))["error"]["code"], "BRIDGE_RECOVERY_REQUIRED")
        self.assertEqual(len(cabi.calls), 2)

    def test_unsnapped_profile_is_four_original_phases_with_fixed_flags_restored(self):
        cabi = self.bind()
        result = embedded.bezier_drag_unsnapped(params(start=[3, 4], end=[3.25, 4]))
        self.assertEqual(result["status"], "SUCCEEDED", result)
        raw = result["result"]
        self.assertEqual(raw["profile"], embedded.UNSNAPPED_PROFILE)
        self.assertEqual(raw["QAction_trigger_calls"], 4)
        self.assertTrue(raw["snap_settings"]["original_states_restored"])
        self.assertEqual(raw["gesture_observation_before"]["mapping"]["checked_actions"], [])
        self.assertNotIn("native_view_ref", raw["gesture_observation_before"])
        self.assertEqual([row["modifiers"] for row in raw["events"]], [0, 0, 0x08000000, 0x08000000])
        self.assertEqual(cabi.calls[0]["profile"], embedded.UNSNAPPED_PROFILE)
        self.assertEqual(embedded.bezier_drag_unsnapped(params(start=[3, 4], end=[4, 4], actions=["anything"]))["status"], "REJECTED")

    def test_unsnapped_already_off_has_zero_triggers_and_still_verifies_restore(self):
        def off(request):
            raw = receipt(request)
            raw["QAction_trigger_calls"] = 0
            raw["snap_settings"]["direct_trigger_calls"] = 0
            raw["before"]["mapping"]["checked_actions"] = []
            raw["after"]["mapping"]["checked_actions"] = []
            for row in raw["snap_settings"]["actions"]:
                row.update(before_checked=False, after_checked=False, disable_trigger_calls=0, restore_trigger_calls=0)
            return raw
        self.bind(off)
        self.assertEqual(embedded.bezier_drag_unsnapped(params(start=[3, 4], end=[4, 4]))["status"], "SUCCEEDED")

    def test_unsnapped_restore_does_not_toggle_an_already_restored_original_again(self):
        def satisfied(request):
            raw = receipt(request)
            raw["QAction_trigger_calls"] = raw["snap_settings"]["direct_trigger_calls"] = 3
            raw["snap_settings"]["actions"][0]["restore_trigger_calls"] = 0
            return raw
        self.bind(satisfied)
        reply = embedded.bezier_drag_unsnapped(params(start=[3, 4], end=[4, 4]))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)

    def test_unsnapped_wrong_action_restore_group_baseline_or_counter_rejects(self):
        cabi = self.bind()
        for mutation in ("name", "restored", "group", "baseline", "count", "setChecked"):
            embedded._state()["blocked"] = False
            def corrupt(request):
                raw = receipt(request)
                if mutation == "name": raw["snap_settings"]["actions"][0]["object_name"] = "actionOther"
                elif mutation == "restored": raw["snap_settings"]["actions"][0]["after_checked"] = False
                elif mutation == "group": raw["snap_settings"]["actions"][0]["group"] = {"class_name": "QActionGroup", "object_name": "", "exclusive": True, "current_owner_parent": True}
                elif mutation == "baseline": raw["gesture_observation_before"]["mapping"]["checked_actions"] = copy.deepcopy(raw["before"]["mapping"]["checked_actions"])
                elif mutation == "count": raw["snap_settings"]["actions"][0]["restore_trigger_calls"] = True
                else: raw["snap_settings"]["setChecked_calls"] = 1
                return raw
            cabi.callback = corrupt
            result = embedded.bezier_drag_unsnapped(params(start=[3, 4], end=[4, 4]))
            self.assertEqual(result["status"], "UNKNOWN", (mutation, result))
            self.assertTrue(result["requires_recovery"])
        self.assertEqual(len(cabi.calls), 6)

    def test_preserved_fit_selected_uses_existing_singleton_gate(self):
        self.bind()
        with patch("md_mcp_plugin.embedded.selection.inspect_selection", return_value={"selected": [{}], "scene_token": TOKEN}):
            reply = embedded.fit_selected(params())
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        self.assertEqual(reply["result"]["fit_action_name"], "actionShow_Selected_Patterns_in_Window")

    def test_precise_five_phases_overshoot_then_return_to_requested_endpoint(self):
        cabi = self.bind()
        reply = embedded.bezier_drag_precise_unsnapped(params(start=[100, 100], end=[100.5, 100]))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        raw = reply["result"]
        self.assertEqual(raw["activation_point"], [112.0, 100.0])
        self.assertEqual([row["stage"] for row in raw["events"]], ["hover", "press", "activation_move", "final_move", "release"])
        self.assertEqual([row["modifiers"] for row in raw["events"]], [0, 0, 0x08000000, 0x08000000, 0x08000000])
        self.assertEqual(raw["events"][3]["viewport_logical"], [100.5, 100])
        self.assertEqual(raw["events"][3]["last_scene"], raw["events"][2]["scene"])
        self.assertTrue(raw["snap_settings"]["original_states_restored"])
        self.assertFalse(raw["pointer_drag_activation_verified"])
        self.assertFalse(raw["fulfilled"])
        self.assertEqual(cabi.calls[0]["profile"], embedded.PRECISE_PROFILE)

    def test_precise_direction_and_internal_distance_rule_clamp32(self):
        def clamp(request):
            raw = receipt(request)
            raw.update(start_drag_distance_before=60, activation_distance_logical=32,
                       activation_point=[100, 68])
            for key in ("before", "after", "gesture_observation_before"):
                raw[key]["mapping"]["start_drag_distance"] = 60
            raw["events"][2].update(viewport_logical=[100, 68], scene=[100, 68])
            raw["events"][3]["last_scene"] = [100, 68]
            return raw
        self.bind(clamp)
        reply = embedded.bezier_drag_precise_unsnapped(params(start=[100, 100], end=[100, 99.75]))
        self.assertEqual(reply["status"], "SUCCEEDED", reply)
        self.assertEqual(reply["result"]["activation_distance_logical"], 32)

    def test_precise_bad_activation_last_down_snap_and_counts_reject(self):
        cabi = self.bind()
        for mutation in ("direction", "distance", "out", "last", "down", "snap", "count", "bool_threshold"):
            embedded._state()["blocked"] = False
            def corrupt(request):
                raw = receipt(request)
                if mutation == "direction": raw["activation_point"] = [88, 100]
                elif mutation == "distance": raw["activation_distance_logical"] = 2
                elif mutation == "out": raw["activation_point"] = [1000, 100]
                elif mutation == "last": raw["events"][3]["last_scene"] = request["start"]
                elif mutation == "down": raw["events"][3]["button_down_scene"] = request["end"]
                elif mutation == "snap": raw["snap_settings"]["original_states_restored"] = False
                elif mutation == "count": raw["event_calls"] = 4
                else: raw["start_drag_distance_before"] = True
                return raw
            cabi.callback = corrupt
            reply = embedded.bezier_drag_precise_unsnapped(params(start=[100, 100], end=[100.5, 100]))
            self.assertEqual(reply["status"], "UNKNOWN", (mutation, reply))
            self.assertTrue(reply["requires_recovery"])
        self.assertEqual(len(cabi.calls), 8)

    def test_precise_caller_cannot_supply_activation_or_recipe_and_zero_input_reject_is_safe(self):
        cabi = self.bind()
        for extra in ({"activation": [112, 100]}, {"activation_distance": 1}, {"modifiers": []}, {"profile": "custom"}):
            reply = embedded.bezier_drag_precise_unsnapped(params(start=[100, 100], end=[101, 100], **extra))
            self.assertEqual(reply["status"], "REJECTED")
        self.assertFalse(cabi.calls)
        def zero(request):
            raw = receipt(request)
            raw.update(status="REJECTED", return_code=-5, verified=False, effects_started=False, requires_recovery=False,
                       event_calls=0, QAction_trigger_calls=0, events=[], press_sent=False, release_sent=False,
                       error={"code": "ACTIVATION_POINT_OUTSIDE_VIEWPORT"})
            return raw
        cabi.callback = zero
        reply = embedded.bezier_drag_precise_unsnapped(params(start=[1, 1], end=[0, 1]))
        self.assertEqual(reply["status"], "REJECTED", reply)
        self.assertEqual(reply["result"]["event_calls"], 0)
        self.assertFalse(reply["requires_recovery"])

    def test_r4_is_separate_and_cannot_bypass_earlier_shared_or_r3_recovery(self):
        cabi = self.bind()
        self.assertEqual(embedded.STATE_KEY, "_md_mcp_plugin_scene_adapter_r4_state")
        for key in ("_md_mcp_plugin_scene_adapter_state", "_md_mcp_plugin_scene_adapter_r3_state"):
            with patch.object(sys, key, {"blocked": True}, create=True):
                reply = embedded.zoom_in_at(params(point=[3, 4]))
                self.assertEqual(reply["error"]["code"], "EARLIER_ADAPTER_REQUIRES_RECOVERY")
                self.assertEqual(getattr(sys, key), {"blocked": True})
        self.assertFalse(cabi.calls)


if __name__ == "__main__":
    unittest.main()
