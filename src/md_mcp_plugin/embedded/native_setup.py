"""Configure only the bridge shipped with this package, never a tool-supplied DLL."""
import json
from pathlib import Path
import re


def configure_canvas():
    from .canvas import install_native_bridge
    directory = Path(__file__).resolve().parents[1] / "native" / "canvas"
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return {"configured": False, "reason": "PACKAGED_CANVAS_BRIDGE_MISSING"}
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    if (manifest.get("schema") != "md-mcp-packaged-canvas/1"
            or manifest.get("library") != "md_qt_canvas_bridge.dll"
            or manifest.get("qt_version") != "6.10.3"
            or manifest.get("architecture") != "x64"
            or not isinstance(manifest.get("sha256"), str)
            or re.fullmatch("[0-9a-f]{64}", manifest["sha256"]) is None):
        raise ValueError("Invalid packaged canvas bridge manifest")
    revision = manifest.get("revision")
    if revision is not None:
        if not isinstance(revision, str) or re.fullmatch("[0-9a-f]{64}", revision) is None:
            raise ValueError("Invalid packaged native revision")
        directory = directory / revision
    return install_native_bridge(str(directory / manifest["library"]), manifest["sha256"])


def _packaged_scene_adapter():
    import tempfile
    directory = Path(__file__).resolve().parents[1] / "native" / "scene_adapter"
    path = directory / "manifest.json"
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf8"))
    libraries = {'md-mcp-packaged-scene-adapter/2':'md_qt_scene_adapter_capture_r2_bridge.dll',
                 'md-mcp-packaged-scene-adapter/3':'md_qt_scene_adapter_zoom_r3_bridge.dll',
                 'md-mcp-packaged-scene-adapter/4':'md_qt_scene_adapter_v4_bridge.dll',
                 'md-mcp-packaged-scene-adapter/5':'md_qt_scene_adapter_v5_bridge.dll',
                 'md-mcp-packaged-scene-adapter/6':'md_qt_scene_adapter_v6_bridge.dll'}
    if (manifest.get('schema') not in libraries
            or manifest.get("library") != libraries[manifest['schema']]
            or manifest.get("qt_version") != "6.10.3" or manifest.get("architecture") != "x64"
            or not isinstance(manifest.get("sha256"), str)
            or re.fullmatch("[0-9a-f]{64}", manifest["sha256"]) is None
            or manifest.get("revision") != manifest["sha256"]):
        raise ValueError("Invalid packaged scene adapter manifest")
    library = directory / manifest["revision"] / manifest["library"]
    return {"path": str(library), "sha256": manifest["sha256"],
            "artifact_root": Path(tempfile.gettempdir()) / "md-mcp-plugin-scene-captures"}


def configure_scene_adapter():
    package = _packaged_scene_adapter()
    if package is None:
        return {"configured": False, "reason": "PACKAGED_SCENE_ADAPTER_MISSING"}
    return get_scene_adapter().install_native_bridge(**package)


def get_scene_adapter():
    from .dispatch import _module
    path = Path(__file__).resolve().parents[1] / 'native' / 'scene_adapter' / 'manifest.json'
    version = json.loads(path.read_text(encoding='utf8')).get('schema') if path.is_file() else None
    if version == 'md-mcp-packaged-scene-adapter/2':
        return _module('scene_adapter_v2')
    if version == 'md-mcp-packaged-scene-adapter/3':
        return _module('scene_adapter_v3')
    if version == 'md-mcp-packaged-scene-adapter/4':
        return _module('scene_adapter_v4')
    if version == 'md-mcp-packaged-scene-adapter/5':
        return _module('scene_adapter_v5')
    if version == 'md-mcp-packaged-scene-adapter/6':
        return _module('scene_adapter_v6')
    raise ValueError('No packaged scene-adapter Python/native cohort is selected')


