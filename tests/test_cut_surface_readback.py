"""Actual OBJ parsing plus pure surface checks. No SDK, MD, sockets or native IO."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from md_mcp_plugin.embedded import mesh
from md_mcp_plugin.embedded.cut_surface_readback import verify_cut_surface

A, B, C, D = (0., 0., 0.), (2., 0., 0.), (2., 2., 0.), (0., 2., 0.)
E, F = (1., 0., 0.), (1., 2., 0.)
OTHER = [((10., 0., 0.), (11., 0., 0.), (10., 1., 0.))]
OLD = {'front': [(A, B, C), (A, C, D)], 'Topstitch': OTHER}
NEW = {'child_left': [(A, E, F), (A, F, D)],
       'child_right': [(E, B, C), (E, C, F)], 'Topstitch': OTHER}


def snapshot(groups, capture):
    # Unwelded per-face arrays exercise coordinate-edge connectivity; global OBJ
    # indices of the unchanged accessory shift when target triangulation changes.
    lines, index = ['mtllib same.mtl', 'o exported'], 0
    for name, triangles in groups.items():
        lines.extend(['g '+name, 'usemtl fabric', 's 1'])
        for triangle in triangles:
            for p in triangle:
                lines.extend(['v '+' '.join(map(str, p)), 'vt '+str(p[0])+' '+str(p[1]), 'vn 0 0 1'])
            indices = range(index+1, index+4)
            lines.append('f '+' '.join(f'{i}/{i}/{i}' for i in indices))
            index += 3
    payload = ('\n'.join(lines)+'\n').encode()
    result = dict(mesh.parse_obj(payload), schema='md-exported-obj-mesh-v1', capture_id=capture,
                  scope='scene_garment_OBJ_export', space='world', unit='mm', axis_order=['X', 'Y', 'Z'],
                  scene_identity=dict(pid=123, project_path='current.zprj'))
    result['snapshot_id'] = mesh._hash(result)
    return result


def compare(old=OLD, new=NEW, **kwargs):
    return verify_cut_surface(snapshot(old, 'before'), snapshot(new, 'after'),
                              ['front'], ['child_left', 'child_right'], **kwargs)


class CutSurfaceReadbackTests(unittest.TestCase):
    def test_planar_split_and_real_retriangulation_pass_exported_sample_scope(self):
        result = compare()
        self.assertTrue(result['verified'], result)
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(dict(before=2, after=4), result['triangle_counts'])
        self.assertEqual(14, result['before_to_after']['sample_count'])
        self.assertEqual(28, result['after_to_before']['sample_count'])
        self.assertEqual({'Topstitch': True}, result['non_target_groups'])
        self.assertTrue(result['work']['all_work_within_budget'])
        for key in ('continuous_hausdorff_verified', 'solver_rest_verified', 'native_owner_binding_verified',
                    'target_uv_preservation_verified', 'full_cut_preservation_verified'):
            self.assertFalse(result[key])

    def test_reversed_face_winding_rejects_even_with_identical_surface_points(self):
        flipped = {name: [tuple(reversed(t)) for t in triangles] if name != 'Topstitch' else triangles
                   for name, triangles in NEW.items()}
        result = compare(new=flipped)
        self.assertFalse(result['verified'])
        self.assertTrue(result['checks']['bidirectional_sampled_distance_verified'])
        self.assertFalse(result['checks']['centroid_orientation_preserved'])
        self.assertLess(result['after_to_before']['orientation_failure_witness']['normal_dot'], 0.)

    def test_missing_surface_rejects_with_both_child_labels_present(self):
        partial = copy.deepcopy(NEW)
        partial['child_right'].pop()
        result = compare(new=partial)
        self.assertEqual('FAILED', result['status'])
        self.assertFalse(result['checks']['target_area_preserved'])
        self.assertFalse(result['checks']['target_union_edge_connectivity_preserved'])
        # This particular missing cell lies between the finite source samples;
        # area/connectivity must remain independent hard gates, not diagnostics.

    def test_unchanged_target_does_not_hide_moved_non_target_group(self):
        changed = copy.deepcopy(NEW)
        changed['Topstitch'] = [tuple((x, y, z+1.) for x, y, z in t) for t in OTHER]
        result = compare(new=changed)
        self.assertTrue(result['checks']['bidirectional_sampled_distance_verified'])
        self.assertFalse(result['checks']['non_target_exported_arrays_exact'])
        self.assertFalse(result['verified'])

    def test_folded_surface_kept_or_flattened_and_new_bridge_interior(self):
        a, b, c = (0., 0., 0.), (1., 0., 1.), (2., 0., 0.)
        d, e, f = (0., 2., 0.), (1., 2., 1.), (2., 2., 0.)
        left, right = [(a, b, e), (a, e, d)], [(b, c, f), (b, f, e)]
        old = {'front': left+right, 'Topstitch': OTHER}
        preserved = {'child_left': left, 'child_right': right, 'Topstitch': OTHER}
        self.assertTrue(compare(old, preserved)['verified'])
        flattened = {name: [tuple((x, y, 0.) for x, y, z in t) for t in triangles]
                     if name != 'Topstitch' else triangles for name, triangles in preserved.items()}
        self.assertFalse(compare(old, flattened)['checks']['bidirectional_sampled_distance_verified'])
        # Every new vertex is already on the old folded surface. New triangle
        # edges/interiors bridge across the fold: vertex-only comparison misses it.
        bridge = {'child_left': [(a, c, f)], 'child_right': [(a, f, d)], 'Topstitch': OTHER}
        result = compare(old, bridge)
        witness = result['after_to_before']['max_sample_witness']
        self.assertFalse(result['verified'])
        self.assertGreater(witness['distance_mm'], .1)
        self.assertIn(witness['sample_kind'], ('edge_midpoint', 'centroid'))

    def test_work_exhaustion_missing_group_ambiguous_group_and_stale_snapshot_reject(self):
        limited = compare(max_triangle_tests=1)
        self.assertEqual('UNSUPPORTED', limited['status'])
        self.assertEqual('SURFACE_WORK_BUDGET', limited['error']['code'])
        self.assertFalse(limited['verification_completed'])
        self.assertTrue(limited['work']['budget_exhausted'])
        self.assertLessEqual(limited['work']['triangle_tests'], 1)
        old, new = snapshot(OLD, 'before'), snapshot(NEW, 'after')
        missing = verify_cut_surface(old, new, ['front'], ['not-present'])
        self.assertEqual('MISSING_TARGET_GROUP', missing['error']['code'])
        ambiguous = copy.deepcopy(new)
        ambiguous['face_labels'][0]['groups'].append('another')
        ambiguous['snapshot_id'] = mesh._hash({k:v for k,v in ambiguous.items() if k != 'snapshot_id'})
        self.assertEqual('AMBIGUOUS_OBJ_GROUPS', verify_cut_surface(old, ambiguous,
                         ['front'], ['child_left', 'child_right'])['error']['code'])
        incomplete = copy.deepcopy(new)
        incomplete['face_labels'].pop()
        incomplete['snapshot_id'] = mesh._hash({k:v for k,v in incomplete.items() if k != 'snapshot_id'})
        self.assertEqual('INVALID_MESH_SNAPSHOT', verify_cut_surface(old, incomplete,
                         ['front'], ['child_left', 'child_right'])['error']['code'])
        new['positions_mm'][0][2] += 1.
        self.assertEqual('INVALID_MESH_SNAPSHOT', verify_cut_surface(old, new,
                         ['front'], ['child_left', 'child_right'])['error']['code'])


if __name__ == '__main__':
    unittest.main()
