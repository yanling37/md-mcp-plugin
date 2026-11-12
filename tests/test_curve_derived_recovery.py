import unittest

from md_mcp_plugin.embedded.project import _closed_derived_sewing_failure


class DerivedSewingRollbackTests(unittest.TestCase):
    def setUp(self):
        self.previous = {'status': 'PARTIAL', 'effects_started': True, 'requires_recovery': True}
        self.derived = {
            'verified': False, 'reason': 'Physical sewing endpoint moved beyond tolerance',
            'sewing_endpoint_max_error_mm': 1.48,
            'normalized_paths': [
                ['PatternList', 0, 'InternalLineList', 0, 'LineList', 4, 'PointList', 1, 'Position'],
                ['SeamLinePairGroupList', 0, 'PairList', 0, 'Second', 'LengthParam', 'fStart'],
            ],
        }
        self.payload = {'write_attempted': True, 'automatic_retry_allowed': False, 'verification': {
            'scene_identity_preserved': True, 'handle_identity_preserved': True,
            'derived_native_data_verification': self.derived,
        }}
        self.provider = {'status': 'PARTIAL', 'error': {'code': 'NON_TARGET_NATIVE_CHANGE'},
                         'geometry_effects_started': True}
        self.batch = {
            'status': 'SUCCEEDED', 'requires_recovery': False, 'paired_release_verified': True,
            'events': [{'stage': stage, 'sent': True, 'returned': True, 'buttons': int(stage in ('press', 'move'))}
                       for stage in ('hover', 'press', 'move', 'release')],
        }

    def allowed(self):
        return _closed_derived_sewing_failure(self.previous, self.payload, self.provider, [self.batch])

    def test_closed_derived_sewing_change_may_roll_back(self):
        self.assertTrue(self.allowed())

    def test_other_native_change_refuses_recovery(self):
        self.derived['normalized_paths'].append(['PatternList', 0, 'Thickness'])
        self.assertFalse(self.allowed())

    def test_unconfirmed_release_refuses_recovery(self):
        self.batch['events'][-1]['returned'] = False
        self.assertFalse(self.allowed())
