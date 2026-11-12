import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from md_mcp_plugin.config import Config
from md_mcp_plugin.context import Context
from md_mcp_plugin.errors import PluginError
from md_mcp_plugin.transport import Transport


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.timeout = False
        self.receipt = None

    def execute(self, operation, params, **kwargs):
        self.calls.append((operation, params, kwargs))
        if operation == "runtime.receipt":
            return {"protocol": "md-mcp-plugin/1", "status": "SUCCEEDED", "result": {"found": self.receipt is not None, "receipt": self.receipt}}
        if self.timeout:
            raise PluginError("TRANSPORT_ERROR", "Synthetic timeout after send", outcome="unknown", retry_safe=False)
        return {"protocol": "md-mcp-plugin/1", "status": "SUCCEEDED", "result": {"verified": True}, "error": None}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parents[1] / ".state" / "core-tests"
        self.base.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=self.base)
        self.root = Path(self.directory.name)
        self.config = Config("127.0.0.1", 7421, 30, self.root, Path(__file__).resolve().parents[1] / "src")
        self.transport = FakeTransport()
        self.context = Context(self.config, self.transport)

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(self.base.resolve()))
        self.directory.cleanup()

    def test_repeated_request_never_executes_twice(self):
        kwargs = dict(mutation=True, request_id="a" * 32, scene_token="b" * 64)
        first = self.context.call("project.open", {"path": "example.zprj"}, **kwargs)
        second = self.context.call("project.open", {"path": "example.zprj"}, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(self.context.journal.lease.exists())

    def test_numbered_fixed_operation_routes_but_statements_do_not(self):
        self.assertEqual(self.context.call('world_adapter.inspect_3d',{})['status'],'SUCCEEDED')
        for name in ('world_adapter.inspect_3d;other','world_adapter.3d','a.b.c','a/b.inspect'):
            self.assertEqual(self.context.call(name,{})['error']['code'],'INVALID_CALL')
        self.assertEqual([call[0]for call in self.transport.calls],['world_adapter.inspect_3d'])

    def test_changed_parameters_cannot_reuse_request(self):
        kwargs = dict(mutation=True, request_id="a" * 32, scene_token="b" * 64)
        self.context.call("project.open", {"path": "first.zprj"}, **kwargs)
        result = self.context.call("project.open", {"path": "second.zprj"}, **kwargs)
        self.assertEqual(result["error"]["code"], "REQUEST_ID_REUSED")
        self.assertEqual(len(self.transport.calls), 1)

    def test_timeout_keeps_lease_and_reconcile_does_not_repeat(self):
        self.transport.timeout = True
        kwargs = dict(mutation=True, request_id="a" * 32, scene_token="b" * 64)
        first = self.context.call("project.open", {}, **kwargs)
        self.assertEqual(first["status"], "UNKNOWN")
        self.assertTrue(self.context.journal.lease.exists())
        denied = self.context.call("project.open", {}, mutation=True, request_id="c" * 32, scene_token="d" * 64)
        self.assertEqual(denied["error"]["code"], "MUTATION_BUSY")
        self.transport.receipt = {"status": "SUCCEEDED", "result": {"verified": True}, "error": None}
        final = self.context.operation_status("a" * 32)
        self.assertEqual(final["status"], "SUCCEEDED")
        self.assertEqual([row[0] for row in self.transport.calls], ["project.open", "runtime.receipt"])
        self.assertFalse(self.context.journal.lease.exists())

    def test_two_contexts_share_mutation_lease(self):
        self.transport.timeout = True
        self.context.call("project.open", {}, mutation=True, request_id="a" * 32, scene_token="b" * 64)
        other = Context(self.config, FakeTransport())
        denied = other.call("project.open", {}, mutation=True, request_id="c" * 32, scene_token="d" * 64)
        self.assertEqual(denied["error"]["code"], "MUTATION_BUSY")

    def test_parameters_are_data_in_embedded_source(self):
        transport = Transport(self.config)
        captured = {}
        def rpc(method, params):
            captured.update(method=method, code=params["code"])
            return {"result": {"protocol": "md-mcp-plugin/1", "status": "SUCCEEDED"}}
        transport.request = rpc
        dangerous = "'); __import__('pathlib').Path('DO_NOT_CREATE').touch(); #"
        transport.execute("project.open", {"path": dangerous})
        tree = ast.parse(captured["code"])
        final = tree.body[-1].value
        payload = json.loads(ast.literal_eval(final.args[0].args[0]))
        self.assertEqual(payload["params"]["path"], dangerous)
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == "touch" for node in ast.walk(tree)))

    def test_read_passes_scope_and_rejects_malformed_scope_before_rpc(self):
        self.context.call("curves.inspect", {"pattern_uuid": "paper"}, scene_token="b" * 64)
        self.assertEqual(self.transport.calls[0][2]["scene_token"], "b" * 64)
        invalid = self.context.call("curves.inspect", {}, scene_token="old-project")
        self.assertEqual(invalid["error"]["code"], "INVALID_SCENE_TOKEN")
        self.assertEqual(len(self.transport.calls), 1)

    def test_stale_scoped_read_does_not_enter_native_operation(self):
        from md_mcp_plugin.embedded import dispatch
        with patch.object(dispatch, "check_scene_token", side_effect=PluginError("STALE_SCENE", "Changed project")), patch("md_mcp_plugin.embedded.scene.OPERATIONS", {"scene.snapshot": lambda params: self.fail("Stale read entered the getter")}):
            reply = dispatch.execute({"operation": "scene.snapshot", "params": {}, "scene_token": "b" * 64})
        self.assertEqual(reply["status"], "FAILED")
        self.assertEqual(reply["error"]["code"], "STALE_SCENE")
        self.assertFalse(reply["effects_started"])

    def test_explicit_recovery_keeps_original_failure_and_never_replays_it(self):
        executed = []
        def execute(operation, params, **kwargs):
            executed.append(operation)
            if operation == 'curves.set_handles':
                return {'status':'PARTIAL','result':{},'error':{'code':'RESIDUAL'},'effects_started':True,'requires_recovery':True}
            return {'status':'SUCCEEDED','result':{'verified':True},'error':None}
        self.transport.execute = execute
        original = 'a' * 32
        self.context.call('curves.set_handles', {}, mutation=True, request_id=original, scene_token='b'*64)
        recovered = self.context.recover_curve_operation(original, 'c'*32, 'd'*64, 'e'*64)
        self.assertEqual(recovered['status'], 'SUCCEEDED')
        parent = self.context.journal.get(original)
        self.assertEqual(parent['status'], 'PARTIAL')
        self.assertEqual(parent['recovery']['status'], 'SAVED_PROJECT_RELOADED')
        self.assertFalse(self.context.journal.lease.exists())
        self.assertEqual(self.context.recover_curve_operation(original, 'c'*32, 'd'*64, 'e'*64), recovered)
        self.assertEqual(executed, ['curves.set_handles', 'project.recover_curve'])


if __name__ == "__main__":
    unittest.main()
