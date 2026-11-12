"""Portable stdlib fixtures; no MD, native module, Pillow or historical IDs."""
import copy
import importlib
import math
from pathlib import Path
import struct
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
projection = importlib.import_module('md_mcp_plugin.embedded.curve_projection')


def chunk(tag, body):
    return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xffffffff)


def png(width, height, raw, kind=6, interlace=0):
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, kind, 0, 0, interlace)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


ANCHORS = [{'id': str(i), 'xy_mm': xy} for i, xy in enumerate([[0, 0], [20, 0], [5, 17], [0, 30]])]
HANDLES = [{'id': 'target', 'xy_mm': [3, 6]}, {'id': 'other', 'xy_mm': [12, 4]}]


def drawing(anchors=ANCHORS, handles=HANDLES, *, mirror=1, omit_anchor=None, hide_target=False, thin_target=False):
    width = height = 260
    data = bytearray([190, 190, 190, 255] * width * height)
    def point(xy): return [150 - 3 * xy[1] * mirror, 40 + 3 * xy[0]]
    def dot(xy, radius, color):
        x, y = map(round, xy)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if 0 <= x + dx < width and 0 <= y + dy < height:
                    j = ((y + dy) * width + x + dx) * 4
                    data[j:j + 4] = bytes(color)
    for row in anchors:
        if row['id'] != omit_anchor: dot(point(row['xy_mm']), 3, [255, 255, 0, 255])
    # Unrelated interior/toolbar markers are allowed, but cannot define the fit.
    dot([20, 180], 3, [255, 255, 0, 255]); dot([20, 20], 3, [255, 255, 0, 255])
    for row in handles:
        if row['id'] == 'target' and hide_target: continue
        if row['id'] == 'target' and thin_target:
            x, y = point(row['xy_mm'])
            for dx, dy in [(-1, 0), (0, -1), (0, 0), (0, 1), (1, 0)]: dot([x + dx, y + dy], 0, [79, 128, 255, 255])
        else: dot(point(row['xy_mm']), 1, [79, 128, 255, 255])
    raw = b''.join(b'\0' + data[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return png(width, height, raw), point


class CurveProjectionTests(unittest.TestCase):
    def test_rgb_all_five_filters_match_known_pixels(self):
        expected = bytes([10, 20, 30, 40, 50, 60, 20, 30, 40, 50, 60, 70])
        encoded = [([10, 20, 30, 40, 50, 60], [20, 30, 40, 50, 60, 70]),
                   ([10, 20, 30, 30, 30, 30], [20, 30, 40, 30, 30, 30]),
                   ([10, 20, 30, 40, 50, 60], [10, 10, 10, 10, 10, 10]),
                   ([10, 20, 30, 35, 40, 45], [15, 20, 25, 20, 20, 20]),
                   ([10, 20, 30, 30, 30, 30], [10, 10, 10, 10, 10, 10])]
        for mode, (first, second) in enumerate(encoded):
            image = projection.decode_png(png(2, 2, bytes([mode] + first + [mode] + second), kind=2))
            self.assertEqual(image['pixels'], expected)

    def test_png_crc_interlace_size_and_decompression_budgets(self):
        valid = png(1, 1, bytes([0, 1, 2, 3, 255]))
        bad_crc = bytearray(valid); bad_crc[29] ^= 1
        for value in [bytes(bad_crc), png(1, 1, b'\0abcd', interlace=1), png(4097, 1, b''),
                      png(1, 1, b'\0abcde'), valid[:-1], valid + b'garbage']:
            with self.assertRaises(ValueError): projection.decode_png(value)

    def test_rotation_reflection_and_extra_observations(self):
        for mirror in (1, -1):
            # Reflection test uses 3 anchors to keep this synthetic canvas bounded.
            anchors = ANCHORS[:3]
            image, point = drawing(anchors, mirror=mirror)
            result = projection.solve_projection(image, anchors, HANDLES, 'target')
            self.assertTrue(result['unique'])
            self.assertEqual(result['target_xy_px'], point(HANDLES[0]['xy_mm']))
            self.assertAlmostEqual(result['scale_px_per_mm'], 3)
            self.assertAlmostEqual(result['anchor_max_error_px'], 0)
            self.assertEqual(len(result['anchor_pairs']), 3)
            self.assertFalse(result['absolute_0p002_mm_accuracy_verified'])
            self.assertFalse(result['native_dispatch_authorized'])

    def test_all_outer_anchors_including_straight_only_corner_are_required(self):
        image, _ = drawing(omit_anchor='3')
        with self.assertRaises(projection.ProjectionError):
            projection.solve_projection(image, ANCHORS, HANDLES, 'target')

    def test_occluded_target_or_thin_blue_cross_is_not_a_filled_handle(self):
        for options in ({'hide_target': True}, {'thin_target': True}):
            image, _ = drawing(**options)
            with self.assertRaises(projection.ProjectionError):
                projection.solve_projection(image, ANCHORS, HANDLES, 'target')

    def test_shared_handle_position_is_not_independently_addressable(self):
        handles = HANDLES + [{'id': 'alias', 'xy_mm': HANDLES[0]['xy_mm']}]
        image, _ = drawing(handles=handles)
        with self.assertRaises(projection.ProjectionError):
            projection.solve_projection(image, ANCHORS, handles, 'target')

    def test_symmetric_anchor_and_handle_correspondence_is_rejected(self):
        anchors = [{'id': str(i), 'xy_mm': xy} for i, xy in enumerate([[0, 0], [20, 0], [20, 20], [0, 20]])]
        handles = [{'id': 'center', 'xy_mm': [10, 10]}]
        image, _ = drawing(anchors, handles)
        with self.assertRaises(projection.ProjectionError) as error:
            projection.solve_projection(image, anchors, handles, 'center')
        self.assertGreater(error.exception.diagnostics['solution_count'], 1)

    def test_dpr_conversion_and_relative_delta_contract(self):
        image, point = drawing()
        result = projection.solve_projection(image, ANCHORS, HANDLES, 'target', device_pixel_ratio=2)
        self.assertEqual(result['target_xy_px'], [v / 2 for v in point(HANDLES[0]['xy_mm'])])
        delta = [result['linear'][i][0] * .1 + result['linear'][i][1] * -.2 for i in range(2)]
        self.assertAlmostEqual(delta[0], .3); self.assertAlmostEqual(delta[1], .15)

    def test_invalid_or_collinear_native_data_has_structured_error(self):
        image, _ = drawing()
        variants = [copy.deepcopy(ANCHORS), [{'id': str(i), 'xy_mm': [i, 0]} for i in range(3)]]
        variants[0][0]['xy_mm'][0] = float('nan')
        for anchors in variants:
            with self.assertRaises(projection.ProjectionError) as error:
                projection.solve_projection(image, anchors, HANDLES, 'target')
            self.assertEqual(error.exception.code, 'INVALID_PROJECTION_INPUT')


def local_image(dots=(), *, dpr=1, line=False):
    w, h = 160 * dpr, 100 * dpr
    data = bytearray([180, 180, 180, 255] * w * h)
    colors = {'blue': [79, 128, 255, 255], 'yellow': [255, 255, 0, 255]}
    for point, color in dots:
        x, y = [round(v * dpr) for v in point]; radius = (1 if color == 'blue' else 3) * dpr
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                j = ((y + dy) * w + x + dx) * 4
                data[j:j + 4] = bytes(colors[color])
    if line:
        for x in range(34, 47):
            for y in (30, 31):
                j = (y * w + x) * 4; data[j:j + 4] = bytes(colors['blue'])
    raw = b''.join(b'\0' + data[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return png(w, h, raw)


class LocalMarkerTests(unittest.TestCase):
    def test_blue_yellow_and_bbox_hit_are_local_only(self):
        for color in ('blue', 'yellow'):
            data = local_image([([40, 30], color), ([100, 60], 'blue')])
            result = projection.track_marker(data, [39, 31], allowed_colors=(color,))
            self.assertEqual(result['xy_px'], [40, 30])
            self.assertEqual(result['hit_xy_px'], [40, 30])
            self.assertEqual(result['offset_px'], [1, -1])
            self.assertEqual(result['candidate_count'], 1)
            self.assertNotIn('blue_candidates', result['trace_record'])
            self.assertFalse(result['handle_identity_verified'])
            self.assertFalse(result['mm_scale_reconstructed'])
            self.assertEqual(result['input_calls'], 0)
            if color == 'yellow':
                with self.assertRaises(projection.ProjectionError): projection.track_marker(data, [40, 30])

    def test_two_dots_or_line_only_reject_without_nearest_fallback(self):
        images = [local_image([([37, 30], 'blue'), ([43, 30], 'blue')]), local_image(line=True)]
        for data in images:
            with self.assertRaises(projection.ProjectionError) as error:
                projection.track_marker(data, [40, 30])
            self.assertEqual(error.exception.code, 'LOCAL_MARKER_NOT_UNIQUE')
            self.assertIn(error.exception.diagnostics['candidate_count'], (0, 2))

    def test_dpr_scales_search_glyph_and_return_to_logical_pixels(self):
        for color in ('blue', 'yellow'):
            data = local_image([([40, 30], color)], dpr=2)
            result = projection.track_marker(data, [39, 31], device_pixel_ratio=2, allowed_colors=(color,))
            self.assertEqual(result['xy_px'], [40, 30])
            self.assertEqual(result['offset_px'], [1, -1])
            self.assertEqual(result['trace_record']['device_pixel_ratio_used'], 2)

    def test_no_external_verified_certificate_or_arbitrary_color(self):
        data = local_image([([40, 30], 'blue')])
        with self.assertRaises(TypeError): projection.track_marker(data, [40, 30], verified=True)
        for options in ({'allowed_colors': ('red',)}, {'radius_px': True}, {'device_pixel_ratio': float('nan')}):
            with self.assertRaises(projection.ProjectionError): projection.track_marker(data, [40, 30], **options)


if __name__ == '__main__': unittest.main()
