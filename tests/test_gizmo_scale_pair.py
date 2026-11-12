"""Offline journal-state tests for the one-pair Gizmo Scale proof."""

import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import gizmo_scale as scale
from md_mcp_plugin.embedded import gizmo_scale_series as series


TOKEN = 'a'*64
TICKET = 'b'*32
CURRENT = {'scene_token': TOKEN, 'identity': {'project_path': 'current-project.zprj'}}
VIEW = {'native_view_ref': 'native', 'mapping_fingerprint': 'mapping', 'view_fingerprint': 'view'}
FRAME = {**CURRENT, 'scene_identity': CURRENT['identity'], 'camera': {}, 'selection': [], 'view': VIEW}
BASELINE = {'frame': FRAME, 'radius_px': 80., 'pixels_sha256': 'initial', 'world_arrays_sha256': 'world'}
EXPANDED = dict(BASELINE, radius_px=90., pixels_sha256='expanded')
ACTION = {'identified': True, 'enabled': True}
INSPECT = {'verified': True, 'pair_pending': False, 'before': VIEW, 'increase_action': ACTION, 'decrease_action': ACTION}
ACTIVE = {'verified': True, 'pair_pending': True, 'status': 'SUCCEEDED'}
CLOSED = {'verified': True, 'pair_pending': False}


class GizmoScaleSeriesTests(unittest.TestCase):
    @staticmethod
    def frame(radius, pixels):
        return dict(BASELINE, red={'ellipse_center_px': [10., 10.], 'ellipse_semiaxes_px': [radius, radius / 2]},
                    pixels_sha256=pixels)

    @staticmethod
    def phase(increases, decreases, *, closed=False):
        state = {'transaction_id': TICKET, 'native_phases_settled': True}
        for direction, count in [('increase', increases), ('decrease', decreases)]:
            for stage in ('attempts', 'returns', 'verified'):
                state[f'{direction}_{stage}'] = count
        return {**ACTIVE, 'pair_state': state, 'pair_pending': not closed}

    def run_series(self, observed, restored, replies):
        params = {'scene_token': TOKEN, 'roi_px': [0, 0, 1, 1], 'increase_steps': 2, 'pattern_uuid': 'current-pattern'}
        with (patch.object(scale, 'check_scene_token', return_value=CURRENT),
              patch.object(series, 'scene', return_value=CURRENT),
              patch.object(series, '_observe', side_effect=observed),
              patch.object(scale, '_capture_frame', side_effect=[(row, b'png') for row in restored]),
              patch.object(scale, '_native', side_effect=[INSPECT, *replies]) as native):
            return series.prove(params, TICKET), [call.args[0] for call in native.call_args_list]

    def test_full_growth_and_each_exact_inverse_close(self):
        a, b, c = [self.frame(radius, str(radius)) for radius in (80, 160, 320)]
        result, calls = self.run_series([a, b, c], [b, a], [self.phase(1, 0), self.phase(2, 0), self.phase(2, 1),
                                                            self.phase(2, 2), self.phase(2, 2, closed=True)])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertTrue(result['result']['series_growth_verified'])
        self.assertEqual(calls, ['inspect', 'increase', 'increase', 'decrease', 'decrease', 'close'])

    def test_cropped_last_observation_still_restores_the_two_exact_captured_scales(self):
        a, b = self.frame(80, 'base'), self.frame(160, 'one')
        result, calls = self.run_series([a, b, ValueError('cropped')], [b, a],
                                        [self.phase(1, 0), self.phase(2, 0), self.phase(2, 1),
                                         self.phase(2, 2), self.phase(2, 2, closed=True)])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertFalse(result['result']['series_growth_verified'])
        self.assertTrue(result['result']['display_scale_restoration_verified'])
        self.assertEqual(calls.count('decrease'), 2)

    def test_uncertain_inverse_prevents_next_inverse(self):
        a, b, c = [self.frame(radius, str(radius)) for radius in (80, 160, 320)]
        result, calls = self.run_series([a, b, c], [a], [self.phase(1, 0), self.phase(2, 0), self.phase(2, 1)])
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertEqual(calls.count('decrease'), 1)

    def test_one_level_outside_ring_keeps_scope_but_ring_pixel_change_does_not(self):
        red = {'support_pixel_bounds_xyxy_inclusive': [1, 1, 2, 2]}
        a = {**self.frame(80, 'old'), 'red': red, 'roi_px': [0, 0, 8, 8],
             'frame': {**FRAME, 'image': {}}}
        b = dict(a, pixels_sha256='new')
        original = b'\x04\x05\x06\xff' * 64
        for index, allowed in ((2 * 8 + 2, False), (5 * 8 + 5, True)):
            modified = bytearray(original)
            modified[index * 4] += 1
            decoded = [{'width': 8, 'height': 8, 'pixels': values} for values in (original, modified)]
            with (patch.object(scale, '_frame_bytes', return_value=b'png'),
                  patch.object(scale, '_red_observation'),
                  patch.object(scale, '_qualified_red', return_value=red),
                  patch.object(scale, 'decode_png', side_effect=decoded)):
                observed = series._display_restoration(a, b)
            self.assertEqual(observed, {'exact_pixels': False, 'outside_red_ring_noise_pixels': 1} if allowed else None)
