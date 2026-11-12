"""Pure tests for the separate tolerant exported Cut-interface check."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from md_mcp_plugin.embedded.cut_interface_connectivity import verify_cut_interface


def triangle(group, *points):
    return {"group": group, "points": [(x, y, 0.) for x, y in points]}


def strip(group, lower, upper):
    rows = []
    for a, b, c, d in zip(lower, lower[1:], upper[1:], upper):
        rows.extend([triangle(group, a, b, c), triangle(group, a, c, d)])
    return rows


def square_halves(delta=.0001, *, reverse=False):
    a, b, c, d = (0, 0), (1, 0), (1, 1), (0, 1)
    x, y = (0, delta), (1, 1 + delta)
    before = [triangle("source", a, b, c), triangle("source", a, c, d)]
    second = (x, d, y) if reverse else (x, y, d)
    after = [triangle("a", a, b, c), triangle("b", *second)]
    return before, after


class TolerantCutInterface(unittest.TestCase):
    def test_unique_chain_keeps_raw_exact_metric(self):
        before, after = square_halves()
        result = verify_cut_interface(before, after, ["a", "b"])
        self.assertTrue(result["verified"], result)
        self.assertEqual((result["before_exact_edge_components"], result["after_exact_edge_components"]), (1, 2))
        self.assertEqual(result["paired_edge_count"], 1)
        self.assertAlmostEqual(result["maximum_paired_endpoint_distance_mm"], .0001)
        self.assertTrue(result["original_boundary_endpoint_closure"])
        self.assertFalse(result["coordinate_welding_applied"])
        self.assertFalse(result["native_topology_verified"])

    def test_exact_shared_edge_does_not_use_tolerant_fallback(self):
        before, after = square_halves(0)
        result = verify_cut_interface(before, after, ["a", "b"])
        self.assertEqual(result["reason"], "INTERFACE_EXACT_UNION_ALREADY_CONNECTED")
        self.assertEqual(result["after_exact_edge_components"], 1)

    def test_large_gap_and_reversed_child_are_rejected(self):
        before, after = square_halves(.003)
        self.assertFalse(verify_cut_interface(before, after, ["a", "b"])["verified"])
        before, after = square_halves(reverse=True)
        self.assertEqual(verify_cut_interface(before, after, ["a", "b"])["reason"],
                         "INTERFACE_ORIENTATION_UNPROVEN")

    def test_ambiguous_counterpart_is_rejected(self):
        before, after = square_halves()
        after[1] = triangle("b", (0, .0001), (1, 1.0001), (0, .001))
        self.assertEqual(verify_cut_interface(before, after, ["a", "b"])["reason"],
                         "AMBIGUOUS_INTERFACE_ENDPOINT")

    def test_interface_must_close_on_original_border(self):
        _, after = square_halves()
        before = strip("source", [(-1, -1), (2, -1)], [(-1, 2), (2, 2)])
        self.assertEqual(verify_cut_interface(before, after, ["a", "b"])["reason"],
                         "INTERFACE_ENDPOINT_CLOSURE_UNPROVEN")

    def test_disconnected_interface_is_rejected(self):
        before = strip("source", [(x, -1) for x in range(5)], [(x, 1) for x in range(5)])
        upper = strip("a", [(x, 0) for x in range(5)], [(x, 1) for x in range(5)])
        lower = strip("b", [(x, -1) for x in range(5)], [(x, -.01 if x == 2 else -.0001) for x in range(5)])
        self.assertEqual(verify_cut_interface(before, upper + lower, ["a", "b"])["reason"],
                         "INTERFACE_CHAIN_UNPROVEN")

    def test_closed_interface_cycle_is_rejected(self):
        before, _ = square_halves()
        after = [triangle("a", (0, 0), (1, 0), (1, 1)),
                 triangle("b", (0, .0001), (1, 1.0001), (1, .0001))]
        self.assertEqual(verify_cut_interface(before, after, ["a", "b"])["reason"],
                         "INTERFACE_CHAIN_UNPROVEN")


if __name__ == "__main__":
    unittest.main()
