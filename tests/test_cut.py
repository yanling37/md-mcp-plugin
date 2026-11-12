"""Offline-only Cut contract/readback tests; no MD or network connection."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from md_mcp_plugin.embedded import cut
from md_mcp_plugin.features import cut as host


def line(identity, points):
    kind = "Bezier Curve" if len(points) == 4 else "Straight"
    return {"ID": identity, "PointList": [
        {"PointType": kind, "Position": {"x": x, "y": y}} for x, y in points]}


def shape(identity, vertices):
    return {"ID": identity, "IsClosed": True, "LineList": [
        line(identity + str(i), [a, b])
        for i, (a, b) in enumerate(zip(vertices, vertices[1:] + vertices[:1]))]}


def fixture(curved=False):
    cut_points = [[0, 0], [4, 0], [6, 10], [10, 10]] if curved else [[0, 0], [10, 10]]
    internal = {"ID": "inside", "ShapeType": 1, "IsClosed": False,
                "LineList": [line("cut_line", cut_points)]}
    source = {"ID": "source", "Name": "Source", "ShapeInfo": shape("outer", [[0, 0], [10, 0], [10, 10], [0, 10]]),
              "InternalLineList": [
                  {"ID": "baseline_export_alias", "ShapeType": 2, "LineList": []}, internal]}
    partner = {"ID": "partner", "Name": "Partner", "ShapeInfo": shape("partner_outer", [[20, 0], [21, 0], [21, 1], [20, 1]]),
               "InternalLineList": []}
    before = {"PatternList": [source, partner], "SeamLinePairGroupList": []}
    a = {"ID": "source", "Name": "Source A", "InternalLineList": [],
         "ShapeInfo": {"ID": "outer_a", "IsClosed": True, "LineList": [
             line("cut_a", cut_points), line("top", [[10, 10], [0, 10]]), line("left", [[0, 10], [0, 0]])]}}
    b = {"ID": "created", "Name": "Source B", "InternalLineList": [],
         "ShapeInfo": {"ID": "outer_b", "IsClosed": True, "LineList": [
             line("bottom", [[0, 0], [10, 0]]), line("right", [[10, 0], [10, 10]]),
             line("cut_b", list(reversed(cut_points)))]}}
    after = {"PatternList": [a, b, copy.deepcopy(partner)], "SeamLinePairGroupList": []}
    return before, after


class SDK:
    def __init__(self, returned=True, effect=True, curved=False, reverse_order=False):
        self.native, self.after = fixture(curved)
        self.returned, self.effect, self.reverse_order = returned, effect, reverse_order
        self.calls, self.exports, self.getters = [], 0, 0
        self.raise_after_mutation = False
        self.fail_readback = False
        self.empty_getter = False
        self.export_false = False

    def papers(self):
        return list(reversed(self.native["PatternList"])) if self.reverse_order else self.native["PatternList"]

    def GetPatternCount(self):
        return len(self.papers())

    def GetPatternInformation(self, index):
        paper = self.papers()[index]
        return json.dumps({"uuid": paper["ID"], "name": paper["Name"]})

    def GetPatternInputInformation(self, index):
        paper = self.papers()[index]
        children = []
        for item in paper["InternalLineList"]:
            if item["ShapeType"] != 1:
                continue
            raw = item["LineList"][0]["PointList"]
            children.append({
                "Shape index": "7", "Shape type": "InnerShape",
                "PointList": [{"Point index": str(i), "Point positionX": q["Position"]["x"],
                               "Point positionY": q["Position"]["y"]} for i, q in enumerate([raw[0], raw[-1]])],
                "LineList": [{"Line index": "3", "Line length": 20.0, "Line type": "Straight type"}],
            })
        return json.dumps({"Pattern InputInformation": [{
            "Pattern index": "0", "Pattern name": paper["Name"], "Children Information": children}]})

    def ExportPatternJSON(self, path):
        self.exports += 1
        if self.fail_readback and self.calls:
            raise OSError("synthetic readback failure")
        if self.export_false:
            return False
        Path(path).write_text(json.dumps(self.native), encoding="utf-8")
        return True

    def GetPatternInternalShapePoints(self, index, ordinal):
        """GetPatternInternalShapePoints(arg0: int, arg1: int) -> list[list[float]]
        Coordinates are millimeters with Y increasing downward.
        """
        self.getters += 1
        if self.empty_getter:
            return []
        assert ordinal == 0, "Runtime child 7 must never be used as ordinal"
        item = next(q for q in self.papers()[index]["InternalLineList"] if q["ShapeType"] == 1)
        return [[q["Position"]["x"] + 30, -q["Position"]["y"] + 40] for q in item["LineList"][0]["PointList"]]

    def CutPatternAlongInternalShapes(self, index, ordinal, sew):
        """CutPatternAlongInternalShapes(arg0: int, arg1: int, arg2: bool) -> bool
        Index is 0-based internal shape list order, same as GetPatternInternalShapePoints,
        not the child-shape index in m_children.
        """
        self.calls.append([index, ordinal, sew])
        if self.effect:
            self.native = copy.deepcopy(self.after)
        if self.raise_after_mutation:
            raise RuntimeError("synthetic exception after native side effect")
        return self.returned


class CutTests(unittest.TestCase):
    def invoke(self, sdk, **updates):
        with patch.dict(sys.modules, {"pattern_api": sdk}):
            capture = cut._capture(sdk)
            fingerprint = cut._owner(capture, "source")[2]
            params = {"pattern_uuid": "source", "shape_uuid": "inside", "line_uuid": "cut_line",
                      "topology_fingerprint": fingerprint, "require_surface_preservation": False}
            params.update(updates)
            return cut.OPERATIONS["cut.along_internal_shape"](params)

    def test_verified_partition_has_fresh_refs_and_distinct_indices(self):
        sdk = SDK(reverse_order=True)
        result = self.invoke(sdk)
        self.assertTrue(result["verified"], result)
        self.assertEqual(sdk.calls, [[1, 0, False]])
        self.assertEqual(result["mapping"]["runtime_child_index"], 7)
        self.assertEqual(result["mapping"]["runtime_line_index"], 3)
        self.assertEqual(result["readback"]["created_pattern_uuids"], ["created"])
        self.assertEqual({r["pattern_uuid"] for r in result["new_pattern_refs"]}, {"source", "created"})
        self.assertTrue(all(r["topology_fingerprint"].startswith("sha256:") for r in result["new_pattern_refs"]))
        self.assertFalse(result["surface_preservation"]["3d"])

    def test_cubic_controls_and_exact_boundary_readback(self):
        result = self.invoke(SDK(curved=True))
        self.assertTrue(result["verified"], result)
        self.assertAlmostEqual(result["readback"]["checks"]["area_error_mm2"], 0.0, places=7)

    def test_false_without_effect_is_rejected_once(self):
        sdk = SDK(returned=False, effect=False)
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "rejected")
        self.assertFalse(result["mutation_observed"])
        self.assertFalse(result["fulfilled"])
        self.assertEqual(len(sdk.calls), 1)
        self.assertTrue(result["do_not_retry"])

    def test_false_with_effect_is_partial_not_success(self):
        sdk = SDK(returned=False)
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "partial")
        self.assertTrue(result["readback"]["native_partition_observed"])
        self.assertFalse(result["fulfilled"])
        self.assertEqual(len(sdk.calls), 1)

    def test_true_without_split_is_unfulfilled(self):
        sdk = SDK(effect=False)
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "unfulfilled")
        self.assertFalse(result["fulfilled"])
        self.assertFalse(result["readback"]["native_partition_observed"])
        self.assertEqual(len(sdk.calls), 1)

    def test_exception_after_effect_still_reads_back_and_never_resends(self):
        sdk = SDK()
        sdk.raise_after_mutation = True
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "unknown")
        self.assertTrue(result["readback"]["native_partition_observed"])
        self.assertFalse(result["fulfilled"])
        self.assertEqual(len(sdk.calls), 1)

    def test_postcall_readback_failure_preserves_invocation(self):
        sdk = SDK()
        sdk.fail_readback = True
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "unknown")
        self.assertIs(result["invocation"]["returned"], True)
        self.assertEqual(result["error"]["code"], "POST_CUT_READBACK_FAILED")
        self.assertEqual(len(sdk.calls), 1)

    def test_nonbool_native_return_is_unknown(self):
        sdk = SDK(returned=1)
        result = self.invoke(sdk)
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["fulfilled"])

    def test_nonfinite_native_return_stays_json_serializable(self):
        result = self.invoke(SDK(returned=float("nan")))
        self.assertEqual(result["status"], "UNKNOWN")
        json.dumps(result, allow_nan=False)

    def test_type_signature_does_not_prove_md_namespace(self):
        sdk = SDK()
        with patch.object(SDK.CutPatternAlongInternalShapes, "__doc__", "CutPatternAlongInternalShapes(arg0: int, arg1: int, arg2: bool) -> bool"):
            result = self.invoke(sdk)
        self.assertEqual(result["error"]["code"], "MD_CUT_ORDINAL_CONTRACT_UNVERIFIED")
        self.assertEqual(sdk.calls, [])
        self.assertEqual(sdk.getters, 0)

    def test_sparse_or_empty_getter_does_not_dispatch_cut(self):
        sdk = SDK()
        sdk.empty_getter = True
        result = self.invoke(sdk)
        self.assertEqual(result["error"]["code"], "ORDINAL_GETTER_UNVERIFIED")
        self.assertEqual(sdk.calls, [])
        self.assertEqual(sdk.getters, 1)

    def test_stale_reference_and_wrong_line_are_rejected_before_cut(self):
        sdk = SDK()
        result = self.invoke(sdk, topology_fingerprint="sha256:" + "0" * 64)
        self.assertEqual(result["error"]["code"], "STALE_TOPOLOGY")
        self.assertEqual(sdk.calls, [])
        result = self.invoke(sdk, line_uuid="wrong_owner_line")
        self.assertEqual(result["error"]["code"], "STALE_LINE_REFERENCE")
        self.assertEqual(sdk.calls, [])

    def test_multiple_shapes_do_not_enumerate_candidates(self):
        sdk = SDK()
        other = copy.deepcopy(sdk.native["PatternList"][0]["InternalLineList"][1])
        other["ID"] = "other"
        sdk.native["PatternList"][0]["InternalLineList"].append(other)
        result = self.invoke(sdk)
        self.assertEqual(result["error"]["code"], "MULTISHAPE_ORDINAL_UNVERIFIED")
        self.assertEqual(sdk.calls, [])
        self.assertEqual(sdk.getters, 0)

    def test_nonfinite_geometry_is_rejected(self):
        sdk = SDK()
        sdk.native["PatternList"][0]["ShapeInfo"]["LineList"][0]["PointList"][0]["Position"]["x"] = float("nan")
        with patch.dict(sys.modules, {"pattern_api": sdk}):
            result = cut.OPERATIONS["cut.along_internal_shape"]({
                "pattern_uuid": "source", "shape_uuid": "inside", "topology_fingerprint": "sha256:" + "0" * 64,
                "require_surface_preservation": False})
        self.assertEqual(result["error"]["code"], "INVALID_GEOMETRY")
        self.assertEqual(sdk.calls, [])

    def test_surface_requirement_is_default_and_makes_no_api_call(self):
        with patch.object(cut, "_api", side_effect=AssertionError("must not import MD")):
            result = cut.OPERATIONS["cut.along_internal_shape"]({
                "pattern_uuid": "source", "shape_uuid": "inside", "topology_fingerprint": "sha256:" + "0" * 64})
        self.assertEqual(result["error"]["code"], "SURFACE_PRESERVATION_UNSUPPORTED")
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertFalse(result["effects_started"])

    def test_probe_only_inspects_metadata(self):
        sdk = SDK()
        with patch.dict(sys.modules, {"pattern_api": sdk}):
            result = cut.OPERATIONS["cut.probe_runtime"]({})
        self.assertTrue(result["verified"])
        self.assertEqual((sdk.exports, sdk.getters, len(sdk.calls)), (0, 0, 0))
        self.assertEqual(result["observation"], "metadata_only_not_execution")

    def test_deprecated_export_returns_real_limitation_with_runtime_refs(self):
        sdk = SDK()
        sdk.export_false = True
        with patch.dict(sys.modules, {"pattern_api": sdk}):
            result = cut.OPERATIONS["cut.inspect"]({"pattern_uuid": "source"})
        self.assertTrue(result["verified"])
        self.assertFalse(result["native_snapshot_available"])
        self.assertIsNone(result["topology_fingerprint"])
        self.assertEqual(result["runtime_child_refs"][0]["child_index"], "7")
        self.assertIsNone(result["runtime_child_refs"][0]["shape_uuid"])
        self.assertEqual(sdk.calls, [])

    def test_count_increase_and_cut_edges_do_not_hide_boundary_damage(self):
        sdk = SDK()
        sdk.after["PatternList"][1]["ShapeInfo"]["LineList"][0]["PointList"][1]["Position"]["x"] = 9
        result = self.invoke(sdk)
        self.assertFalse(result["fulfilled"])
        self.assertEqual(result["outcome"], "partial")
        self.assertFalse(result["readback"]["checks"]["boundary_partition_verified"])

    def test_cut_and_sew_is_passed_but_not_claimed_verified(self):
        sdk = SDK()
        result = self.invoke(sdk, cut_and_sew=True)
        self.assertEqual(sdk.calls, [[0, 0, True]])
        self.assertFalse(result["fulfilled"])
        self.assertEqual(result["cut_and_sew_readback"], "unverified")

    def test_host_registration_routes_typed_parameters_through_context(self):
        class MCP:
            tools = {}
            def tool(self):
                def register(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return register
        class Context:
            def call(self, operation, params, **kwargs):
                return {"operation": operation, "params": params, **kwargs}
        mcp = MCP()
        host.register(mcp, Context())
        self.assertEqual(set(mcp.tools), {"cut_probe_runtime", "cut_inspect", "cut_along_internal_shape"})
        result = mcp.tools["cut_along_internal_shape"](
            "source", "inside", "sha256:" + "a" * 64, "scene", require_surface_preservation=False, request_id="request")
        self.assertTrue(result["mutation"])
        self.assertEqual(result["scene_token"], "scene")
        self.assertEqual(result["request_id"], "request")
        self.assertEqual(result["operation"], "cut.along_internal_shape")
        with self.assertRaises(ValueError):
            mcp.tools["cut_along_internal_shape"]("source", "inside", "bad", "scene")
        with self.assertRaises(ValueError):
            mcp.tools["cut_along_internal_shape"]("source", "inside", "sha256:" + "a" * 64, "scene", cut_and_sew=1)


if __name__ == "__main__":
    unittest.main()
