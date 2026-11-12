"""Build native MD/MCP bridges using a caller-supplied Windows Qt SDK."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent
REST = Path('qt_rest_adapter')
GRID = Path('qt_world_grid_step')
REST_BASE = REST / 'bridge.cpp'
GRID_COMMON = Path('qt_world_adapter/canvas_common.inc')
QT_MODULES = ('Qt6Core', 'Qt6Gui', 'Qt6Widgets', 'Qt6OpenGLWidgets')
FORBIDDEN = ('SendInput', 'mouse_event', 'keybd_event', 'SetCursorPos', 'SetForegroundWindow',
             'SetActiveWindow', 'SetFocus', 'ShowWindow', 'SetCapture', 'AttachThreadInput',
             'SwitchDesktop', 'OpenProcess', 'WriteProcessMemory', 'VirtualAllocEx',
             'CreateRemoteThread', 'PostMessage', 'SendMessage', 'processEvents', 'grabMouse',
             'grabKeyboard', 'activateWindow', 'setFocus', 'showNormal', 'showMinimized', 'showMaximized')
EXPORT_ROW = re.compile(r'^[ \t]+\d+[ \t]+[\dA-F]+[ \t]+[\dA-F]{8}[ \t]+(\S+)', re.MULTILINE)
IMPORT_ROW = re.compile(r'^[ \t]+[\dA-F]+[ \t]+(\S+)[ \t]*$', re.MULTILINE)


def rest_spec(leaf, source, exports, original):
    exports = set(exports)
    if source != 'bridge.cpp':
        exports.update(('mdqt_rest_adapter_abi_version', 'mdqt_rest_adapter_inspect'))
    return {'library': f'md_qt_rest_{leaf}_bridge.dll', 'source': REST / source,
            'included_sources': () if source == 'bridge.cpp' else (REST_BASE,),
            'exports': exports, 'original_sha256': original, 'qt_modules': QT_MODULES[:3]}


TARGETS = {
    'rest_adapter': rest_spec('adapter', 'bridge.cpp',
        ('mdqt_rest_adapter_abi_version', 'mdqt_rest_adapter_inspect'),
        'fdefe27d693f87182b6f1a4091c1804736a0c99da5e06d22db8928db8a9be4da'),
    'rest_context': rest_spec('context', 'context_bridge.cpp',
        ('mdqt_rest_context_abi_version', 'mdqt_rest_context_inspect'),
        'f3cc0f85604cd100575a3e43d3866ef0ec433fd438f5fd16eb14d7ddd0455c38'),
    'rest_simulation_entry': rest_spec('simulation_entry', 'simulation_entry_bridge.cpp',
        ('mdqt_rest_simulation_entry_abi_version', 'mdqt_set_simulation_ui_running', 'mdqt_enter_brush_pinching'),
        'a82c9eb8367353b2a9b20a2d4c315ccd65874b3d974f0145ee515b28116b21a5'),
    'world_grid_step': {
        'library': 'md_qt_world_grid_step.dll', 'source': GRID / 'bridge.cpp', 'included_sources': (GRID_COMMON,),
        'exports': {'mdqt_world_grid_step_abi_version', 'mdqt_world_grid_step_json'}, 'qt_modules': QT_MODULES,
        'original_sha256': '1595e064afa349606e64e8e2482a6309011f8f1b1246a1c31a4c01d92b429943',
    },
}


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def import_table(dump):
    sections = re.split(r'^    (\S+\.dll)[ \t]*$', dump, flags=re.IGNORECASE | re.MULTILINE)
    return {name.lower(): set(IMPORT_ROW.findall(rows)) for name, rows in zip(sections[1::2], sections[2::2], strict=True)}


def build(args, target, spec):
    output = args.out / target
    output.mkdir(parents=True, exist_ok=False)
    tools = args.vc / 'bin/Hostx64/x64'
    source = args.source / spec['source']
    obj, dll = output / 'bridge.obj', output / spec['library']

    def run(program, options, name):
        process = subprocess.run([str(tools / program), *map(str, options)], cwd=output,
                                 capture_output=True, text=True, encoding='utf8', errors='replace', timeout=90)
        data = process.stdout + process.stderr
        log = output / f'{name}.log'
        log.write_text(data, encoding='utf8')
        if process.returncode:
            raise RuntimeError(f'{target}: {name} failed. See {log}')
        return data

    def dumpbin(option, asset, label):
        return run('dumpbin.exe', [option, asset], label)

    kit_include = args.windows_kits / 'Include' / args.kit_version
    kit_lib = args.windows_kits / 'Lib' / args.kit_version
    modules = spec['qt_modules']
    includes = [args.vc / 'include', args.qt_sdk / 'include',
                *(args.qt_sdk / 'include' / name.replace('Qt6', 'Qt', 1) for name in modules),
                *(kit_include / name for name in ('ucrt', 'shared', 'um', 'winrt'))]
    libraries = [args.vc / 'lib/x64', kit_lib / 'ucrt/x64', kit_lib / 'um/x64']
    qt_libraries = [args.qt_sdk / 'lib' / f'{name}.lib' for name in modules]
    for path in [source, *includes, *libraries, *qt_libraries]:
        if not path.exists():
            raise RuntimeError(f'Build input does not exist: {path}')
    flags = ['/nologo', '/c', '/std:c++17', '/EHsc', '/MD', '/O2', '/W4', '/WX', '/Zc:__cplusplus',
             '/permissive-', '/guard:cf', '/DQT_NO_DEBUG', '/DQT_SHARED', '/DNDEBUG', '/utf-8']
    if target == 'world_grid_step' or spec.get('unused_static_functions'):
        flags.append('/wd4505')
    flags.extend(f'/D{name}' for name in spec.get('defines', ()))
    generated_header = None
    if spec.get('install_directory_binding'):
        if args.runtime_qt is None or not (args.runtime_qt / 'Qt6Core.dll').is_file():
            raise RuntimeError(f'{target}: --runtime-qt must point to the installed Qt libraries')
        path = str(args.runtime_qt) + '\\'
        if '"' in path or any(ord(character) < 32 for character in path):
            raise ValueError('The installation path cannot be quoted as a Windows path')
        generated_header = output / 'mdmcp_install_dir.h'
        quoted = path.replace('\\', '\\\\')
        generated_header.write_text(f'#define MDMCP_QT_INSTALL_DIR L"{quoted}"\n', encoding='utf8')
        flags.append(f'/FI{generated_header}')
    run('cl.exe', [*flags, *[f'/I{path}' for path in includes], f'/Fo{obj}', source], 'compile')
    link_flags = ['/NOLOGO', '/DLL', '/MACHINE:X64', '/DYNAMICBASE', '/NXCOMPAT', '/GUARD:CF']
    run('link.exe', [*link_flags, f'/OUT:{dll}', f'/IMPLIB:{output / "bridge.lib"}',
                    *[f'/LIBPATH:{path}' for path in libraries], obj, *qt_libraries,
                    'kernel32.lib', 'user32.lib'], 'link')

    imports = dumpbin('/IMPORTS', dll, 'imports')
    exports = set(EXPORT_ROW.findall(dumpbin('/EXPORTS', dll, 'exports')))
    headers = dumpbin('/HEADERS', dll, 'headers')
    forbidden = [name for name in FORBIDDEN if re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', imports)]
    if '8664 machine (x64)' not in headers or exports != spec['exports'] or forbidden:
        raise RuntimeError(f'{target}: unexpected architecture, exports, or forbidden imports: {forbidden}')

    table = import_table(imports)
    if 'expected_import_libraries' in spec and set(table) != set(spec['expected_import_libraries']):
        raise RuntimeError(f'{target}: unexpected imported libraries: {sorted(set(table) ^ set(spec["expected_import_libraries"]))}')
    qt = {}
    for name in modules:
        symbols = table.get(f'{name.lower()}.dll')
        if symbols and args.runtime_qt:
            runtime = args.runtime_qt / f'{name}.dll'
            available = set(EXPORT_ROW.findall(dumpbin('/EXPORTS', runtime, f'{name}-runtime-exports')))
            if missing := symbols - available:
                raise RuntimeError(f'{target}: installed {name} is missing these symbols: {sorted(missing)}')
            qt[runtime.name] = {'sha256': sha256(runtime), 'matched_symbol_count': len(symbols)}

    assets = (spec['source'], *spec['included_sources'])
    receipt = {'status': 'COMPILED_NOT_LOADED', 'dll': spec['library'], 'sha256': sha256(dll),
               'original_sha256': spec['original_sha256'], 'source_sha256': {p.as_posix(): sha256(args.source / p) for p in assets},
               'build_script_sha256': sha256(Path(__file__)), 'qt_version': '6.10.3', 'architecture': 'x64',
               'c_abi_version_in_source': spec.get('abi', 1), 'pe_exports': sorted(exports), 'forbidden_imports': forbidden,
               'import_libraries': sorted(table), 'runtime_qt_export_check': qt, 'windows_kit_version': args.kit_version,
               'compiler_sha256': sha256(tools / 'cl.exe'), 'linker_sha256': sha256(tools / 'link.exe'),
               'qt_sdk_library_sha256': {path.name: sha256(path) for path in qt_libraries},
               'compile_flags': [flag for flag in flags if not flag.startswith('/FI')], 'link_flags': link_flags,
               'generated_install_binding_sha256': sha256(generated_header) if generated_header else None,
               'live_validation': 'pending'}
    (output / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=HERE / 'native')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--qt-sdk', type=Path, required=True)
    parser.add_argument('--vc', type=Path, required=True)
    parser.add_argument('--windows-kits', type=Path, required=True)
    parser.add_argument('--kit-version', default='10.0.26100.0')
    parser.add_argument('--runtime-qt', type=Path, help='Optionally verify against exports of this installed Qt runtime')
    parser.add_argument('--recipes', type=Path, help='Portable release target manifest')
    parser.add_argument('--target', action='append')
    args = parser.parse_args()
    for name in ('source', 'out', 'qt_sdk', 'vc', 'windows_kits', 'runtime_qt'):
        if path := getattr(args, name):
            setattr(args, name, path.resolve())
    header = args.qt_sdk / 'include/QtCore/qtcoreversion.h'
    if not re.search(r'QTCORE_VERSION_STR\s+"6\.10\.3"', header.read_text(encoding='utf8')):
        raise RuntimeError('Qt 6.10.3 release SDK required')
    targets = TARGETS
    if args.recipes:
        recipes = json.loads(args.recipes.read_text(encoding='utf8'))
        if recipes.get('schema') != 'mdmcp-portable-native-recipes/1':
            raise ValueError('Unsupported native build recipes')
        targets = recipes['targets']
        for target, spec in targets.items():
            if re.fullmatch(r'[a-z][a-z0-9_]*', target) is None or Path(spec['library']).name != spec['library']:
                raise ValueError('Invalid target or DLL name')
            source_files = {Path(name): value for name, value in spec['source_sha256'].items()}
            for path, expected in source_files.items():
                if not (args.source / path).resolve().is_relative_to(args.source) or sha256(args.source / path) != expected:
                    raise ValueError(f'Native source hash mismatch: {path}')
            spec.update(source=Path(spec['source']), included_sources=tuple(Path(path) for path in spec['included_sources']),
                        exports=set(spec['exports']))
            if set(source_files) != {spec['source'], *spec['included_sources']}:
                raise ValueError('Build recipe hashes must cover the declared source files')
    for target in args.target or targets:
        if target not in targets:
            parser.error(f'unknown target: {target}')
        receipt = build(args, target, targets[target])
        print(json.dumps({'target': target, 'sha256': receipt['sha256'], 'status': receipt['status']}), flush=True)


if __name__ == '__main__':
    main()
