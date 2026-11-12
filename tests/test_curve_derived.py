import copy
import unittest
from md_mcp_plugin.embedded import curves


def native():
    def point(ident, xy, kind):
        return {"ID": ident, "PointType": kind, "Position": dict(zip(('x', 'y'), xy))}
    start = point('a', [0, 0], 'Straight')
    end = point('b', [4, 0], 'Straight')
    outer = [{'ID': 'curve', 'PointList': [start, point('h1', [0, 4], 'Bezier Curve'), point('h2', [4, 4], 'Bezier Curve'), end]},
             {'ID': 'closing', 'PointList': [copy.deepcopy(end), copy.deepcopy(start)]}]
    cache = [{'ID': 'derived', 'PointList': [point('ca', [0, 0], 'Straight'), point('mid', [2, 3], 'Polyline'), point('cb', [4, 0], 'Straight')]},
             {'ID': 'derived_closing', 'PointList': [point('cc', [4, 0], 'Straight'), point('cd', [0, 0], 'Straight')]}]
    return {'Unit': 'mm', 'PatternList': [{'ID': 'owner', 'Name': 'Panel', 'ShapeInfo': {'LineList': outer},
                            'InternalLineList': [{'ID': 'baseline', 'ShapeType': 2, 'LineList': cache}]}],
            'SeamLinePairGroupList': [], 'FabricList': []}


class DerivedTests(unittest.TestCase):
    def prepared(self):
        before = native()
        ref = curves.describe_native(before, 'owner')['curves'][0]['handles'][0]['handle_ref']
        plan = curves.prepare_edit(before, 'owner', ref, [1, 4])
        after = copy.deepcopy(before)
        after['PatternList'][0]['ShapeInfo']['LineList'][0]['PointList'][1]['Position']['x'] = 1
        # At t=.5 the four Bernstein weights are 1/8,3/8,3/8,1/8.
        after['PatternList'][0]['InternalLineList'][0]['LineList'][0]['PointList'][1]['Position']['x'] = 2.375
        return before, after, plan

    def test_derived_midpoint_is_accepted_with_explicit_numeric_change(self):
        before, after, plan = self.prepared()
        result = curves.verify_edit(before, after, plan)
        self.assertTrue(result['verified'])
        self.assertFalse(result['fixed_endpoints_and_other_native_data_unchanged'])
        self.assertTrue(result['fixed_endpoints_and_other_semantics_preserved'])
        self.assertFalse(result['derived_native_data_verification']['solver_rest_verified'])

    def test_off_curve_cache_point_and_unrelated_handle_are_rejected(self):
        for bad in ('cache', 'other_handle'):
            before, after, plan = self.prepared()
            if bad == 'cache':
                after['PatternList'][0]['InternalLineList'][0]['LineList'][0]['PointList'][1]['Position']['y'] += .01
            else:
                after['PatternList'][0]['ShapeInfo']['LineList'][0]['PointList'][2]['Position']['x'] += .0001
            self.assertFalse(curves.verify_edit(before, after, plan)['verified'])


if __name__ == '__main__':
    unittest.main()
