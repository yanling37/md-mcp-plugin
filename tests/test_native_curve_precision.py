"""Offline numeric/closure fault injection; no SDK, native DLL or listener."""
from __future__ import annotations

from contextlib import ExitStack
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from md_mcp_plugin.embedded import native_curve_precision as precision
from md_mcp_plugin.embedded import native_curve_provider as provider


class BoundaryFixture:
    """Explicit adapter/readback stand-ins, not native geometry certification."""
    def __init__(self, png, *, unknown=False, reach=False, drift_on_fit=False,
                 drift_on_zoom=False, drift_on_capture=False, partial_calibration=False):
        self.png, self.xy = png, [0., 0.]
        self.unknown, self.reach = unknown, reach
        self.drift_on_fit, self.drift_on_zoom = drift_on_fit, drift_on_zoom
        self.drift_on_capture = drift_on_capture
        self.calls, self.drag_count = [], 0
        self.capture_scopes = []
        self.drag_budgets = []
        self.partial_calibration = partial_calibration
        self.request = dict(pattern_uuid="paper", scene_scope={}, reference={},
                            target_xy_mm=[10., 0.], tolerance_mm=.002)

    @staticmethod
    def ok(result=None, effects=False):
        return dict(status="SUCCEEDED", result=result or {}, effects_started=effects,
                    requires_recovery=False, error=None)

    def inspect_2d(self, params):
        self.calls.append("inspect")
        return self.ok(dict(after=dict(view_ref="view", view_fingerprint="fingerprint"),
                            scene_token="scene", tools_after=dict(current_tool="edit_curve_point")))

    def capture_2d(self, params):
        self.calls.append("capture")
        self.capture_scopes.append(params)
        if self.drift_on_capture:
            self.drift_on_capture = False
            self.xy[0] += .25
        return self.ok(dict(after=dict(view_ref="view", view_fingerprint="fingerprint"),
                            scene_token_after="scene", image=dict(path=str(self.png),
                            viewport_size=[1000, 1000], device_pixel_ratio=1)))

    def bezier_drag_precise_unsnapped(self, params):
        self.calls.append("drag")
        self.drag_count += 1
        self.drag_budgets.append(params.get("budget_ms"))
        if self.unknown:
            return dict(status="UNKNOWN", result={}, effects_started=True,
                        requires_recovery=True, error=dict(code="UNKNOWN_BATCH"))
        if self.drag_count == 1:
            self.xy = [10., 0.] if self.reach else [1., 0.]
            return self.ok(dict(paired_release_verified=True), effects=True)
        if self.partial_calibration:
            return dict(status="PARTIAL", result=dict(paired_release_verified=True), effects_started=True,
                        requires_recovery=True, error=dict(code="COOPERATIVE_BUDGET_EXCEEDED"))
        raise precision.StopWrite("TEST_STOP", "Stop before any calibration dispatch")

    # Trusted adapter boundaries use the same explicit fault callback here;
    # these closure tests do not certify either native event recipe or hit point.
    bezier_drag = bezier_drag_precise_unsnapped
    bezier_drag_unsnapped = bezier_drag_precise_unsnapped

    def zoom_in_at(self, params):
        self.calls.append("zoom")
        if self.drift_on_zoom:
            self.xy[0] += .25
        return self.ok(dict(after=dict(view_ref="zoomed-view", view_fingerprint="zoomed-fingerprint"),
                            scene_token_after="scene", tools_after=dict(current_tool="edit_curve_point")), effects=True)

    def fit_selected(self, params):
        self.calls.append("fit")
        if self.drift_on_fit:
            self.xy = [9., 0.]
        return self.ok(effects=True)

    def snapshot(self, uuid):
        return dict(native=dict(xy=self.xy[:]), scope={}, identities=[dict(uuid="paper")],
                    public=dict(scene_fingerprint=str(self.xy), geometry_fingerprint=str(self.xy),
                                topology_fingerprint="unchanged"))

    def verify(self, initial, measured, plan):
        actual = measured["xy"][:]
        error = math.dist(actual, plan["target_xy_mm"])
        return dict(actual_xy_mm=actual, target_error_mm=error,
                    target_reached=error <= self.request["tolerance_mm"],
                    fixed_endpoints_and_other_semantics_preserved=True,
                    scene_unchanged=actual == initial["xy"])

    def run(self):
        analysis = dict(outer_endpoints={"a": [0, 0], "b": [10, 0], "c": [0, 10]},
                        public=dict(curves=[]))
        target = dict(identity=dict(shape_kind="outer"), native_ids=dict(handle_uuid="handle"),
                      current_xy_mm=[0., 0.])
        plan = dict(target_xy_mm=[10., 0.], current_xy_mm=[0., 0.])
        marker = dict(hit_xy_px=[200., 200.], trace_record={})
        def select(params):
            self.calls.append("refresh")
            return self.ok(effects=True)
        with ExitStack() as stack:
            for obj, name, value in (
                (precision.selection, "inspect_selection", lambda p: dict(selected=[dict(pattern_uuid="paper")])),
                (precision.selection, "select_pattern", select),
                (precision.curves, "_read_current", self.snapshot),
                (precision.curves, "prepare_edit", lambda *a, **k: plan),
                (precision.curves, "verify_edit", self.verify),
                (provider, "_resolve", lambda *a: (analysis, "ref", target)),
                (precision, "solve_projection", lambda *a, **k: dict(target_xy_px=[200., 200.],
                    linear=[[10., 0.], [0., -10.]], translation=[200., 200.],
                    anchor_max_error_px=0., target_marker_error_px=0.)),
                (precision, "track_marker", lambda *a, **k: marker.copy()),
            ):
                stack.enter_context(patch.object(obj, name, value))
            return precision.set_handle(self.request, self)


class PrecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(self.temp.cleanup)
        self.png = Path(self.temp.name) / "fixture.png"
        self.png.write_bytes(b"transport stand-in; raster solver explicitly mocked")

    def test_inverse_rotated_reflected_axes(self):
        angle = .43
        columns = [[.01*math.cos(angle), .01*math.sin(angle)],
                   [.01*math.sin(angle), -.01*math.cos(angle)]]
        inverse, resolution = precision.inverse_jacobian(columns)
        for native in ([.003, -.008], [-.1, .2]):
            pixels = [sum(r[i]*native[i] for i in (0, 1)) for r in inverse]
            actual = [sum(columns[i][axis]*pixels[i] for i in (0, 1)) for axis in (0, 1)]
            for expected, observed in zip(native, actual):
                self.assertAlmostEqual(expected, observed, places=12)
        self.assertAlmostEqual(.01, resolution)

    def test_degenerate_and_nonfinite_jacobian_rejected(self):
        for columns in ([[0., 0.], [0., .01]], [[.01, 0.], [.01, 0.]],
                        [[.01, 0.], [0., .02]], [[float("nan"), 0.], [0., .01]],
                        [[float("inf"), 0.], [0., .01]]):
            with self.subTest(columns=columns), self.assertRaises((precision.StopWrite, ValueError)):
                precision.inverse_jacobian(columns)

    def test_unknown_batch_does_not_attempt_view_cleanup(self):
        f = BoundaryFixture(self.png, unknown=True)
        result = f.run()
        self.assertTrue(result["requires_recovery"])
        self.assertFalse(result["closed_input_batch_verified"])
        self.assertNotIn("fit", f.calls)
        self.assertNotIn("refresh", f.calls)

    def test_fit_readback_can_reject_previously_reached_target(self):
        f = BoundaryFixture(self.png, reach=True, drift_on_fit=True)
        result = f.run()
        self.assertNotEqual("SUCCEEDED", result["status"])
        self.assertTrue(result["requires_recovery"])
        self.assertFalse(result["verification"]["target_reached"])
        self.assertIn(result["error"]["code"], ("VIEW_PREPARATION_CHANGED_NATIVE_DATA",
                                             "TARGET_CHANGED_AFTER_VIEW_RESTORATION"))

    def test_known_stopped_calibration_restores_view(self):
        f = BoundaryFixture(self.png)
        result = f.run()
        self.assertEqual("fit", f.calls[-1])
        self.assertEqual("after_stopped_fit_restoration", result["trace"][-1]["stage"])
        self.assertEqual("TEST_STOP", result["error"]["code"])
        self.assertEqual(2, f.drag_count)
        self.assertEqual([None, 1500], f.drag_budgets)

    def test_larger_precision_budget_does_not_accept_a_partial_batch(self):
        f = BoundaryFixture(self.png, partial_calibration=True)
        result = f.run()
        self.assertEqual([None, 1500], f.drag_budgets)
        self.assertEqual("PARTIAL", result["status"])
        self.assertTrue(result["requires_recovery"])
        self.assertFalse(result["closed_input_batch_verified"])
        self.assertNotIn("fit", f.calls)

    def test_zoom_target_only_change_stops_before_calibration(self):
        f = BoundaryFixture(self.png, drift_on_zoom=True)
        result = f.run()
        # verify_edit allows target changes by design. A view-only boundary must
        # compare its own native before/after instead of treating them as edits.
        self.assertEqual(1, f.drag_count, "View-only target drift must not authorize calibration")
        self.assertEqual(1, f.calls.count("zoom"))
        self.assertEqual("capture", f.calls[f.calls.index("zoom") + 1])
        self.assertEqual(dict(view_ref="zoomed-view", view_fingerprint="zoomed-fingerprint", scene_token="scene"),
                         f.capture_scopes[-1])
        self.assertEqual("VIEW_PREPARATION_CHANGED_NATIVE_DATA", result["error"]["code"])
        self.assertTrue(result["requires_recovery"])

    def test_first_capture_drift_cannot_authorize_coarse_drag(self):
        f = BoundaryFixture(self.png, drift_on_capture=True)
        result = f.run()
        self.assertEqual(0, f.drag_count, "Initial screenshot must retain its native anchor/handle source")
        self.assertEqual("VIEW_PREPARATION_CHANGED_NATIVE_DATA", result["error"]["code"])
        self.assertTrue(result["requires_recovery"])


if __name__ == "__main__":
    unittest.main()
