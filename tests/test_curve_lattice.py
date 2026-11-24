"""Two measured scale planning only: no SDK, native input or MD."""
from fractions import Fraction
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from md_mcp_plugin.embedded.curve_lattice import LatticeError, plan_two_scales


def columns(gain, angle=.0):
    return [[gain*math.cos(angle), gain*math.sin(angle)],
            [gain*math.sin(angle), -gain*math.cos(angle)]]


class LatticeTests(unittest.TestCase):
    def assert_valid_plan(self, plan, fine, coarse, delta, tolerance=.002, budget_fraction=.5):
        m, n = plan['coarse_steps_px'], plan['fine_steps_px']
        self.assertTrue(all(type(v) is int and abs(v) <= plan['max_step'] for v in m+n))
        actual = [sum(Fraction(coarse[i][a])*m[i] + Fraction(fine[i][a])*n[i]
                      for i in (0, 1)) for a in (0, 1)]
        error2 = sum((v-Fraction(d))**2 for v, d in zip(actual, delta))
        self.assertLessEqual(error2, (Fraction(tolerance)*Fraction(str(budget_fraction)))**2)
        self.assertEqual(Fraction(plan['predicted_error_squared_mm2_exact']), error2)
        self.assertFalse(plan['native_dispatch_authorized'])
        self.assertFalse(plan['native_owner_binding_verified'])

    def test_subpixel_residual_uses_small_opposing_integer_steps(self):
        fine, coarse = columns(.015314583), columns(.0172)
        delta = [.005886, 0.]
        plan = plan_two_scales(fine, coarse, delta)
        self.assert_valid_plan(plan, fine, coarse, delta)
        self.assertEqual([3, 0], plan['coarse_steps_px'])
        self.assertEqual([-3, 0], plan['fine_steps_px'])

    def test_rotated_reflected_two_axis_case(self):
        for coarse_angle in (.61, .611):
            with self.subTest(coarse_angle=coarse_angle):
                fine, coarse = columns(.015314583, .61), columns(.0172, coarse_angle)
                wanted_m, wanted_n = [3, -4], [-3, 4]
                delta = [sum(coarse[i][a]*wanted_m[i] + fine[i][a]*wanted_n[i]
                             for i in (0, 1)) for a in (0, 1)]
                self.assert_valid_plan(plan_two_scales(fine, coarse, delta), fine, coarse, delta)

    def test_zero_displacement_requires_no_input(self):
        plan = plan_two_scales(columns(.015), columns(.017), [0., 0.])
        self.assertEqual(0, plan['scale_batches'])
        self.assertEqual(0, plan['absolute_pixel_units'])

    def test_measured_near_six_to_five_scales_require_large_cancelling_batches(self):
        fine, coarse = columns(.0153141), columns(.01837667)
        delta = [.005886, 0.]
        plan = plan_two_scales(fine, coarse, delta, min_batch_length_px=16)
        self.assert_valid_plan(plan, fine, coarse, delta)
        for vector in (plan['coarse_steps_px'], plan['fine_steps_px']):
            self.assertTrue(not any(vector) or math.hypot(*vector) >= 16)
        self.assertEqual([17, 0], plan['coarse_steps_px'])
        self.assertEqual([-20, 0], plan['fine_steps_px'])

    def test_commensurate_unreachable_and_displacement_budget_reject(self):
        for delta in ([.005, 0.], [100., 100.]):
            with self.subTest(delta=delta), self.assertRaises(LatticeError) as caught:
                plan_two_scales(columns(.02), columns(.04), delta)
            self.assertEqual('LATTICE_TARGET_UNREACHABLE', caught.exception.code)

    def test_prediction_reserve_preserves_the_original_tolerance(self):
        fine, coarse = columns(.02), columns(.04)
        with self.assertRaises(LatticeError) as caught:
            plan_two_scales(fine, coarse, [.0015, 0.])
        self.assertEqual('LATTICE_TARGET_UNREACHABLE', caught.exception.code)

        plan = plan_two_scales(fine, coarse, [.0015, 0.], prediction_budget_fraction=.85)
        self.assertEqual(.002, plan['tolerance_mm'])
        self.assertAlmostEqual(.0017, plan['error_budget_mm'])
        self.assertAlmostEqual(.0015, plan['predicted_error_mm'])
        self.assertFalse(plan['native_dispatch_authorized'])

        with self.assertRaises(LatticeError):
            plan_two_scales(fine, coarse, [.0015, 0.], prediction_budget_fraction=.9)

    def test_full_budget_prefers_the_smallest_error_for_recorded_scale_pair(self):
        fine = [[.01531416666666748, 0.], [0., -.015312083333332302]]
        coarse = [[.02143916666666712, 0.], [0., -.021438750000001505]]
        delta = [-.056709999999981164, -.032330000000001746]
        for budget in (.5, .85):
            with self.subTest(budget=budget), self.assertRaises(LatticeError) as caught:
                plan_two_scales(fine, coarse, delta, min_batch_length_px=16, prediction_budget_fraction=budget)
            self.assertEqual('LATTICE_TARGET_UNREACHABLE', caught.exception.code)

        plan = plan_two_scales(fine, coarse, delta, min_batch_length_px=16, prediction_budget_fraction=1.0)
        self.assert_valid_plan(plan, fine, coarse, delta, budget_fraction=1.0)
        self.assertEqual([-22, -17], plan['coarse_steps_px'])
        self.assertEqual([27, 26], plan['fine_steps_px'])
        self.assertAlmostEqual(.0019786813878436152, plan['predicted_error_mm'])
        self.assertFalse(plan['execution_verified'])

    def test_full_prediction_budget_still_rejects_error_above_tolerance(self):
        with self.assertRaises(LatticeError) as caught:
            plan_two_scales(columns(.02), columns(.04), [.002001, 0.], prediction_budget_fraction=1.0)
        self.assertEqual('LATTICE_TARGET_UNREACHABLE', caught.exception.code)

    def test_nonfinite_pathological_rotation_and_step_budgets_reject(self):
        bad = [([[float('nan'), 0.], [0., .01]], columns(.02), [0., 0.], {}),
               (columns(.01), columns(.02), [float('inf'), 0.], {}),
               ([[.01, 0.], [.01, 0.]], columns(.02), [0., 0.], {}),
               (columns(.01), columns(.02, .15), [0., 0.], {}),
               (columns(.01), columns(.02), [0., 0.], {'max_step': True}),
               (columns(.01), columns(.02), [0., 0.], {'max_step': 33}),
               (columns(.01), columns(.02), [0., 0.], {'min_batch_length_px': float('nan')}),
               (columns(.01, .7), columns(.02, .7), [1e308, 1e308], {}),
               (columns(.01), columns(.02), [0., 0.], {'tolerance_mm': 10**500}),
               (columns(.01), columns(.02), [0., 0.], {'tolerance_mm': float('nan')})]
        for fine, coarse, delta, kwargs in bad:
            with self.subTest(kwargs=kwargs, fine=fine), self.assertRaises(LatticeError):
                plan_two_scales(fine, coarse, delta, **kwargs)


if __name__ == '__main__':
    unittest.main()