def configure_rest_host_controls():
    from .rest_adapter import install_host_controls_bridge
    directory = Path(__file__).resolve().parents[1] / 'native' / 'rest_host_controls'
    path = directory / 'manifest.json'
    if not path.is_file():
        return {'configured': False, 'reason': 'PACKAGED_REST_HOST_CONTROLS_MISSING'}
    manifest = json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema') != 'md-mcp-packaged-rest-host-controls/1'
            or manifest.get('library') != 'md_qt_rest_host_controls_bridge.dll'
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or not isinstance(manifest.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', manifest['sha256']) is None
            or manifest.get('revision') != manifest['sha256']):
        raise ValueError('Invalid packaged Rest host controls manifest')
    return install_host_controls_bridge(str(directory / manifest['revision'] / manifest['library']), manifest['sha256'])


def configure_rest_selected_trial():
    from .rest_selected_trial import DLL_NAME, install_native_bridge
    directory = Path(__file__).resolve().parents[1] / 'native' / 'rest_selected_trial'
    path = directory / 'manifest.json'
    if not path.is_file():
        return {'configured': False, 'reason': 'PACKAGED_REST_SELECTED_TRIAL_MISSING'}
    manifest = json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema') != 'md-mcp-packaged-rest-selected-trial/1' or manifest.get('library') != DLL_NAME
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or not isinstance(manifest.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', manifest['sha256']) is None
            or manifest.get('revision') != manifest['sha256']):
        raise ValueError('Invalid packaged Selected Rest trial manifest')
    return install_native_bridge(str(directory / manifest['revision'] / DLL_NAME), manifest['sha256'])


def configure_gizmo_scale():
    from .gizmo_scale import SERIES_LIBRARY as library, install_native_bridge

    directory = Path(__file__).resolve().parents[1] / 'native' / 'gizmo_scale_v2'
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf8'))
    revision = manifest.get('sha256')
    if (manifest.get('schema') != 'md-mcp-packaged-gizmo-scale/2' or manifest.get('library') != library
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or not isinstance(revision, str) or re.fullmatch('[0-9a-f]{64}', revision) is None):
        raise ValueError('Invalid packaged Gizmo Scale bridge manifest')
    install_native_bridge(str(directory / revision / library), revision, abi=2)


def configure_curve_writer():
    from . import curves
    from .dispatch import _module
    configured = configure_scene_adapter()
    if not configured.get('configured'):
        return configured
    _module('curve_projection')
    if hasattr(get_scene_adapter(),'bezier_drag_precise_unsnapped'):
        _module('native_curve_precision')
    provider = _module('native_curve_provider')
    curves.install_handle_writer(provider.set_handle, name='fresh_native_2d_handle_provider',
        evidence='Current UUID/role, fresh scene paint, unique complete marker geometry, fixed Alt batch and independent native readback')
    return {'configured': True}


