"""Cut recovery host binding against temporary journals and a fake transport."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from md_mcp_plugin.config import Config
from md_mcp_plugin.context import Context

CUT = 'cut_qt.execute'
RECOVER = 'project.recover_cut'
PARENT = 'a' * 32
CHILD = 'c' * 32
SOURCE_HASH = 'e' * 64


def restored_receipt():
    return {
        'operation_id': CHILD, 'status': 'SUCCEEDED', 'requires_recovery': False,
        'effects_started': True, 'error': None,
        'result': {'verified': True, 'recovered_operation_id': PARENT,
                   'recovery_action': 'SAVED_PROJECT_RELOADED', 'source_file_unchanged': True,
                   'source_sha256': SOURCE_HASH},
    }


class FakeRecoveryTransport:
    def __init__(self, receipt):
        self.receipt = receipt
        self.operations = []
        self.before_recovery_return = None

    def execute(self, operation, params, **kwargs):
        self.operations.append(operation)
        if operation == CUT:
            return {'operation_id': PARENT, 'status': 'UNKNOWN', 'effects_started': True,
                    'requires_recovery': True, 'error': {'code': 'POST_CUT_READBACK_FAILED'},
                    'result': {'verified': False, 'adapter_response': {'status': 'PARTIAL', 'requires_recovery': True}}}
        if operation == RECOVER:
            if self.before_recovery_return is not None:
                self.before_recovery_return()
            return copy.deepcopy(self.receipt)
        if operation == 'runtime.receipt' and params == {'operation_id': CHILD}:
            return {'status': 'SUCCEEDED', 'result': {'found': True, 'receipt': copy.deepcopy(self.receipt)}}
        raise AssertionError(f'Unexpected transport operation {operation}')


class CutRecoveryHostTests(unittest.TestCase):
    def setup_recovery(self, directory, receipt):
        transport = FakeRecoveryTransport(receipt)
        context = Context(Config('127.0.0.1', 1, 1, Path(directory), ROOT / 'src'), transport)
        original = context.call(CUT, {}, mutation=True, request_id=PARENT, scene_token='b' * 64)
        self.assertEqual(original['status'], 'UNKNOWN')
        return context, transport, original

    def recover(self, context):
        return context.recover_cut_operation(PARENT, CHILD, 'd' * 64, SOURCE_HASH)

    def test_confirmed_reload_links_original_without_replacing_unknown_receipt(self):
        with tempfile.TemporaryDirectory(prefix='md-cut-recovery-host-') as directory:
            receipt = restored_receipt()
            context, transport, original = self.setup_recovery(directory, receipt)
            recovered = self.recover(context)
            self.assertEqual(recovered['status'], 'SUCCEEDED')
            self.assertEqual(recovered['receipt'], receipt)
            parent = context.journal.get(PARENT)
            self.assertEqual(parent.pop('recovery'), {'request_id': CHILD, 'status': 'SAVED_PROJECT_RELOADED',
                                                     'original_status': 'UNKNOWN'})
            self.assertEqual(parent, original)
            self.assertFalse(context.journal.lease.exists())
            self.assertEqual(self.recover(context), recovered)
            self.assertEqual(transport.operations, [CUT, RECOVER])

    def test_forged_or_incomplete_success_keeps_raw_child_receipt_and_parent_lock(self):
        variants = [('child_operation_id', 'operation_id', PARENT), ('requires_recovery_false', 'requires_recovery', True),
                    ('verified', 'result.verified', 1), ('recovered_operation_id', 'result.recovered_operation_id', 'f' * 32),
                    ('recovery_action', 'result.recovery_action', 'OTHER'), ('source_file_unchanged', 'result.source_file_unchanged', False),
                    ('source_sha256', 'result.source_sha256', '0' * 64)]
        for check, field, value in variants:
            with self.subTest(field=field), tempfile.TemporaryDirectory(prefix='md-cut-recovery-host-') as directory:
                receipt = restored_receipt()
                if field.startswith('result.'):
                    receipt['result'][field.split('.', 1)[1]] = value
                else:
                    receipt[field] = value
                context, transport, original = self.setup_recovery(directory, receipt)
                parent_bytes = context.journal._path(PARENT).read_bytes()
                lease_bytes = context.journal.lease.read_bytes()
                rejected = self.recover(context)
                self.assertEqual(rejected['status'], 'UNKNOWN')
                self.assertEqual(rejected['error']['code'], 'CUT_RECOVERY_RECEIPT_NOT_VERIFIED')
                self.assertIn(check, rejected['error']['failed_checks'])
                self.assertEqual(rejected['receipt'], receipt)
                self.assertEqual(context.journal.get(PARENT), original)
                self.assertEqual(context.journal._path(PARENT).read_bytes(), parent_bytes)
                self.assertEqual(context.journal.lease.read_bytes(), lease_bytes)
                self.assertEqual(self.recover(context), rejected)
                self.assertEqual(context.operation_status(CHILD), rejected)
                self.assertEqual(transport.operations, [CUT, RECOVER, 'runtime.receipt'])

    def test_lease_id_and_operation_rechecked_after_transport(self):
        for change in ({'request_id': 'f' * 32}, {'operation': 'project.open'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory(prefix='md-cut-recovery-host-') as directory:
                context, transport, original = self.setup_recovery(directory, restored_receipt())
                changed_lease = json.loads(context.journal.lease.read_text(encoding='utf8'))
                changed_lease.update(change)
                transport.before_recovery_return = lambda: context.journal.lease.write_text(json.dumps(changed_lease), encoding='utf8')
                rejected = self.recover(context)
                self.assertEqual(rejected['status'], 'UNKNOWN')
                self.assertIn('original_cut_lease', rejected['error']['failed_checks'])
                self.assertEqual(context.journal.get(PARENT), original)
                self.assertEqual(json.loads(context.journal.lease.read_text(encoding='utf8')), changed_lease)


if __name__ == '__main__':
    unittest.main()
