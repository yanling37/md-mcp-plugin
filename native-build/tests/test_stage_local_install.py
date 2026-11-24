"""Verify staging profiles using synthetic build receipts without loading native code."""

from hashlib import sha256
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import unittest


NATIVE_BUILD = Path(__file__).resolve().parents[1]
PROJECT = NATIVE_BUILD.parent
STAGING = runpy.run_path(str(NATIVE_BUILD / 'stage_local_install.py'))
RECIPES = json.loads((NATIVE_BUILD / 'release-recipes.json').read_text(encoding='utf8'))['targets']
BUILDER_SHA = sha256((NATIVE_BUILD / 'build_bridges.py').read_bytes()).hexdigest()


class StageLocalInstallTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.build = root / 'build'
        self.output = root / 'staged'
        self.package = self.output / 'src/md_mcp_plugin'

    def write_receipt(self, target):
        spec = RECIPES[target]
        folder = self.build / target
        folder.mkdir(parents=True)
        dll = folder / spec['library']
        dll.write_bytes(f'Synthetic bridge fixture for {target}'.encode())
        digest = sha256(dll.read_bytes()).hexdigest()
        receipt = {
            'sha256': digest,
            'status': 'COMPILED_NOT_LOADED',
            'architecture': 'x64',
            'qt_version': '6.10.3',
            'original_sha256': spec['original_sha256'],
            'build_script_sha256': BUILDER_SHA,
            'source_sha256': spec['source_sha256'],
            'dll': spec['library'],
            'c_abi_version_in_source': spec['abi'],
            'pe_exports': spec['exports'],
            'import_libraries': spec['expected_import_libraries'],
            'forbidden_imports': [],
            'runtime_qt_export_check': {f'{module}.dll': {} for module in spec['qt_modules']},
            'generated_install_binding_sha256': sha256(b'synthetic fixture').hexdigest(),
        }
        (folder / 'build-receipt.json').write_text(json.dumps(receipt), encoding='utf8')
        return dll, digest

    def test_curve_profile_stages_both_required_bridges_and_public_metadata(self):
        fixtures = {target: self.write_receipt(target) for target in ('scene_adapter', 'canvas')}
        STAGING['stage'](PROJECT, self.build, self.output, profile='curve')
        self.assertEqual({path.name for path in (self.package / 'native').iterdir()}, set(fixtures))
        for target, (source_dll, digest) in fixtures.items():
            bridge = self.package / 'native' / target
            manifest = json.loads((bridge / 'manifest.json').read_text(encoding='utf8'))
            self.assertEqual(manifest['sha256'], digest)
            self.assertEqual((bridge / digest / source_dll.name).read_bytes(), source_dll.read_bytes())
        for filename in ('README.md', 'NOTICE.md'):
            self.assertEqual((self.output / filename).read_bytes(), (PROJECT / filename).read_bytes())

    def test_curve_profile_requires_the_recovery_bridge(self):
        self.write_receipt('scene_adapter')
        with self.assertRaisesRegex(ValueError, 'canvas: missing .*--target canvas'):
            STAGING['stage'](PROJECT, self.build, self.output, profile='curve')
        self.assertFalse(self.output.exists())

    def test_curve_profile_rejects_a_dll_that_differs_from_the_receipt(self):
        dll, _ = self.write_receipt('scene_adapter')
        self.write_receipt('canvas')
        dll.write_bytes(b'Unexpected bytes')
        with self.assertRaisesRegex(ValueError, 'scene_adapter: build receipt or adapter bytes differ'):
            STAGING['stage'](PROJECT, self.build, self.output, profile='curve')
        self.assertFalse(self.output.exists())

    def test_default_profile_stages_all_targets_and_rewrites_the_original_pins(self):
        fixtures = {target: self.write_receipt(target) for target in RECIPES}
        STAGING['stage'](PROJECT, self.build, self.output)
        self.assertEqual({path.name for path in (self.package / 'native').iterdir()}, set(RECIPES))
        binding = Path('native/cloth_meshbox_entry/binding.py')
        self.assertEqual((self.package / binding).read_bytes(), (PROJECT / 'src/md_mcp_plugin' / binding).read_bytes())
        for target, filename in STAGING['PIN_MODULES'].items():
            source = (self.package / 'embedded' / filename).read_text(encoding='utf8')
            self.assertNotIn(RECIPES[target]['original_sha256'], source)
            self.assertIn(fixtures[target][1], source)
