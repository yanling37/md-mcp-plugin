import copy
import sys
import types
import unittest
from unittest.mock import patch

from md_mcp_plugin.embedded import selection


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"pattern_uuid": "a", "index": 0, "information": {"name": "Same name"}},
                     {"pattern_uuid": "b", "index": 1, "information": {"name": "Same name"}}]
        self.selected = [True, False]
        self.calls = []
        def select(index, keep):
            self.calls.append((index, keep))
            self.selected[:] = [i == index for i in range(2)]
        api = types.SimpleNamespace(GetSelectedPatternViaIndex=lambda i: self.selected[i],
             GetSelectedPattern=lambda: ["Same name" for x in self.selected if x], SelectPatternViaIndex=select)
        self.modules = patch.dict(sys.modules, {"pattern_api": api})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.snapshot = {"patterns": self.rows, "scene_token": "current"}
        self.current = patch.object(selection, "scene", side_effect=lambda: copy.deepcopy(self.snapshot))
        self.current.start()
        self.addCleanup(self.current.stop)

    def test_duplicate_names_still_resolve_uuid_and_verify_each_flag(self):
        reply = selection.select_pattern({"pattern_uuid": "b"})
        self.assertEqual(self.calls, [(1, False)])
        self.assertEqual(reply["status"], "SUCCEEDED")
        self.assertEqual([r["pattern_uuid"] for r in reply["selected"]], ["b"])

    def test_stale_owner_is_rejected_without_sdk_selection(self):
        with self.assertRaises(Exception) as caught:
            selection.select_pattern({"pattern_uuid": "old-project"})
        self.assertFalse(caught.exception.details["effects_started"])
        self.assertEqual(self.calls, [])

    def test_already_selected_is_noop_and_wrong_flags_are_partial(self):
        reply = selection.select_pattern({"pattern_uuid": "a"})
        self.assertEqual(reply["status"], "NO_OP")
        self.assertEqual(self.calls, [])
        with patch.object(sys.modules["pattern_api"], "SelectPatternViaIndex", return_value=None):
            bad = selection.select_pattern({"pattern_uuid": "b"})
        self.assertEqual(bad["status"], "PARTIAL")
        self.assertTrue(bad["requires_recovery"])


if __name__ == "__main__":
    unittest.main()
