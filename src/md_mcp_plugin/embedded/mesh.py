"""Fresh, explicit OBJ readback. OBJ indices are never native setter indices."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import tempfile
from time import perf_counter
from datetime import datetime, timezone
import uuid

from md_mcp_plugin.errors import PluginError
from .common import scene
from .mesh_export_guard import guarded_obj_export

MAX_OBJ_BYTES = 16 * 1024 * 1024
MAX_REPLY_BYTES = 24 * 1024 * 1024
MAX_ELEMENTS = 250000
MAX_FACE_CORNERS = 1000000
_OPTIONS = {
    'scale': 1.0, 'axisX': 0, 'axisY': 1, 'axisZ': 2,
    'bInvertX': False, 'bInvertY': False, 'bInvertZ': False,
    'bExportGarment': True, 'bExportAvatar': False,
    'bSingleObject': False, 'bThin': True, 'bSaveInZip': False,
    'bIncludeHiddenObject': True, 'bIncludeInnerShape': False,
    'bClothUnifiedUVCoordinates': False,
}
_OPTIONAL_OPTIONS = {
    'bUnifiedUVCoordinates': False, 'bCreateUnifiedTexture': False,
    'bMetaData': False, 'bUseSelectComponent': False,
}
_REF_KEYS = {'kind', 'pattern_uuid', 'scene_token', 'topology_fingerprint', 'snapshot_id', 'vertex_index'}
_GROUP_REF_KEYS = _REF_KEYS | {'group_name', 'export_vertex_index'}
_GEOMETRY_FIELDS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices', 'face_labels')


def _hash(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value or len(value) > 512 or value.strip() != value or any(ord(c) < 32 for c in value):
        raise ValueError(label + ' must be a nonempty exact string')
    return value


def _index(token, count):
    if re.fullmatch(r'-?[0-9]+', token) is None:
        raise ValueError('OBJ index must be an integer')
    value = int(token)
    if value == 0:
        raise ValueError('OBJ indices cannot be zero')
    result = value - 1 if value > 0 else count + value
    if not 0 <= result < count:
        raise ValueError('OBJ index is outside the declared array')
    return result


def parse_obj(payload):
    """Parse a bounded OBJ without loading referenced files or triangulating it.

    Arrays retain file order. Faces are polygons with independent v/vt/vn
    indices (zero-based here); negative indices resolve at their source line.
    Group/material labels are retained as labels, never interpreted as UUIDs.
    """
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_OBJ_BYTES:
        raise ValueError('OBJ must contain 1..16 MiB of bytes')
    vertices, uv, normals, faces, face_uv, face_normals, labels = [], [], [], [], [], [], []
    libraries, corner_count = [], 0
    current = {'object': '', 'groups': [], 'material': '', 'smoothing': ''}
    for number, line in enumerate(payload.decode('utf-8-sig').splitlines(), 1):
        if len(line) > 65536 or line.rstrip().endswith('\\'):
            raise ValueError('Unsupported oversized/continued OBJ line')
        fields = line.split('#', 1)[0].split()
        if not fields:
            continue
        tag, args = fields[0], fields[1:]
        if tag in ('v', 'vt', 'vn'):
            dimension = {'v': 3, 'vt': 2, 'vn': 3}[tag]
            if len(args) != dimension:
                raise ValueError('Unsupported OBJ coordinate dimension on line ' + str(number))
            values = [float(v) for v in args]
            if not all(math.isfinite(v) for v in values):
                raise ValueError('OBJ coordinates must be finite')
            target = {'v': vertices, 'vt': uv, 'vn': normals}[tag]
            target.append(values)
            if len(target) > MAX_ELEMENTS:
                raise ValueError('OBJ array exceeds bounded element limit')
        elif tag == 'f':
            if not 3 <= len(args) <= 256 or len(faces) >= MAX_ELEMENTS:
                raise ValueError('Unsupported OBJ face size/count')
            corner_count += len(args)
            if corner_count > MAX_FACE_CORNERS:
                raise ValueError('OBJ exceeds the total face-corner budget')
            vi, ti, ni = [], [], []
            for corner in args:
                parts = corner.split('/')
                if not 1 <= len(parts) <= 3 or not parts[0]:
                    raise ValueError('Malformed OBJ face corner')
                vi.append(_index(parts[0], len(vertices)))
                ti.append(_index(parts[1], len(uv)) if len(parts) > 1 and parts[1] else None)
                ni.append(_index(parts[2], len(normals)) if len(parts) > 2 and parts[2] else None)
            if len(set(vi)) != len(vi):
                raise ValueError('OBJ face contains repeated vertices')
            faces.append(vi); face_uv.append(ti); face_normals.append(ni)
            labels.append(dict(current, groups=list(current['groups'])))
        elif tag in ('o', 'g', 'usemtl', 's'):
            current[{'o': 'object', 'g': 'groups', 'usemtl': 'material', 's': 'smoothing'}[tag]] = args if tag == 'g' else ' '.join(args)
        elif tag == 'mtllib':
            libraries.extend(args)  # Metadata only. Never follow caller/file paths.
        elif tag in ('l', 'p', 'curv', 'surf', 'vp'):
            raise ValueError('OBJ contains non-surface geometry; not a pure mesh snapshot')
        else:
            raise ValueError('Unsupported OBJ directive: ' + tag)
    if not vertices or not faces:
        raise ValueError('OBJ has no complete surface mesh')
    topology = {'vertex_count': len(vertices), 'uv_count': len(uv), 'normal_count': len(normals),
                'faces': faces, 'face_uv_indices': face_uv, 'face_normal_indices': face_normals}
    return {'positions_mm': vertices, 'uv': uv, 'normals': normals, **topology,
            'face_labels': labels, 'material_libraries': libraries,
            'topology_fingerprint': _hash(topology),
            'index_space': 'zero_based_exported_OBJ_arrays', 'native_vertex_mapping_verified': False}


def _same_typed(actual, expected):
    return type(actual) is type(expected) and (all(type(x) is int for x in actual) and actual == expected if type(expected) is list else actual == expected)


def _options(api_types, indices, *, owner_required):
    if not callable(getattr(api_types, 'ImportExportOption', None)):
        raise PluginError('MESH_OPTIONS_UNAVAILABLE', 'ImportExportOption constructor is unavailable', effects_started=False)
    option = api_types.ImportExportOption()
    values = dict(_OPTIONS)
    for name, value in _OPTIONAL_OPTIONS.items():
        if hasattr(option, name):
            values[name] = value
    if hasattr(option, 'patternList'):
        values['patternList'] = list(indices)
    elif owner_required:
        raise PluginError('OWNER_FILTER_UNAVAILABLE', 'ImportExportOption.patternList is absent; use mesh.read_scene for explicitly scene-wide geometry', effects_started=False)
    enum = getattr(getattr(api_types, 'WELD_TYPE', None), 'FULLY_UNWELDED', None)
    if enum is None or not hasattr(option, 'weldType') or int(enum) != 0:
        raise PluginError('MESH_OPTIONS_UNAVAILABLE', 'Explicit FULLY_UNWELDED export option is unavailable', effects_started=False)
    for name, value in values.items():
        if not hasattr(option, name) or not _same_typed(getattr(option, name), value):
            # Values need not already equal the requested option; their types must.
            actual = getattr(option, name, None)
            valid_type = type(actual) is type(value) and (type(value) is not list or all(type(i) is int for i in actual))
            if not hasattr(option, name) or not valid_type:
                raise PluginError('MESH_OPTIONS_UNAVAILABLE', 'Required typed export field unavailable', field=name, effects_started=False)
        setattr(option, name, value)
        if not _same_typed(getattr(option, name), value):
            raise PluginError('MESH_OPTIONS_READBACK_FAILED', 'Export field did not retain its typed value', field=name, effects_started=False)
    option.weldType = enum
    if type(option.weldType) is not type(enum) or int(option.weldType) != 0:
        raise PluginError('MESH_OPTIONS_READBACK_FAILED', 'Unwelded option did not retain its enum value', effects_started=False)
    return option, dict(values, weldType='FULLY_UNWELDED')


def _runtime_rows(snapshot):
    rows = snapshot.get('patterns')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise PluginError('EMPTY_OR_INVALID_SCENE', 'A nonempty current pattern list is required', effects_started=False)
    mapping = {}
    for i, row in enumerate(rows):
        target = _text(row.get('pattern_uuid'), 'pattern_uuid')
        if target in mapping or type(row.get('index')) is not int or row['index'] != i:
            raise PluginError('AMBIGUOUS_PATTERN', 'Current UUID/index list is not unique and ordered', effects_started=False)
        mapping[target] = i
    return mapping


def _asset(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_OBJ_BYTES:
        raise PluginError('INVALID_MESH_ASSET', 'Export did not produce a bounded regular file', effects_started=False)
    payload = path.read_bytes()
    if not 0 < len(payload) <= MAX_OBJ_BYTES:
        raise PluginError('INVALID_MESH_ASSET', 'Export file changed size while being read', effects_started=False)
    return payload, {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest(), 'byte_count': len(payload)}


def _unsupported(exc):
    return {'status': 'UNSUPPORTED', 'supported': False, 'verified': False, 'effects_started': False,
            'error': exc.as_dict(), 'native_vertex_mapping_verified': False}


def group_inventory(mesh):
    groups = {}
    ungrouped = 0
    ambiguous = 0
    for row in mesh['face_labels']:
        labels = row['groups']
        if not labels:
            ungrouped += 1
        if len(labels) != 1:
            ambiguous += 1
        for name in set(labels):
            record = groups.setdefault(name, {'face_count': 0, 'exclusive_face_count': 0})
            record['face_count'] += 1
            record['exclusive_face_count'] += int(len(labels) == 1)
    return {'groups': groups, 'ungrouped_face_count': ungrouped,
            'nonexclusive_face_count': ambiguous, 'face_count': len(mesh['faces'])}


def extract_group(mesh, group_name):
    """Exact OBJ g-label subset, with no UUID/cloth/native-index inference."""
    group_name = _text(group_name, 'group_name')
    selected = [i for i, row in enumerate(mesh['face_labels']) if group_name in row['groups']]
    if not selected:
        raise PluginError('OBJ_GROUP_NOT_FOUND', 'Exact OBJ group is absent from this export', group_name=group_name, effects_started=False)
    if any(mesh['face_labels'][i]['groups'] != [group_name] for i in selected):
        raise PluginError('AMBIGUOUS_OBJ_GROUP', 'A selected face belongs to multiple OBJ groups', group_name=group_name, effects_started=False)
    maps = {}
    values = {}
    for array, corners in (('positions_mm', 'faces'), ('uv', 'face_uv_indices'), ('normals', 'face_normal_indices')):
        compact_to_export = sorted({index for i in selected for index in mesh[corners][i] if index is not None})
        export_to_compact = [None] * len(mesh[array])
        for compact, index in enumerate(compact_to_export):
            export_to_compact[index] = compact
        maps[array] = {'compact_to_export': compact_to_export, 'export_to_compact': export_to_compact}
        values[array] = [mesh[array][i] for i in compact_to_export]
        values[corners] = [[None if index is None else export_to_compact[index] for index in mesh[corners][i]] for i in selected]
    reverse_faces = [None] * len(mesh['faces'])
    for compact, index in enumerate(selected):
        reverse_faces[index] = compact
    maps['faces'] = {'compact_to_export': selected, 'export_to_compact': reverse_faces}
    topology = {'vertex_count': len(values['positions_mm']), 'uv_count': len(values['uv']), 'normal_count': len(values['normals']),
                **{key: values[key] for key in ('faces', 'face_uv_indices', 'face_normal_indices')}}
    return {**values, **topology,
            'face_labels': [mesh['face_labels'][i] for i in selected],
            'material_libraries': list(mesh['material_libraries']), 'group_index_maps': maps,
            'topology_fingerprint': _hash(topology), 'group_name': group_name,
            'index_space': 'zero_based_compact_OBJ_group_arrays', 'native_vertex_mapping_verified': False}


def _filter_output_check(mesh, before, target):
    names = {}
    for row in before['patterns']:
        name = row.get('information', {}).get('name')
        if isinstance(name, str) and name:
            names.setdefault(name, []).append(row['pattern_uuid'])
    row = next(row for row in before['patterns'] if row['pattern_uuid'] == target)
    name = row.get('information', {}).get('name')
    unique = isinstance(name, str) and names.get(name) == [target]
    other = set(names) - {name} if unique else set(names)
    foreign = sum(any(label in other for label in row['groups']) for row in mesh['face_labels'])
    unknown = sum(not row['groups'] or any(label not in names for label in row['groups']) for row in mesh['face_labels'])
    exact = unique and all(row['groups'] == [name] for row in mesh['face_labels'])
    return {'requested_runtime_name': name, 'runtime_name_unique': unique,
            'other_current_pattern_group_face_count': foreign,
            'unknown_or_ungrouped_face_count': unknown,
            'all_faces_have_exclusive_requested_name': exact,
            'group_inventory': group_inventory(mesh),
            # Even a matching name is label evidence, not an independent native
            # owner certificate. Do not turn a no-op SDK option into success.
            'native_pattern_filter_verified': False,
            'reason': 'FOREIGN_PATTERN_FACES_EXPORTED' if foreign else 'NO_INDEPENDENT_NATIVE_OWNER_CERTIFICATE'}


def _bounded_reply(result, asset):
    if len(json.dumps(result, allow_nan=False).encode('utf8')) > MAX_REPLY_BYTES:
        raise PluginError('MESH_REPLY_TOO_LARGE', 'Parsed mesh exceeds the bounded reply size; OBJ artifact was retained', obj_asset=asset, effects_started=False)
    return result


def _capture(params, pattern_only, *, group_name=None):
    required = {'pattern_uuid'} if pattern_only else set()
    allowed = required | {'vertex_indices'}
    if not pattern_only and group_name is None:
        allowed.add('summary_only')
    if not isinstance(params, dict) or set(params) - allowed or required - set(params):
        raise ValueError('Unexpected/missing mesh read parameters')
    indices = params.get('vertex_indices', [])
    if not isinstance(indices, list) or len(indices) > 4096 or any(type(i) is not int or i < 0 for i in indices) or len(set(indices)) != len(indices):
        raise ValueError('vertex_indices requires at most 4096 unique nonnegative integers')
    summary_only = params.get('summary_only', False)
    if type(summary_only) is not bool or summary_only and indices:
        raise ValueError('summary_only requires a boolean and no vertex references')
    target = _text(params['pattern_uuid'], 'pattern_uuid') if pattern_only else None
    before = scene()
    mapping = _runtime_rows(before)
    if target is not None and target not in mapping:
        raise PluginError('STALE_PATTERN_REFERENCE', 'UUID is absent from the current project', effects_started=False)
    requested = [mapping[target]] if target else list(mapping.values())
    api_types, exporter = importlib.import_module('ApiTypes'), importlib.import_module('export_api')
    try:
        option, fields = _options(api_types, requested, owner_required=pattern_only)
    except PluginError as exc:
        return _unsupported(exc)
    function = getattr(exporter, 'ExportOBJW', None)
    api_name = 'ExportOBJW'
    if not callable(function):
        function, api_name = getattr(exporter, 'ExportOBJ', None), 'ExportOBJ'
    if not callable(function):
        return _unsupported(PluginError('OBJ_EXPORT_UNAVAILABLE', 'Documented explicit OBJ export API is unavailable'))
    if scene() != before:
        raise PluginError('STALE_SCENE', 'Scene changed before mesh export', effects_started=False)
    directory = Path(tempfile.mkdtemp(prefix='md-mcp-mesh-')).resolve()
    path = directory / 'mesh.obj'
    started_at = datetime.now(timezone.utc).isoformat()
    # Exactly one documented signature, always explicit options, never a dialog,
    # selection setter, project load/save, simulation or an alternative retry.
    export_started = perf_counter()
    returned, activation_guard = guarded_obj_export(function, str(path), option, before['identity']['pid'])
    export_finished = perf_counter()
    if type(returned) is not list or not returned or any(type(p) is not str for p in returned):
        raise PluginError('OBJ_EXPORT_FAILED', 'Exporter must return its documented nonempty list of file paths', effects_started=False)
    if path not in [Path(p).resolve() for p in returned]:
        raise PluginError('OBJ_EXPORT_PATH_MISMATCH', 'Exporter did not return this capture OBJ path', effects_started=False)
    payload, asset = _asset(path)
    mesh = parse_obj(payload)
    parsed_at = perf_counter()
    after = scene()
    if after != before:
        raise PluginError('SCENE_CHANGED_DURING_EXPORT', 'Project/UUID/input/quality changed while capturing mesh', effects_started=False)
    mtl_path = path.with_suffix('.mtl')
    material_asset = _asset(mtl_path)[1] if mtl_path.exists() else None
    if summary_only:
        summary = {
            'schema': 'md-exported-obj-scene-digest-v1', 'capture_id': uuid.uuid4().hex,
            'scene_token': before['scene_token'], 'scene_identity': before['identity'],
            'captured_at_utc': started_at, 'scene_before_after_exact': True,
            'space': 'world', 'unit': 'mm', 'axis_order': ['X', 'Y', 'Z'],
            'vertex_count': mesh['vertex_count'], 'face_count': len(mesh['faces']),
            'topology_fingerprint': mesh['topology_fingerprint'],
            'geometry_sha256': _hash({key: mesh[key] for key in _GEOMETRY_FIELDS}),
            'geometry_fields': list(_GEOMETRY_FIELDS), 'group_inventory': group_inventory(mesh),
            'export': {'api': api_name, 'options': fields, 'returned_paths': returned,
                       'obj_asset': asset, 'mtl_asset': material_asset, 'activation_guard': activation_guard},
            'timing_ms': {'native_export': (export_finished - export_started) * 1000,
                          'read_and_parse': (parsed_at - export_finished) * 1000},
        }
        return _bounded_reply({'status': 'SUCCEEDED', 'supported': True, 'verified': True,
                               'effects_started': False, 'summary': summary, 'vertex_references': [],
                               'native_setter_index_mapping_verified': False}, asset)
    snapshot = {
        'schema': 'md-exported-obj-mesh-v1', 'capture_id': uuid.uuid4().hex,
        'captured_at_utc': started_at, 'pattern_uuid': None,
        'scope': 'unverified_native_filter_OBJ_export' if target else 'scene_garment_OBJ_export',
        'scene_token': before['scene_token'], 'scene_identity': before['identity'],
        'space': 'world', 'unit': 'mm', 'axis_order': ['X', 'Y', 'Z'],
        'owner_binding': {'basis': 'no_verified_export_face_to_UUID_binding',
                          'current_pattern_uuid_to_runtime_index': mapping,
                          'requested_pattern_indices': requested, 'group_names_used_as_owner_mapping': False},
        'export': {'api': api_name, 'options': fields, 'returned_paths': returned,
                   'obj_asset': asset, 'mtl_asset': material_asset, 'activation_guard': activation_guard},
        'scene_before_after_exact': True,
        'scene_token_scope': before.get('fingerprint_scope'),
        'geometry_scope': 'all_surface_faces_emitted_by_explicit_garment_export; accessories_are_not_inferred_as_solver_cloth',
        'solver_rest_verified': False, **mesh, 'group_inventory': group_inventory(mesh),
    }
    snapshot['snapshot_id'] = _hash(snapshot)
    if target:
        diagnostic = _filter_output_check(mesh, before, target)
        result = _unsupported(PluginError('OWNER_FILTER_OUTPUT_UNVERIFIED', 'Typed patternList readback does not establish actual owner filtering; use scene/group reads', effects_started=False))
        result.update(requested_pattern_uuid=target, filter_output_check=diagnostic,
                      export_diagnostic_snapshot=snapshot, vertex_references=[])
        return _bounded_reply(result, asset)
    if group_name is not None:
        source_id = snapshot['snapshot_id']
        subgroup = extract_group(mesh, group_name)
        snapshot.update(subgroup, source_snapshot_id=source_id, scope='exact_OBJ_group_label',
                        source_group_inventory=snapshot['group_inventory'], group_inventory=group_inventory(subgroup),
                        geometry_scope='exact_export_group_label_only; UUID_and_solver_cloth_binding_unverified')
        snapshot.pop('snapshot_id')
        snapshot['snapshot_id'] = _hash(snapshot)
    if any(i >= len(snapshot['positions_mm']) for i in indices):
        raise ValueError('Requested vertex index is outside this fresh OBJ/group snapshot')
    references = [vertex_reference(snapshot, i) for i in indices]
    result = {'status': 'SUCCEEDED', 'supported': True, 'verified': True,
              'effects_started': False, 'snapshot': snapshot, 'vertex_references': references,
              'native_setter_index_mapping_verified': False, 'live_validation': 'pending'}
    return _bounded_reply(result, asset)


def read_group(params):
    if not isinstance(params, dict) or set(params) - {'group_name', 'vertex_indices'} or 'group_name' not in params:
        raise ValueError('group_name and optional vertex_indices are required')
    name = _text(params['group_name'], 'group_name')
    return _capture({'vertex_indices': params.get('vertex_indices', [])}, False, group_name=name)


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'md-exported-obj-mesh-v1':
        raise ValueError('An exported OBJ snapshot is required')
    value = dict(snapshot)
    supplied = value.pop('snapshot_id', None)
    if _hash(value) != supplied:
        raise ReferenceError('STALE_REFERENCE: snapshot data changed')


def vertex_reference(snapshot, vertex_index):
    _validate_snapshot(snapshot)
    if type(vertex_index) is not int or not 0 <= vertex_index < len(snapshot['positions_mm']):
        raise ValueError('vertex_index is outside the exported snapshot')
    reference = {'kind': 'obj_export_vertex_snapshot_v1', 'pattern_uuid': snapshot['pattern_uuid'],
            'scene_token': snapshot['scene_token'], 'topology_fingerprint': snapshot['topology_fingerprint'],
            'snapshot_id': snapshot['snapshot_id'], 'vertex_index': vertex_index}
    if snapshot.get('scope') == 'exact_OBJ_group_label':
        reference.update(kind='obj_export_group_vertex_snapshot_v1', group_name=snapshot['group_name'],
                         export_vertex_index=snapshot['group_index_maps']['positions_mm']['compact_to_export'][vertex_index])
    return reference


def resolve_snapshot_vertex(snapshot, reference):
    _validate_snapshot(snapshot)
    expected_keys = _GROUP_REF_KEYS if snapshot.get('scope') == 'exact_OBJ_group_label' else _REF_KEYS
    if not isinstance(reference, dict) or set(reference) != expected_keys:
        raise ValueError('A complete OBJ snapshot reference is required')
    expected = vertex_reference(snapshot, reference['vertex_index'])
    if reference != expected:
        raise ReferenceError('STALE_REFERENCE: scene, owner, topology or capture changed')
    return reference['vertex_index']  # OBJ index ONLY, never an MD native index.


def probe_runtime(params):
    if params != {}:
        raise ValueError('Runtime probe takes no parameters')
    api_types, exporter = importlib.import_module('ApiTypes'), importlib.import_module('export_api')
    option = api_types.ImportExportOption()
    names = list(_OPTIONS) + list(_OPTIONAL_OPTIONS) + ['weldType', 'patternList']
    fields = {name: {'present': hasattr(option, name),
                     'type': type(getattr(option, name)).__name__ if hasattr(option, name) else None} for name in names}
    public_defaults = {}
    for name in dir(option):
        if name.startswith('_'):
            continue
        value = getattr(option, name)
        if type(value) in (bool, int, float) or type(value) is list and len(value) <= 32 and all(type(x) is int for x in value):
            public_defaults[name] = {'type': type(value).__name__, 'value': value}
    functions = {name: {'callable': callable(getattr(exporter, name, None)),
                        'doc': (getattr(getattr(exporter, name, None), '__doc__', None) or '')[:2048]}
                 for name in ('ExportOBJW', 'ExportOBJ')}
    return {'status': 'SUCCEEDED', 'verified': True, 'fields': fields, 'public_option_defaults': public_defaults, 'functions': functions,
            'export_calls': 0, 'metadata_only': True, 'native_vertex_mapping_verified': False}


OPERATIONS = {'mesh.probe_runtime': probe_runtime,
              'mesh.read_pattern': lambda params: _capture(params, True),
              'mesh.read_scene': lambda params: _capture(params, False),
              'mesh.read_group': read_group}
