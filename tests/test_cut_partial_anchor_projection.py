"""Offline Cut-only partial gold contracts."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from md_mcp_plugin.embedded.cut_partial_anchor_projection import solve_partial_anchor_projection
from test_cut_frame_projection import png


OUTLINE = [(0, 0), (35, 0), (36, 21), (15, 32), (0, 16)]
VISIBLE = (0, 2, 3)


def anchors(points):
    return [{"id": str(i), "xy_mm": list(point)} for i, point in enumerate(points)]


def projected(points):
    return [(35 + 3*x, 45 + 3*y) for x, y in points]


class PartialCutProjection(unittest.TestCase):
    def test_unique_partial_accounts_for_every_observed_dot(self):
        data = png(projected([OUTLINE[i] for i in VISIBLE]))
        result = solve_partial_anchor_projection(data, anchors(OUTLINE))
        self.assertTrue(result["projection_geometry_verified"], result)
        self.assertTrue(result["all_observed_gold_accounted"])
        self.assertEqual(result["matrix_native_mm_to_logical"], [3, 0, 0, 3, 35, 45])
        self.assertEqual(set(result["unobserved_anchor_ids"]), {"1", "4"})
        self.assertFalse(result["dispatch_authorized"])
        self.assertFalse(result["semantic_line_binding_verified"])

    def test_extra_dot_is_not_dropped(self):
        data = png(projected([OUTLINE[i] for i in VISIBLE]) + [(210, 187)])
        result = solve_partial_anchor_projection(data, anchors(OUTLINE))
        self.assertFalse(result["projection_geometry_verified"], result)
        self.assertEqual(result["reason"], "NO_COMPLETE_OBSERVED_GOLD_ASSIGNMENT")

    def test_symmetric_and_collinear_correspondences_are_rejected(self):
        square = anchors([(0, 0), (25, 0), (25, 25), (0, 25)])
        symmetric = solve_partial_anchor_projection(png([(50, 60), (100, 60), (100, 110)]), square)
        self.assertEqual(symmetric["reason"], "AMBIGUOUS_PARTIAL_GOLD_SIMILARITY")
        collinear = solve_partial_anchor_projection(png([(50, 60), (90, 60), (110, 60)]), anchors(OUTLINE))
        self.assertEqual(collinear["reason"], "PARTIAL_GOLD_NEAR_COLLINEAR")

    def test_full_marker_set_uses_the_existing_strict_path(self):
        result = solve_partial_anchor_projection(png(projected(OUTLINE)), anchors(OUTLINE))
        self.assertEqual(result["reason"], "PARTIAL_GOLD_COUNT_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
