"""Offline OBJ, calibrated geometry and actual synthetic PNG checks."""
import copy
import hashlib
from pathlib import Path
import struct
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from md_mcp_plugin.embedded import mesh
from md_mcp_plugin.embedded.cloth_vertex_mapping import (
    VertexMappingError, match_visible_vertex, measure_exported_vertex_effect, plan_vertex_drag, verify_meshbox_vertex_marker,
)

CAMERA = dict(right=[1, 0, 0], up=[0, 1, 0], forward=[0, 0, 1], camera_center_world_mm=[0, 0, 10],
              center_px=[128, 128], focal_px=200, viewport_size_px=[256, 256])


def current_export(*, competing=False, target=(0, 0, 0), non_target=(4, 0, 0)):
    other = (0., 0., 2.) if competing else (6., 0., 0.)
    vertices = [target, non_target, (0., 4., 0.), other, (6., 4., 0.), (7., 0., 0.)]
    obj = ('\n'.join('v '+' '.join(map(str, p)) for p in vertices) +
           '\ng Alpha\nf 1 2 3\ng Beta\nf 4 5 6\n').encode()
    actual = mesh.parse_obj(obj)
    identity = {'pid': 456, 'project_path': 'current-copy.zprj'}
    scene = dict(identity=identity, scene_token='test-scene', patterns=[
        dict(index=i, pattern_uuid=uuid, information=dict(uuid=uuid, name=label))
        for i, (uuid, label) in enumerate((('paper-a', 'Alpha'), ('paper-b', 'Beta')))])
    actual.update(schema='md-exported-obj-mesh-v1', scope='scene_garment_OBJ_export', pattern_uuid=None,
                  scene_identity=identity, scene_token=scene['scene_token'], space='world', unit='mm',
                  axis_order=['X', 'Y', 'Z'], capture_id='fixture-capture', group_inventory=mesh.group_inventory(actual),
                  export={'obj_asset': {'path': 'fixture.obj', 'sha256': hashlib.sha256(obj).hexdigest()}})
    actual['snapshot_id'] = mesh._hash(actual)
    return scene, actual


def png(*, handle=True, blue=(), overlay=None):
    size = 256
    image = bytearray(bytes([80, 80, 80, 255])*size*size)
    if handle:
        for y in range(126, 135):
            for x in range(127, 136):
                i = (y*size+x)*4
                image[i:i+4] = bytes([12, 230, 15, 255])
    if overlay:
        left, top, right, bottom = overlay
        for y in range(top, bottom):
            for x in range(left, right):
                i = (y*size+x)*4
                image[i:i+4] = bytes([80, 202, 80, 255])
    for cx, cy in blue:
        for y in range(cy-2, cy+3):
            for x in range(cx-2, cx+3):
                i = (y*size+x)*4
                image[i:i+4] = bytes([35, 125, 220, 255])
    def chunk(tag, data):
        return struct.pack('>I', len(data))+tag+data+struct.pack('>I', zlib.crc32(tag+data)&0xffffffff)
    raw = b''.join(b'\0'+image[y*size*4:(y+1)*size*4] for y in range(size))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))+chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b'')


