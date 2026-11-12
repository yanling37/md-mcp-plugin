"""Partial-visible correspondence fixtures: no MD, SDK, historical IDs or IO."""
import copy
import math
from pathlib import Path
import struct
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from md_mcp_plugin.embedded import curve_partial_projection as partial
from md_mcp_plugin.embedded.curve_projection import ProjectionError

ANCHORS = [dict(id=str(i), xy_mm=p) for i, p in enumerate([[0, 0], [20, 1], [28, 17], [17, 31], [-5, 26], [-11, 9]])]
HANDLES = [dict(id='target', xy_mm=[3, 6]), dict(id='other', xy_mm=[12, 8])]


def picture(anchors=ANCHORS, handles=HANDLES, *, hidden=('2',), missing_target=False,
            extra_gold=False, mirror=1, attached_guide=False, thin_gold=False, thick_guide=False):
    width = height = 260
    pixels = bytearray([190, 190, 190, 255]*width*height)
    def project(point):
        x, y = point
        return [200-3*y*mirror, 50+3*x]
    def dot(point, radius, color):
        x, y = map(round, point)
        for yy in range(y-radius, y+radius+1):
            for xx in range(x-radius, x+radius+1):
                if 0 <= xx < width and 0 <= yy < height:
                    pos = (yy*width+xx)*4
                    pixels[pos:pos+4] = bytes(color)
    if attached_guide:
        for y in range(20, 220):
            for x in (199, 200):
                pos = (y*width+x)*4
                pixels[pos:pos+4] = bytes([255, 255, 0, 255])
    if thick_guide:
        x, y = project(anchors[0]['xy_mm'])
        for yy in range(y-20, y+1):
            for xx in range(x-1, x+2):
                dot([xx, yy], 0, [255, 255, 0, 255])
    for row in anchors:
        if row['id'] not in hidden:
            if thin_gold:
                x, y = map(round, project(row['xy_mm']))
                for xx in range(x-8, x+9):
                    dot([xx, y], 0, [255, 255, 0, 255])
                    dot([xx, y+1], 0, [255, 255, 0, 255])
            else:
                dot(project(row['xy_mm']), 3, [255, 255, 0, 255])
    for row in handles:
        if missing_target and row['id'] == 'target':
            continue
        dot(project(row['xy_mm']), 1, [79, 128, 255, 255])
    if extra_gold:
        dot([15, 230], 3, [255, 255, 0, 255])
    def chunk(tag, body):
        return struct.pack('>I', len(body))+tag+body+struct.pack('>I', zlib.crc32(tag+body)&0xffffffff)
    raw = b''.join(b'\0'+pixels[y*width*4:(y+1)*width*4] for y in range(height))
    png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))+chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b'')
    return png, project


class PartialProjectionTests(unittest.TestCase):
    def test_thick_guide_keeps_the_dot_and_requires_a_visible_blue_target(self):
        png, project = picture(hidden=('2', '5'), thick_guide=True)
        observed = partial.solve_with_fallback(png, ANCHORS, HANDLES, 'target')
        self.assertEqual(4, observed['visible_anchor_count'])
        self.assertEqual(project(HANDLES[0]['xy_mm']), observed['target_xy_px'])
        self.assertFalse(observed['native_dispatch_authorized'])

        png, _ = picture(hidden=('2', '5'), thick_guide=True, missing_target=True)
        with self.assertRaises(ProjectionError) as caught:
            partial.solve_with_fallback(png, ANCHORS, HANDLES, 'target')
        self.assertEqual('NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION', caught.exception.code)

    def test_missing_farthest_native_anchor_still_has_unique_observed_bijection(self):
        png, project = picture()
        result = partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        self.assertEqual(['2'], result['missing_anchor_ids'])
        self.assertEqual(5, len(result['anchor_pairs']))
        self.assertTrue(result['observed_gold_bijection_complete'])
        self.assertFalse(result['all_native_anchors_visible'])
        self.assertEqual(project(HANDLES[0]['xy_mm']), result['target_xy_px'])
        self.assertLessEqual(result['hypotheses_tested'], 20000)
        self.assertFalse(result['native_dispatch_authorized'])

    def test_four_visible_rotated_reflected_and_dpr_contract(self):
        for mirror in (1, -1):
            # Keep the negative-reflection image within this bounded canvas.
            anchors = [dict(id=r['id'], xy_mm=[r['xy_mm'][0], r['xy_mm'][1]-15]) for r in ANCHORS]
            handles = [dict(id=r['id'], xy_mm=[r['xy_mm'][0], r['xy_mm'][1]-15]) for r in HANDLES]
            png, project = picture(anchors, handles, hidden=('2', '5'), mirror=mirror)
            result = partial.solve_projection(png, anchors, handles, 'target', device_pixel_ratio=2)
            self.assertEqual(4, result['visible_anchor_count'])
            self.assertEqual([v/2 for v in project(handles[0]['xy_mm'])], result['target_xy_px'])
            self.assertAlmostEqual(3, result['scale_px_per_mm'])

    def test_hidden_wrong_or_aliased_target_rejects(self):
        png, _ = picture(missing_target=True)
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        png, _ = picture()
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, ANCHORS, HANDLES, 'unknown')
        handles = HANDLES+[dict(id='alias', xy_mm=HANDLES[0]['xy_mm'])]
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, ANCHORS, handles, 'target')

    def test_full_density_dot_stays_separate_from_guide_and_thin_lines_do_not_qualify(self):
        png, _ = picture(attached_guide=True)
        result = partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        self.assertEqual(5, result['visible_anchor_count'])
        self.assertEqual(['2'], result['missing_anchor_ids'])
        png, _ = picture(thin_gold=True)
        with self.assertRaises(ProjectionError) as caught:
            partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        self.assertEqual('VISIBLE_MARKERS_INCOMPLETE', caught.exception.code)

    def test_unexplained_gold_symmetric_assignment_and_budget_reject(self):
        png, _ = picture(extra_gold=True)
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        square = [dict(id=str(i), xy_mm=p) for i, p in enumerate([[0, 0], [20, 0], [20, 20], [0, 20]])]
        center = [dict(id='target', xy_mm=[10, 10])]
        png, _ = picture(square, center, hidden=())
        with self.assertRaises(ProjectionError) as caught:
            partial.solve_projection(png, square, center, 'target')
        self.assertGreater(caught.exception.diagnostics['solution_count'], 1)
        png, _ = picture()
        with self.assertRaises(ProjectionError) as caught:
            partial.solve_projection(png, ANCHORS, HANDLES, 'target', max_hypotheses=1)
        self.assertEqual('PROJECTION_HYPOTHESIS_BUDGET', caught.exception.code)

    def test_fewer_than_four_or_nonfinite_native_input_rejects(self):
        png, _ = picture(hidden=('2', '4', '5'))
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, ANCHORS, HANDLES, 'target')
        invalid = copy.deepcopy(ANCHORS)
        invalid[0]['xy_mm'][0] = math.nan
        with self.assertRaises(ProjectionError):
            partial.solve_projection(png, invalid, HANDLES, 'target')


if __name__ == '__main__':
    unittest.main()
