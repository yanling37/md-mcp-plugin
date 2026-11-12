"""Offline binding and offset compensation on a differently named two-pattern scene."""

import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import mesh, world_assigned_topstitch as stitch


OWNER = 'Owner4'
PARTNER = 'Other5'
OWNER_NAME = 'coat_front'
PARTNER_NAME = 'collar_back'
LABEL = 'DecorSeam_72'
STYLE = 'CreamThread'
TOKEN = 'a' * 64


def capture(current, name, *, offset=0., partner_offset=0., shared=False):
    groups = ((PARTNER_NAME, 0., partner_offset), (OWNER_NAME, 10., 0.), (LABEL, 20., offset))
    obj = []
    for _, x, z in groups:
        obj.extend((f'v {x} 0 {z}', f'v {x + 1} 0 {z}', f'v {x} 1 {z}', 'vt 0 0', 'vt 1 0', 'vt 0 1', 'vn 0 0 1'))
    for i, (label, _, _) in enumerate(groups):
        first = i * 3 + 1
        indices = [1 if shared and i == 2 else first, first + 1, first + 2]
        obj.extend((f'g {label}', 'f ' + ' '.join(f'{index}/{first + j}/{i + 1}' for j, index in enumerate(indices))))
    arrays = mesh.parse_obj('\n'.join(obj).encode())
    result = dict(arrays, schema='md-exported-obj-mesh-v1', scope='scene_garment_OBJ_export',
                  pattern_uuid=None, scene_token=TOKEN, scene_identity=current['identity'], capture_id=name,
                  space='world', unit='mm', axis_order=['X', 'Y', 'Z'], group_inventory=mesh.group_inventory(arrays),
                  export={'obj_asset': {'sha256': name}})
    result['snapshot_id'] = mesh._hash(result)
    return result


class AssignedTopstitchTests(unittest.TestCase):
    def setUp(self):
        self.current = {'scene_token': TOKEN, 'identity': {'pid': 17, 'project_path': 'D:/sample/coat.zprj'},
                        'patterns': [{'index': i, 'pattern_uuid': uuid, 'information': {'uuid': uuid, 'name': name}}
                                     for i, (uuid, name) in enumerate(((PARTNER, PARTNER_NAME), (OWNER, OWNER_NAME)))]}
        self.start = capture(self.current, 'start')
        self.sdk_rows = {0: [], 1: [{'TopstitchName': LABEL}, {'Pattern Topstitch Index': '4'}]}
        self.style_catalog = [{'TopstitchStyleIndex': '3', 'TopstitchStyleName': STYLE}]
        self.model_type = 1
        self.offset = self.original = struct.unpack('<f', struct.pack('<f', .13))[0]
        self.writes = []
        self.temporary_return = None

        def get_offset(index, assigned):
            self.assertEqual((index, assigned), (1, 4))
            return self.offset

        def set_offset(index, assigned, value):
            self.assertEqual((index, assigned), (1, 4))
            self.writes.append(value)
            self.offset = struct.unpack('<f', struct.pack('<f', value))[0]
            return self.temporary_return if value != self.original else None

        get_offset.__doc__ = 'GetPatternAssignedTopstitchZOffset(arg0: int, arg1: int) -> float'
        set_offset.__doc__ = 'SetPatternAssignedTopstitchZOffset(arg0: int, arg1: int, arg2: float) -> None'
        self.api = SimpleNamespace(
            GetPatternAssignedTopstitchCount=lambda i: len(self.sdk_rows[i]) // 2,
            GetPatternAssignedTopstitch=lambda i: self.sdk_rows[i],
            GetPatternAssignedTopstitchStyleIndex=lambda *_: 3,
            GetTopstitchStyleModelType=lambda _: self.model_type,
            GetTopstitchStyleList=lambda: self.style_catalog,
            GetPatternAssignedTopstitchStyle=lambda _: [{'TopstitchStyleName': STYLE}, {'TopstitchStyleCount': '1'}],
            GetPatternAssignedTopstitchZOffset=get_offset, SetPatternAssignedTopstitchZOffset=set_offset)
        self.enterContext(patch.object(stitch, 'check_scene_token', return_value=self.current))
        self.enterContext(patch.object(stitch, '_scope', return_value={'idle': True}))

    def binding(self, snapshot=None):
        return stitch.bind(self.current, OWNER, snapshot or self.start, api=self.api)

    def run_offset(self, snapshots):
        with patch.object(stitch.world_adapter, '_mesh_read', side_effect=snapshots):
            return stitch.offset_roundtrip(self.current, self.binding(), self.start, api=self.api)

    def test_current_uuid_binds_unique_texture_group_and_explicit_zero_is_none(self):
        binding = self.binding()
        self.assertEqual((binding['pattern_index'], binding['assigned_index'], binding['exported_group']), (1, 4, LABEL))
        self.assertEqual((binding['style_index'], binding['style_name'], binding['style_model_type']), (3, STYLE, 1))
        self.assertIsNone(stitch.bind(self.current, PARTNER, self.start, api=self.api))

    def test_assignment_ambiguity_and_non_texture_style_are_rejected(self):
        self.sdk_rows[0] = [{'TopstitchName': LABEL}, {'Pattern Topstitch Index': '0'}]
        with self.assertRaisesRegex(ValueError, 'uniquely named'):
            self.binding()
        self.sdk_rows[0] = []
        self.model_type = 0
        with self.assertRaisesRegex(ValueError, 'must be Texture'):
            self.binding()
        self.model_type = 1
        self.style_catalog.append(dict(self.style_catalog[0]))
        with self.assertRaisesRegex(ValueError, 'style index is ambiguous'):
            self.binding()

    def test_assigned_group_must_not_share_vertices_with_another_group(self):
        with self.assertRaisesRegex(ValueError, 'shares exported positions'):
            self.binding(capture(self.current, 'shared', shared=True))

    def test_closed_roundtrip_returns_fresh_obj_and_exact_float32_offset(self):
        restored = capture(self.current, 'restored', offset=.01)
        result = self.run_offset([capture(self.current, 'fresh'), capture(self.current, 'temporary', offset=.1), restored])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertIs(result['snapshot'], restored)
        self.assertEqual(self.writes, [self.original + .1, self.original])
        self.assertEqual(self.offset, self.original)
        self.assertTrue(result['result']['positive_control_verified'])

    def test_unknown_setter_response_is_compensated_but_remains_unknown(self):
        self.temporary_return = False
        result = self.run_offset([capture(self.current, 'fresh'), capture(self.current, 'restored')])
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertTrue(result['requires_recovery'])
        self.assertIsNone(result['snapshot'])
        self.assertEqual(self.writes, [self.original + .1, self.original])
        self.assertEqual(self.offset, self.original)

    def test_drift_in_partner_group_prevents_closed_geometry_receipt(self):
        result = self.run_offset([capture(self.current, 'fresh'), capture(self.current, 'temporary', offset=.1),
                                  capture(self.current, 'restored', partner_offset=.01)])
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertIsNone(result['snapshot'])
        self.assertIn('outside the assigned group', result['error']['message'])


if __name__ == '__main__':
    unittest.main()