class VertexMappingTests(unittest.TestCase):
    def test_single_new_meshbox_marker_requires_current_png_scope_and_no_competing_mark(self):
        view = dict(view_ref='current', view_fingerprint='scope',
                    mapping=dict(device_pixel_ratio=1, viewport_size=[256, 256], start_drag_distance=10))
        plan = dict(schema='md-meshbox-projected-rectangle/1', candidate_available=True, device_pixel_ratio=1,
                    viewport_size_logical_px=[256, 256], start_drag_distance=10,
                    target_uncertainty_bounds_logical_px=[127, 127, 130, 130], box_start_logical_px=[122, 122],
                    box_end_logical_px=[134, 134], target_pattern_uuid='paper-a', target_vertex_index=7)

        def frame(data):
            return dict(view=view, scene_token='current-scene', image=dict(width=256, height=256, device_pixel_ratio=1,
                        viewport_size=[256, 256], byte_count=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        view_ref='current', view_fingerprint='scope'))

        before = png(handle=False)
        after = png(handle=False, blue=((128, 128),))
        proof = verify_meshbox_vertex_marker(frame(before), frame(after), before, after, plan)
        self.assertTrue(proof['verified'])
        self.assertEqual([128.5, 128.5], proof['marker']['centroid_logical_px'])
        self.assertFalse(proof['native_vertex_identity_verified'])
        local_overlay = png(handle=False, blue=((128, 128),), overlay=(94, 124, 133, 133))
        proof = verify_meshbox_vertex_marker(frame(before), frame(local_overlay), before, local_overlay, plan)
        self.assertTrue(proof['verified'])
        self.assertGreater(proof['selection_overlay_pixels_outside_local_margin'], 0)
        tangent_overlay = png(handle=False, blue=((128, 128),), overlay=(94, 124, 124, 132))
        proof = verify_meshbox_vertex_marker(frame(before), frame(tangent_overlay), before, tangent_overlay, plan)
        self.assertFalse(proof['verified'])
        far_overlay = png(handle=False, blue=((128, 128),), overlay=(70, 124, 133, 133))
        proof = verify_meshbox_vertex_marker(frame(before), frame(far_overlay), before, far_overlay, plan)
        self.assertFalse(proof['verified'])
        self.assertGreater(proof['unexpected_layout_pixels'], 0)
        competing = png(handle=False, blue=((128, 128), (180, 170)))
        proof = verify_meshbox_vertex_marker(frame(before), frame(competing), before, competing, plan)
        self.assertFalse(proof['verified'])
        self.assertEqual(50, proof['new_blue_pixels'])
        with self.assertRaises(VertexMappingError):
            verify_meshbox_vertex_marker(frame(before), frame(competing), before, after, plan)

    def test_green_handle_binds_unique_obj_candidate_and_world_delta_plan(self):
        scene, exported = current_export()
        plan = plan_vertex_drag(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, png(), [.1, -.2, 0])
        observation = plan['observation']
        self.assertEqual(0, observation['export_vertex_index'])
        self.assertEqual([128., 128.], observation['projected_vertex_px'])
        self.assertEqual([131., 130.], observation['click_anchor_px'])
        self.assertGreaterEqual(observation['joint_clearance_px'], 4)
        self.assertEqual([133., 134.], plan['viewport_points']['end'])
        self.assertTrue(plan['incident_triangle_orientation_path_verified'])
        self.assertFalse(plan['dispatch_qualified'])
        self.assertFalse(observation['native_selected_vertex_verified'])

    def test_competing_vertex_missing_marker_wrong_group_and_stale_pose_refuse(self):
        scene, exported = current_export(competing=True)
        with self.assertRaises(VertexMappingError) as caught:
            match_visible_vertex(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, png())
        self.assertEqual('AMBIGUOUS_PROJECTED_VERTEX', caught.exception.code)
        scene, exported = current_export()
        with self.assertRaises(VertexMappingError) as caught:
            match_visible_vertex(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, png(handle=False))
        self.assertEqual('AMBIGUOUS_GREEN_HANDLE', caught.exception.code)
        with self.assertRaises(ValueError):
            match_visible_vertex(scene, exported, 'paper-a', 'Beta', 0, CAMERA, png())
        drifted = copy.deepcopy(exported)
        drifted['positions_mm'][0][0] += .01
        with self.assertRaises(ReferenceError):
            match_visible_vertex(scene, drifted, 'paper-a', 'Alpha', 0, CAMERA, png())

    def test_two_plane_stage_and_unsafe_incident_path(self):
        scene, exported = current_export()
        frame = png()
        with self.assertRaises(VertexMappingError) as caught:
            plan_vertex_drag(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, frame, [.1, -.2, .3])
        self.assertEqual('SECOND_CAMERA_REQUIRED', caught.exception.code)
        plan = plan_vertex_drag(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, frame, [.1, -.2, .3],
                                second_camera={'forward': [1, 0, 0]})
        self.assertEqual([.1, 0., 0.], plan['stage_delta_xyz_mm'])
        self.assertTrue(plan['measured_readback_required'])
        with self.assertRaises(VertexMappingError) as caught:
            plan_vertex_drag(scene, exported, 'paper-a', 'Alpha', 0, CAMERA, frame, [3, 2, 0])
        self.assertEqual('INCIDENT_TRIANGLE_PATH_UNQUALIFIED', caught.exception.code)

    def test_actual_complete_export_diff_identifies_only_target_and_rejects_wrong_effect(self):
        _, old = current_export()
        target = [.1, -.2, .3]
        _, after = current_export(target=target)
        proof = measure_exported_vertex_effect(old, after, 'Alpha', 0, target_xyz_mm=target)
        self.assertTrue(proof['observed_unique_expected_OBJ_vertex_change'])
        self.assertTrue(proof['other_exported_xyz_exact'])
        self.assertTrue(proof['target_xyz_reached'])
        self.assertFalse(proof['native_vertex_index_verified'])
        _, wrong = current_export(target=target, non_target=(4, 0, .01))
        proof = measure_exported_vertex_effect(old, wrong, 'Alpha', 0, target_xyz_mm=target)
        self.assertFalse(proof['observed_unique_expected_OBJ_vertex_change'])
        self.assertFalse(proof['other_exported_xyz_exact'])
        self.assertEqual([0, 1], proof['changed_export_indices'])


if __name__ == '__main__':
    unittest.main()
