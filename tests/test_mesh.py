"""Offline actual-file export/OBJ/reference contracts; SDK boundary is mocked."""
import copy
from enum import IntEnum
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
mesh = importlib.import_module('md_mcp_plugin.embedded.mesh')
feature = importlib.import_module('md_mcp_plugin.features.mesh')

OBJ = b'''# actual file parser fixture
mtllib mesh.mtl
o opaque-export-object
g unrelated-to-runtime-UUID
v 1 2 3
v 11 2 3
v 1 12 3
vt 0 0
vt 1 0
vt 0 1
vn 0 0 1
usemtl material-A
s 1
f 1/1/1 2/2/1 3/3/1
'''


def current_scene():
    return {'identity': {'project_path': 'current-copy.zprj', 'pid': 123},
            'patterns': [{'pattern_uuid': 'uuid-A', 'index': 0, 'information': {'name': 'paper-A'}},
                         {'pattern_uuid': 'uuid-B', 'index': 1, 'information': {'name': 'paper-B'}}],
            'scene_token': 'scene-token', 'fingerprint_scope': 'mock current input metadata'}


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.guard_receipt = {'schema': 'md-obj-export-activation-guard/1', 'applicable': True, 'detached': True}
        self.guard = self.enterContext(patch.object(mesh, 'guarded_obj_export',
            side_effect=lambda function, path, option, pid: (function(path, option), self.guard_receipt)))
        self.actual_mkdtemp = tempfile.mkdtemp
        self.enterContext(patch.object(mesh.tempfile, 'mkdtemp', lambda **kw: self.actual_mkdtemp(dir=self.temp.name, **kw)))
        self.enterContext(patch.object(mesh, 'scene', side_effect=lambda: copy.deepcopy(current_scene())))
        enum = IntEnum('WELD_TYPE', {'FULLY_UNWELDED': 0, 'FULLY_WELDED': 2})

        class Options:
            def __init__(self):
                self.__dict__.update(copy.deepcopy(mesh._OPTIONS))
                self.__dict__.update(copy.deepcopy(mesh._OPTIONAL_OPTIONS))
                self.patternList = []
                self.weldType = enum.FULLY_WELDED

        self.Options = Options
        self.api_types = SimpleNamespace(ImportExportOption=Options, WELD_TYPE=enum)

        def export(path, option):
            self.calls.append((path, copy.deepcopy(option.__dict__)))
            # The explicit SDK filter selects the fixture payload, never an OBJ label.
            payload = OBJ.replace(b'v 1 2 3', b'v 201 2 3') if option.__dict__.get('patternList') == [1] else OBJ
            target = Path(path)
            target.write_bytes(payload)
            target.with_suffix('.mtl').write_text('newmtl material-A\nKd 1 1 1\n')
            return [path, str(target.with_suffix('.mtl'))]

        self.export = Mock(side_effect=export)
        self.enterContext(patch.dict(sys.modules, {'ApiTypes': self.api_types, 'export_api': SimpleNamespace(ExportOBJW=self.export)}))

    def test_parser_preserves_independent_uv_normals_and_negative_indices(self):
        parsed = mesh.parse_obj(OBJ.replace(b'f 1/1/1 2/2/1 3/3/1', b'f -3/-3/-1 -2/-2/-1 -1/-1/-1'))
        self.assertEqual(parsed['faces'], [[0, 1, 2]])
        self.assertEqual(parsed['face_uv_indices'], [[0, 1, 2]])
        self.assertEqual(parsed['face_normal_indices'], [[0, 0, 0]])
        self.assertEqual(parsed['face_labels'][0]['groups'], ['unrelated-to-runtime-UUID'])
        self.assertEqual(parsed['positions_mm'][0], [1, 2, 3])
        self.assertFalse(parsed['native_vertex_mapping_verified'])
        changed = mesh.parse_obj(OBJ.replace(b'2/2/1', b'2/3/1'))
        self.assertNotEqual(parsed['topology_fingerprint'], changed['topology_fingerprint'])

    def test_invalid_obj_is_rejected_instead_of_truncated_or_guessed(self):
        bad = [OBJ.replace(b'v 1 2 3', b'v nan 2 3'), OBJ.replace(b'v 1 2 3', b'v 1 2 3 1'),
               OBJ.replace(b'3/3/1', b'0/3/1'), OBJ.replace(b'3/3/1', b'8/3/1'),
               OBJ.replace(b'3/3/1', b'2/3/1'), OBJ + b'l 1 2\n', OBJ + b'unknown 1\n', b'v 0 0 0\n']
        for payload in bad:
            with self.subTest(payload=payload[-50:]), self.assertRaises(ValueError):
                mesh.parse_obj(payload)

    def test_uuid_option_readback_does_not_certify_an_owner_snapshot(self):
        result = mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-B', 'vertex_indices': [0, 2]})
        self.assertEqual(result['status'], 'UNSUPPORTED')
        self.assertNotIn('snapshot', result)
        self.assertEqual(result['vertex_references'], [])
        snapshot = result['export_diagnostic_snapshot']
        self.assertEqual(snapshot['positions_mm'][0], [201, 2, 3])
        self.assertIsNone(snapshot['pattern_uuid'])
        self.assertEqual(snapshot['scope'], 'unverified_native_filter_OBJ_export')
        self.assertFalse(snapshot['owner_binding']['group_names_used_as_owner_mapping'])
        self.assertFalse(result['filter_output_check']['native_pattern_filter_verified'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]['patternList'], [1])
        self.assertIs(self.calls[0][1]['bExportAvatar'], False)
        self.assertEqual(self.calls[0][1]['scale'], 1.0)

    def test_scene_snapshot_references_are_capture_bound(self):
        result = mesh.OPERATIONS['mesh.read_scene']({'vertex_indices': [0, 2]})
        snapshot = result['snapshot']
        self.assertEqual(mesh.resolve_snapshot_vertex(snapshot, result['vertex_references'][0]), 0)
        newer = mesh.OPERATIONS['mesh.read_scene']({})['snapshot']
        self.assertEqual(snapshot['topology_fingerprint'], newer['topology_fingerprint'])
        self.assertNotEqual(snapshot['snapshot_id'], newer['snapshot_id'])
        with self.assertRaises(ReferenceError):
            mesh.resolve_snapshot_vertex(newer, result['vertex_references'][0])
        altered = copy.deepcopy(snapshot); altered['positions_mm'][0][0] += 1
        with self.assertRaises(ReferenceError):
            mesh.vertex_reference(altered, 0)
        with self.assertRaises(ValueError):
            mesh.vertex_reference(snapshot, True)

    def test_scene_summary_matches_full_geometry_and_preserves_the_obj(self):
        operation = mesh.OPERATIONS['mesh.read_scene']
        full = operation({})['snapshot']
        receipt = operation({'summary_only': True})
        compact = receipt['summary']
        self.assertEqual(compact['geometry_sha256'], mesh._hash({key: full[key] for key in mesh._GEOMETRY_FIELDS}))
        self.assertEqual(compact['topology_fingerprint'], full['topology_fingerprint'])
        self.assertEqual(compact['export']['obj_asset']['sha256'], full['export']['obj_asset']['sha256'])
        self.assertEqual(compact['export']['activation_guard'], self.guard_receipt)
        self.assertEqual(full['export']['activation_guard'], self.guard_receipt)
        self.assertEqual([call.args[3] for call in self.guard.call_args_list], [123, 123])
        self.assertTrue(compact['scene_before_after_exact'])
        self.assertNotIn('snapshot', receipt)
        self.assertFalse(receipt['vertex_references'])

    def test_scene_summary_rejects_reference_indices_before_export(self):
        with self.assertRaisesRegex(ValueError, 'summary_only'):
            mesh.OPERATIONS['mesh.read_scene']({'summary_only': True, 'vertex_indices': [0]})
        self.export.assert_not_called()

    def test_missing_owner_filter_is_unsupported_but_scene_scope_is_explicit(self):
        original = self.Options

        def no_filter():
            value = original(); del value.patternList
            return value

        self.api_types.ImportExportOption = no_filter
        result = mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        self.assertEqual(result['status'], 'UNSUPPORTED')
        self.assertEqual(result['error']['code'], 'OWNER_FILTER_UNAVAILABLE')
        self.export.assert_not_called()
        scene = mesh.OPERATIONS['mesh.read_scene']({})['snapshot']
        self.assertIsNone(scene['pattern_uuid'])
        self.assertEqual(scene['scope'], 'scene_garment_OBJ_export')
        self.assertEqual(scene['owner_binding']['basis'], 'no_verified_export_face_to_UUID_binding')
        self.assertFalse(scene['native_vertex_mapping_verified'])

    def ignored_filter_export(self, path, option):
        self.calls.append((path, copy.deepcopy(option.__dict__)))
        # Real regression: patternList accepts either index but emits all pieces
        # and an accessory. Each group is explicit; no group name is a UUID.
        payload = OBJ.replace(b'g unrelated-to-runtime-UUID', b'g paper-A')
        payload += b'v 100 0 0\nv 110 0 0\nv 100 10 0\nvt .2 .3\nvt .4 .5\nvt .6 .7\nvn 0 1 0\n'
        payload += b'g paper-B\nf 4/6/2 5/4/2 6/5/2\ng Topstitch\nf 1/1/1 4/4/1 6/6/1\n'
        Path(path).write_bytes(payload)
        return [path]

    def test_ignored_filter_with_foreign_piece_and_topstitch_is_unsupported(self):
        self.export.side_effect = self.ignored_filter_export
        for owner in ('uuid-A', 'uuid-B'):
            result = mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': owner})
            self.assertEqual(result['status'], 'UNSUPPORTED')
            self.assertEqual(result['filter_output_check']['other_current_pattern_group_face_count'], 1)
            self.assertEqual(result['filter_output_check']['unknown_or_ungrouped_face_count'], 1)
            self.assertEqual(result['export_diagnostic_snapshot']['vertex_count'], 6)
            self.assertEqual(result['vertex_references'], [])
        self.assertEqual(len(self.calls), 2)  # One call per request, no retries.

    def test_exact_group_compacts_all_arrays_and_preserves_complete_index_maps(self):
        self.export.side_effect = self.ignored_filter_export
        result = mesh.OPERATIONS['mesh.read_group']({'group_name': 'paper-B', 'vertex_indices': [0, 2]})
        self.assertEqual(result['status'], 'SUCCEEDED')
        snapshot = result['snapshot']; maps = snapshot['group_index_maps']
        self.assertIsNone(snapshot['pattern_uuid'])
        self.assertEqual(snapshot['scope'], 'exact_OBJ_group_label')
        self.assertEqual(snapshot['positions_mm'], [[100, 0, 0], [110, 0, 0], [100, 10, 0]])
        self.assertEqual(snapshot['faces'], [[0, 1, 2]])
        self.assertEqual(snapshot['face_uv_indices'], [[2, 0, 1]])
        self.assertEqual(snapshot['face_normal_indices'], [[0, 0, 0]])
        self.assertEqual(maps['positions_mm'], {'compact_to_export': [3, 4, 5], 'export_to_compact': [None, None, None, 0, 1, 2]})
        self.assertEqual(maps['uv']['compact_to_export'], [3, 4, 5])
        self.assertEqual(maps['normals'], {'compact_to_export': [1], 'export_to_compact': [None, 0]})
        self.assertEqual(maps['faces'], {'compact_to_export': [1], 'export_to_compact': [None, 0, None]})
        self.assertTrue(all(row['groups'] == ['paper-B'] for row in snapshot['face_labels']))
        self.assertEqual(set(snapshot['source_group_inventory']['groups']), {'paper-A', 'paper-B', 'Topstitch'})
        ref = result['vertex_references'][0]
        self.assertEqual(ref['group_name'], 'paper-B')
        self.assertEqual(ref['export_vertex_index'], 3)
        self.assertEqual(mesh.resolve_snapshot_vertex(snapshot, ref), 0)
        with self.assertRaises(ReferenceError):
            mesh.resolve_snapshot_vertex(snapshot, dict(ref, group_name='paper-A'))

    def test_multigroup_membership_and_missing_exact_label_are_rejected(self):
        parsed = mesh.parse_obj(OBJ)
        with self.assertRaisesRegex(Exception, 'absent'):
            mesh.extract_group(parsed, 'not-present')
        parsed['face_labels'][0]['groups'].append('overlapping-group')
        with self.assertRaisesRegex(Exception, 'multiple OBJ groups'):
            mesh.extract_group(parsed, 'unrelated-to-runtime-UUID')

    def test_wrong_option_type_or_readback_prevents_export(self):
        original = self.Options

        def wrong_type():
            value = original(); value.axisX = False
            return value

        self.api_types.ImportExportOption = wrong_type
        result = mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        self.assertEqual(result['status'], 'UNSUPPORTED')
        self.export.assert_not_called()

    def test_scene_changes_before_and_after_export_are_rejected(self):
        before = current_scene(); changed = copy.deepcopy(before); changed['scene_token'] = 'changed'
        with patch.object(mesh, 'scene', side_effect=[before, changed]), self.assertRaisesRegex(Exception, 'changed before'):
            mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        self.export.assert_not_called()
        with patch.object(mesh, 'scene', side_effect=[before, before, changed]), self.assertRaisesRegex(Exception, 'changed while'):
            mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        self.assertEqual(self.export.call_count, 1)

    def test_absent_or_duplicate_uuid_never_exports(self):
        with self.assertRaisesRegex(Exception, 'absent'):
            mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'missing'})
        duplicate = current_scene(); duplicate['patterns'][1]['pattern_uuid'] = 'uuid-A'
        with patch.object(mesh, 'scene', return_value=duplicate), self.assertRaisesRegex(Exception, 'not unique'):
            mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        self.export.assert_not_called()

    def test_export_return_and_out_of_range_references_are_not_success(self):
        self.export.side_effect = lambda path, option: False
        with self.assertRaisesRegex(Exception, 'documented nonempty list'):
            mesh.OPERATIONS['mesh.read_pattern']({'pattern_uuid': 'uuid-A'})
        with self.assertRaises(ValueError):
            mesh.OPERATIONS['mesh.read_scene']({'vertex_indices': [True]})

    def test_probe_reads_only_fixed_metadata(self):
        result = mesh.OPERATIONS['mesh.probe_runtime']({})
        self.assertTrue(result['metadata_only'])
        self.assertTrue(result['fields']['patternList']['present'])
        self.export.assert_not_called()

    def test_host_routes_through_core_without_mutation_claim(self):
        tools = {}
        mcp = SimpleNamespace(tool=lambda: lambda function: tools.setdefault(function.__name__, function))
        context = SimpleNamespace(call=Mock(return_value={'status': 'SUCCEEDED'}))
        feature.register(mcp, context)
        tools['mesh_read_pattern']('uuid-B', [1], 'fresh-token')
        context.call.assert_called_once_with('mesh.read_pattern', {'pattern_uuid': 'uuid-B', 'vertex_indices': [1]}, scene_token='fresh-token')
        context.call.reset_mock()
        tools['mesh_read_group']('paper-B', [1], 'fresh-token')
        context.call.assert_called_once_with('mesh.read_group', {'group_name': 'paper-B', 'vertex_indices': [1]}, scene_token='fresh-token')
        self.assertEqual(feature.capabilities()['native_vertex_mapping']['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
