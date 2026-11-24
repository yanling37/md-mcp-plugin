"""Assemble a fresh local-install source tree from reviewed native build receipts."""

import argparse
from hashlib import file_digest
import json
from pathlib import Path
import re
import shutil


HERE = Path(__file__).resolve().parent
PIN_MODULES = {'cloth_meshbox_entry': 'cloth_meshbox_native.py', 'cloth_meshbox_select': 'cloth_meshbox_native.py',
               'cloth_vertex_context': 'cloth_vertex_context.py', 'world_adapter': 'world_adapter.py'}
CURVE_TARGETS = ('scene_adapter', 'canvas')


def sha(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def checked_assets(build, recipes):
    builder_sha = sha(HERE / 'build_bridges.py')
    assets = {}
    for target, spec in recipes.items():
        output = build / target
        receipt_path = output / 'build-receipt.json'
        dll = output / spec['library']
        if not receipt_path.is_file() or not dll.is_file():
            raise ValueError(
                f'{target}: missing build-receipt.json or {dll.name} in {output}. '
                f'Run build_bridges.py with --target {target} before staging.'
            )
        receipt = json.loads(receipt_path.read_text(encoding='utf8'))
        digest = receipt['sha256']
        if (not dll.is_file() or dll.is_symlink() or not 0 < dll.stat().st_size <= 4 * 1024 * 1024
                or re.fullmatch(r'[0-9a-f]{64}', digest) is None or sha(dll) != digest
                or receipt['status'] != 'COMPILED_NOT_LOADED' or receipt['architecture'] != 'x64'
                or receipt['qt_version'] != '6.10.3' or receipt['original_sha256'] != spec['original_sha256']
                or receipt['build_script_sha256'] != builder_sha or receipt['source_sha256'] != spec['source_sha256']
                or receipt['dll'] != spec['library'] or receipt['c_abi_version_in_source'] != spec['abi']
                or receipt['pe_exports'] != spec['exports'] or receipt['import_libraries'] != spec['expected_import_libraries']
                or receipt['forbidden_imports'] != []
                or set(receipt['runtime_qt_export_check']) != {name + '.dll' for name in spec['qt_modules']}
                or spec.get('install_directory_binding') and not receipt.get('generated_install_binding_sha256')):
            raise ValueError(f'{target}: build receipt or adapter bytes differ from the reviewed recipe')
        manifest = {**spec['manifest_template'], 'sha256': digest, 'revision': digest}
        if 'source_sha256' in manifest:
            manifest['source_sha256'] = spec['source_sha256'][spec['source']]
        assets[target] = (dll, digest, manifest)
    return assets


def stage(project, build, output, *, profile='full'):
    document = json.loads((HERE / 'release-recipes.json').read_text(encoding='utf8'))
    if document['schema'] != 'mdmcp-portable-native-recipes/1':
        raise ValueError('Unsupported release recipes')
    recipes = document['targets']
    if profile == 'curve':
        recipes = {target: recipes[target] for target in CURVE_TARGETS}
    elif profile != 'full':
        raise ValueError(f'Unknown native install profile: {profile}')
    assets = checked_assets(build, recipes)
    package_source = project / 'src/md_mcp_plugin'
    pins = {}
    for target, filename in PIN_MODULES.items():
        if target not in assets:
            continue
        if filename not in pins:
            pins[filename] = (package_source / 'embedded' / filename).read_text(encoding='utf8')
        original = recipes[target]['original_sha256']
        if pins[filename].count(original) != 1:
            raise ValueError(f'{target}: expected exactly one original package pin in {filename}')
        pins[filename] = pins[filename].replace(original, assets[target][1])

    output.mkdir(parents=True, exist_ok=False)
    for filename in ('pyproject.toml', 'README.md', 'README.public.md', 'LICENSE', 'NOTICE.md', 'NOTICE'):
        source = project / filename
        if source.is_file():
            shutil.copy2(source, output / filename)
    package = output / 'src/md_mcp_plugin'
    ignore_generated = shutil.ignore_patterns('__pycache__', '*.pyc', '*.dll')

    def ignore_unstaged(path, names):
        ignored = ignore_generated(path, names)
        if Path(path) == package_source / 'native':
            ignored.update(set(names) - assets.keys())
        return ignored

    shutil.copytree(package_source, package, ignore=ignore_unstaged)
    for filename, text in pins.items():
        (package / 'embedded' / filename).write_text(text, encoding='utf8')
    for target, (dll, digest, manifest) in assets.items():
        directory = package / 'native' / target
        revision = directory / digest
        revision.mkdir(parents=True, exist_ok=False)
        shutil.copy2(dll, revision / dll.name)
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
    print(f'Staged {len(assets)} verified native assets in a new local install tree')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=HERE.parent)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--profile', choices=('full', 'curve'), default='full',
                        help='Stage every bridge (default), or only the curve workflow and its recovery bridge.')
    args = parser.parse_args()
    try:
        stage(args.project.resolve(strict=True), args.build.resolve(strict=True), args.out.resolve(), profile=args.profile)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{parser.prog}: {exc}\n')


if __name__ == '__main__':
    main()