def configure_world_adapter():
    from .world_adapter import install_native_bridge
    directory=Path(__file__).resolve().parents[1]/'native'/'world_adapter'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_WORLD_ADAPTER_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-world-adapter/1'
            or manifest.get('library')!='md_qt_world_adapter_bridge.dll'
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged world adapter manifest')
    return install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_rest_dock_identity():
    from .rest_dock_identity import install_native_bridge
    directory=Path(__file__).resolve().parents[1]/'native'/'rest_dock_identity'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_REST_DOCK_IDENTITY_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-rest-dock-identity/1'
            or manifest.get('library')!='md_qt_rest_dock_identity_bridge.dll'
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged dock identity manifest')
    return install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_rest_visible_contents():
    from .rest_visible_contents import install_native_bridge
    directory=Path(__file__).resolve().parents[1]/'native'/'rest_visible_contents'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_REST_VISIBLE_CONTENTS_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-rest-visible-contents/1'
            or manifest.get('library')!='md_qt_rest_visible_contents_bridge.dll'
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged visible contents manifest')
    return install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_rest_visible_recovery():
    from .rest_visible_recovery import install_native_bridge
    directory=Path(__file__).resolve().parents[1]/'native'/'rest_visible_recovery'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_REST_VISIBLE_RECOVERY_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-rest-visible-recovery/1'
            or manifest.get('library')!='md_qt_rest_contents_local_bridge.dll'
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged local contents reader manifest')
    return install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_rest_pinching_override():
    from .rest_pinching_override import install_native_bridge
    directory=Path(__file__).resolve().parents[1]/'native'/'rest_pinching_override'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_REST_PINCHING_OVERRIDE_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-rest-pinching-override/1'
            or manifest.get('library')!='md_qt_rest_pinching_override_bridge.dll'
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged pinching override manifest')
    return install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_rest_simulation_context():
    from .dispatch import _module
    reader = _module('rest_simulation_context')
    directory=Path(__file__).resolve().parents[1]/'native'/'rest_simulation_context'
    path=directory/'manifest.json'
    if not path.is_file():return {'configured':False,'reason':'PACKAGED_REST_SIMULATION_CONTEXT_MISSING'}
    manifest=json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema')!='md-mcp-packaged-rest-simulation-context/2'
            or manifest.get('library')!=reader.LIBRARY
            or manifest.get('qt_version')!='6.10.3' or manifest.get('architecture')!='x64'
            or not isinstance(manifest.get('sha256'),str) or re.fullmatch('[0-9a-f]{64}',manifest['sha256']) is None
            or manifest.get('revision')!=manifest['sha256']):
        raise ValueError('Invalid packaged simulation context manifest')
    return reader.install_native_bridge(str(directory/manifest['revision']/manifest['library']),manifest['sha256'])


def configure_cut_qt():
    from .dispatch import _module

    directory = Path(__file__).resolve().parents[1] / 'native' / 'cut_qt_atomic'
    path = directory / 'manifest.json'
    if not path.is_file() or not Path(__file__).with_name('native_cut_provider.py').is_file():
        return {'configured': False, 'reason': 'PACKAGED_ATOMIC_CUT_PROVIDER_MISSING'}
    manifest = json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema') != 'md-mcp-packaged-cut-qt-atomic/1'
            or manifest.get('library') != 'md_qt_cut_qt_atomic_bridge.dll'
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or not isinstance(manifest.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', manifest['sha256']) is None
            or manifest.get('revision') != manifest['sha256']):
        raise ValueError('Invalid packaged atomic Cut manifest')
    scene = configure_scene_adapter()
    if not scene.get('configured'):
        return scene
    bridge = _module('cut_qt_atomic')
    bridge.install_native_bridge(str(directory / manifest['revision'] / manifest['library']), manifest['sha256'])
    cut = _module('cut_qt')
    provider = _module('native_cut_provider')
    active = cut._state()['selector']
    if active is None or active.get('candidate') is not provider.candidate or active.get('verify') is not provider.verify:
        cut.install_atomic_line_selector(provider.candidate, provider.verify, name=provider.NAME,
                                         evidence=provider.EVIDENCE, surface_readback=True)
    return {'configured': True}


def configure_world_numeric_probe():
    from .world_numeric_probe import install_native_bridge

    directory = Path(__file__).resolve().parents[1] / 'native' / 'world_numeric_probe'
    path = directory / 'manifest.json'
    if not path.is_file():
        return {'configured': False, 'reason': 'PACKAGED_WORLD_NUMERIC_PROBE_MISSING'}
    manifest = json.loads(path.read_text(encoding='utf8'))
    if (manifest.get('schema') != 'md-mcp-packaged-world-numeric-probe/1'
            or manifest.get('library') != 'md_qt_world_numeric_probe.dll'
            or manifest.get('qt_version') != '6.10.3' or manifest.get('architecture') != 'x64'
            or not isinstance(manifest.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', manifest['sha256']) is None
            or manifest.get('revision') != manifest['sha256']):
        raise ValueError('Invalid packaged Translate 3D probe manifest')
    return install_native_bridge(str(directory / manifest['revision'] / manifest['library']), manifest['sha256'])
