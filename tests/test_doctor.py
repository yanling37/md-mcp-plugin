import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from md_mcp_plugin import doctor
from md_mcp_plugin.config import Config
from md_mcp_plugin.errors import PluginError


class DoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Config("127.0.0.1", 7421, 90, self.root / "journal", self.root / "src")
        for patcher in (patch.object(doctor.Config, "from_env", return_value=self.config),
                        patch.object(doctor.sys, "platform", "win32"),
                        patch.object(doctor.platform, "machine", return_value="AMD64"),
                        patch.object(doctor.struct, "calcsize", return_value=8)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def stage_bridge(self, name):
        schema, library = doctor.BRIDGES[name]
        sha = hashlib.sha256(b"sample bridge").hexdigest()
        target = self.config.source_dir / "md_mcp_plugin" / "native" / name
        binary = target / sha / library
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"sample bridge")
        (target / "manifest.json").write_text(json.dumps(dict(schema=schema, library=library, sha256=sha,
                                                               revision=sha, qt_version="6.10.3", architecture="x64")))
        return binary

    def test_offline_reads_bridges_without_connecting_or_creating_a_journal(self):
        for name in doctor.BRIDGES:
            self.stage_bridge(name)
        with patch.object(doctor, "Transport") as transport:
            result = doctor.run(offline=True)
        self.assertTrue(result["ok"])
        transport.assert_not_called()
        self.assertFalse(self.config.data_dir.exists())

    def test_missing_bridge_and_installed_hash_mismatch_are_distinct(self):
        self.stage_bridge("canvas").write_bytes(b"something else")
        result = doctor.run(offline=True)
        codes = {entry["check"]: entry["code"] for entry in result["checks"]}
        self.assertFalse(result["ok"])
        self.assertEqual(codes["bridge.canvas"], "BRIDGE_HASH_MISMATCH")
        self.assertEqual(codes["bridge.scene_adapter"], "BRIDGE_MANIFEST_MISSING")

    def test_active_lease_survives_verbatim(self):
        self.config.data_dir.mkdir()
        lease = self.config.data_dir / "mutation.lock"
        contents = json.dumps({"request_id": "a" * 32}).encode()
        lease.write_bytes(contents)
        check = doctor.journal_check(self.config.data_dir)
        self.assertEqual(check["code"], "MUTATION_LEASE_ACTIVE")
        self.assertEqual(lease.read_bytes(), contents)

    def test_listener_failure_does_not_send_the_scene_call(self):
        with patch.object(doctor, "Transport") as transport:
            transport.return_value.ping.side_effect = PluginError("LISTENER_UNAVAILABLE", "private details")
            checks = doctor.host_checks(self.config)
            transport.return_value.execute.assert_not_called()
        self.assertEqual(checks[0]["code"], "LISTENER_UNAVAILABLE")
        self.assertNotIn("private details", str(checks))

    def test_live_check_uses_a_bounded_scene_read_and_reports_an_unqualified_host(self):
        with patch.object(doctor, "Transport") as transport:
            transport.return_value.execute.return_value = {
                "status": "SUCCEEDED", "result": {"identity": {"md_version": [2026, 2, 0], "project_path": "private"}}}
            checks = doctor.host_checks(self.config)
            self.assertEqual(transport.call_args.args[0].timeout, 5)
            transport.return_value.execute.assert_called_once_with("scene.snapshot", {})
        self.assertFalse(checks[-1]["ok"])
        self.assertEqual(checks[-1]["code"], "MD_VERSION_UNQUALIFIED")
        self.assertNotIn("private", str(checks))
