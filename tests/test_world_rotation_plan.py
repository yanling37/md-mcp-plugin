"""Synthetic projection and rigid-motion tests. No native bridge or MD calls."""
import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from md_mcp_plugin.embedded import world_adapter, world_rotation_plan as planner

TOKEN = 'a' * 64
VIEW_REF = world_adapter._encode_ref('12345678-1234-1234-1234-123456789abc', TOKEN)


def projection(rows, degrees):
    angle = math.radians(degrees)
    direction = (math.cos(angle), math.sin(angle), 1.)
    value = [math.fsum(x * y for x, y in zip(row, direction)) for row in rows]
    return [value[0] / value[2], value[1] / value[2]]


def fixture(rows=((50., 0., 200.), (0., -50., 150.), (.08, -.06, 1.)), size=(420, 320)):
    binding = {'scene_token': TOKEN, 'view_ref': VIEW_REF, 'view_fingerprint': 'b' * 64, 'camera_cmt_sha256': 'c' * 64}
    view = {**binding, 'view': '3d', 'coordinate_space': 'viewport_logical_pixels', 'scene_item_grabber_absent': True,
            'mapping': {'viewport_size': list(size), 'start_drag_distance': 10, 'relative_visible': True, 'enabled': True}}
    ring = {**binding, 'pattern_uuid': 'piece', 'pivot_world_mm': [10., 20., 30.], 'axis_world': [0., 0., 1.],
            'projected_cardinals_px': [projection(rows, angle) for angle in (0., 90., 180., 270.)],
            'check_sample': {'angle_degrees': 45., 'viewport_px': projection(rows, 45.)}}
    request = {'pattern_uuid': 'piece', 'group_name': 'Current piece', 'pivot_world_mm': [10., 20., 30.],
               'axis_world': [0., 0., 1.], 'angle_degrees': 90.}
    return request, view, ring


class RotationPlanTests(unittest.TestCase):
    def test_arbitrary_axis_pivot_and_full_turn_matrix(self):
        pivot = [10., 20., 30.]
        matrix = planner.rotation_matrix_world_mm([0, 0, 2], 450, pivot)
        self.assertLess(math.dist(world_adapter._apply_rigid_matrix(matrix, pivot), pivot), 1e-12)
        self.assertLess(math.dist(world_adapter._apply_rigid_matrix(matrix, [11, 20, 30]), [10, 21, 30]), 1e-12)
        full = planner.rotation_matrix_world_mm([2, -3, 4], 720, pivot)
        self.assertEqual(full, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
        arbitrary = planner.rotation_matrix_world_mm([2, -3, 4], -37.1, pivot)
        world_adapter._validate_rigid_matrix(arbitrary, .002)
        self.assertLess(math.dist(world_adapter._apply_rigid_matrix(arbitrary, pivot), pivot), 1e-12)

    def test_perspective_signed_path_and_reverse_axis_same_line(self):
        request, view, ring = fixture()
        result = planner.plan_rotation(request, view, ring)
        self.assertEqual(result['status'], 'CANDIDATE', result)
        self.assertEqual(result['viewport_sweep'], 'counterclockwise')
        self.assertLess(result['projection_check_error_px'], 1e-9)
        self.assertLess(math.dist(result['drag_params']['end'], ring['projected_cardinals_px'][1]), 1e-9)
        self.assertGreaterEqual(result['activation_distance_logical_px'], 10)
        self.assertLessEqual(result['drag_length_logical_px'], 128)
        self.assertFalse(result['world_rotation_verified'])
        self.assertFalse(result['native_dispatch_authorized'])
        request.update(axis_world=[0, 0, -1], pivot_world_mm=[10, 20, 130])
        reversed_axis = planner.plan_rotation(request, view, ring)
        self.assertEqual(reversed_axis['status'], 'CANDIDATE', reversed_axis)
        self.assertEqual(reversed_axis['signed_ring_angle_degrees'], -90)
        self.assertEqual(reversed_axis['viewport_sweep'], 'clockwise')
        self.assertEqual(reversed_axis['axis_line_offset_mm'], 0)
        self.assertLess(math.dist(reversed_axis['drag_params']['end'], ring['projected_cardinals_px'][3]), 1e-9)

    def test_wrong_scope_axis_pivot_or_projection_rejects_without_drag(self):
        for update, reason in (
            (('view', 'camera_cmt_sha256', 'd' * 64), 'RING_SCOPE_DIFFERS'),
            (('request', 'axis_world', [1, 0, 0]), 'AXIS_DOES_NOT_MATCH_CURRENT_RING'),
            (('request', 'pivot_world_mm', [12, 20, 30]), 'PIVOT_LINE_DIFFERS'),
            (('ring', 'check_sample', {'angle_degrees': 45, 'viewport_px': [1, 2]}), 'INDEPENDENT_RING_PROJECTION_MISMATCH'),
        ):
            request, view, ring = fixture()
            target, field, value = update
            {'request': request, 'view': view, 'ring': ring}[target][field] = value
            result = planner.plan_rotation(request, view, ring)
            self.assertEqual(result['reason'], reason, result)
            self.assertIsNone(result['drag_params'])

    def test_arc_extrema_and_native_drag_budget(self):
        delta = 60 / math.sqrt(2)
        request, view, ring = fixture(((delta, delta, 50), (-delta, delta, 50), (0, 0, 1)), (100, 100))
        self.assertTrue(all(0 <= p < 100 for point in ring['projected_cardinals_px'][:2] for p in point))
        clipped = planner.plan_rotation(request, view, ring)
        self.assertEqual(clipped['reason'], 'ARC_LEAVES_CURRENT_VIEWPORT', clipped)
        self.assertAlmostEqual(clipped['arc_bounds_viewport_px'][0][1], 110)
        request, view, ring = fixture(((100, 0, 200), (0, 100, 150), (0, 0, 1)))
        request['angle_degrees'] = 180
        self.assertEqual(planner.plan_rotation(request, view, ring)['reason'], 'DRAG_EXCEEDS_128_LOGICAL_PIXELS')
        request['angle_degrees'] = 2
        self.assertEqual(planner.plan_rotation(request, view, ring)['reason'], 'NO_BOUNDED_ACTIVATION_SPLIT')

    def test_noop_larger_sweep_and_unusable_plane_are_explicit(self):
        request, view, ring = fixture()
        for angle, reason in ((360, 'IDENTITY_ROTATION'), (220, 'SIGNED_SWEEP_EXCEEDS_ONE_DRAG')):
            test_request = dict(request, angle_degrees=angle)
            result = planner.plan_rotation(test_request, view, ring)
            self.assertEqual(result['reason'], reason)
            self.assertIsNone(result['drag_params'])
        flat = copy.deepcopy(ring)
        flat['projected_cardinals_px'] = [[100, 100], [110, 100], [120, 100], [130, 100]]
        self.assertEqual(planner.plan_rotation(request, view, flat)['status'], 'REJECTED')


if __name__ == '__main__':
    unittest.main()
