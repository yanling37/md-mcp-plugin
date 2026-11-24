import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from md_mcp_plugin.embedded import scene_adapter_v6 as adapter


class ReleasedSceneAdapterTests(unittest.TestCase):
    def setUp(self):
        self.process = {"pid": 22, "creation_filetime": 1400}
        self.old_config = {"path": "old", "sha256": "1" * 64, "artifact_root": "cases"}
        self.new_config = {"path": "new", "sha256": "2" * 64, "artifact_root": "cases"}
        self.old = {"configuration": self.old_config, "blocked": True, "loaded_process": self.process,
                    "library": SimpleNamespace(_handle=10, path="old"), "call": ctypes.c_void_p(100)}
        self.inspection = {"status": "SUCCEEDED", "effects_started": False, "result": {
            "after": {"view": "2d", "scene_item_grabber_absent": True, "mapping_fingerprint": "a" * 64,
                      "selector_chain": ["2D"], "viewport": {"name": "preview"}, "scene": {"name": "paper"},
                      "scene_rect": [0, 0, 25, 25]},
            "physical_after": {"background_safe": True}, "tools_after": {"current_tool": "edit_curve_point"},
            "selection_after": {"selected_items_count": 1},
        }}
        self.library = SimpleNamespace(_handle=11, path="new", mdqt_scene_adapter_recovery_epoch=Mock(return_value=1))

    def prepare(self):
        def load(environment, candidate):
            candidate.update(library=self.library, call=ctypes.c_void_p(101), loaded_process=environment['process'])
        with (patch.object(adapter, '_state', return_value=self.old),
              patch.object(adapter, '_environment', return_value={'process': self.process}),
              patch.object(adapter, '_configuration', return_value=self.new_config),
              patch.object(adapter, '_loaded_library_path', side_effect=lambda library: library.path),
              patch.object(adapter, '_load', side_effect=load),
              patch.object(adapter, '_invoke', return_value=self.inspection) as invoke):
            prepared = adapter.prepare_closed_drag_successor(self.new_config)
        return prepared, invoke

    def test_distinct_successor_requires_read_only_proof_before_routing_changes(self):
        (old, new, evidence), invoke = self.prepare()
        self.assertIs(old, self.old)
        self.assertTrue(self.old['blocked'])
        self.assertTrue(evidence['successor_bridge_verified'])
        self.assertEqual([c.args[0]['operation'] for c in invoke.call_args_list], ['inspect', 'inspect'])
        with patch.object(adapter, '_state', return_value=old), patch.object(adapter.sys, adapter.STATE_KEY, old, create=True):
            adapter.adopt_closed_drag_successor(old, new)
            self.assertIs(getattr(adapter.sys, adapter.STATE_KEY), new)
            self.assertEqual(new['retired'], (old['library'],))
            self.assertTrue(old['blocked'])

    def test_rejects_a_successor_resolving_to_the_predecessor(self):
        self.library._handle = self.old['library']._handle
        with self.assertRaisesRegex(adapter.AdapterError, 'distinct pinned native module'):
            self.prepare()
        self.assertTrue(self.old['blocked'])

    def test_rejects_any_current_scene_grab(self):
        self.inspection['result']['after']['scene_item_grabber_absent'] = False
        with self.assertRaisesRegex(adapter.AdapterError, 'released input'):
            self.prepare()

    def test_uncommitted_bridge_cannot_send_mutations(self):
        with self.assertRaisesRegex(adapter.AdapterError, 'can only inspect'):
            adapter._invoke({'operation': 'bezier_drag'}, inspection_state={})

    def test_public_configuration_reuses_the_verified_successor(self):
        successor = {'configuration': self.new_config, 'library': self.library, 'blocked': False}
        with (patch.object(adapter, '_state', return_value=successor),
              patch.object(adapter, '_configuration', return_value=self.new_config)):
            result = adapter.install_native_bridge(**self.new_config)
        self.assertTrue(result['configured'] and result['loaded'] and result['capture_root_configured'])
