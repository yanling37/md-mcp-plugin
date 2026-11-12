"""Observe one closed public rotation without sending another model operation."""

from contextvars import ContextVar
from importlib import import_module
import math
from pathlib import Path

from . import mesh, world_adapter as world, world_frame as frames
from .common import check_scene_token, digest


OPERATION = 'world_adapter.reconcile_rotation'
OBSERVED = 'WORLD_ROTATION_OBSERVED'
SCHEMA = 'md-world-rotation-observation/1'
HOLD_KEY = 'rotation_recovery_hold'
active_rotation = ContextVar('md_active_world_rotation', default=None)


def call_bound(function, params, request_id, fingerprint, token):
    active = active_rotation.set({'parent_request_id': request_id, 'parent_fingerprint': fingerprint,
                                  'scene_token': token})
    try:
        return function(params)
    finally:
        active_rotation.reset(active)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def hold_key(evidence):
    return {'parent_request_id': evidence['parent_request_id'], 'evidence_sha256': digest(evidence)}


def _arrays(snapshot):
    return digest({key: snapshot[key] for key in frames.MESH_FIELDS})


def _baseline(evidence):
    summary = evidence['before_mesh']
    expected = summary['export']['obj_asset']
    raw, actual = mesh._asset(Path(expected['path']))
    _require(actual == expected, 'The original rotation OBJ asset changed')
    parsed = mesh.parse_obj(raw)
    inventory = mesh.group_inventory(parsed)
    _require(_arrays(parsed) == evidence['before_world_arrays_sha256']
             and parsed['topology_fingerprint'] == summary['topology_fingerprint']
             and parsed['vertex_count'] == summary['vertex_count'] and inventory == summary['group_inventory'],
             'The retained OBJ differs from its original world geometry receipt')
    # Rebuild a comparison snapshot with its own content ID from the retained
    # bytes. The source receipt's capture ID still identifies the original read.
    snapshot = dict(parsed, schema='md-exported-obj-mesh-v1', scope='scene_garment_OBJ_export', pattern_uuid=None,
                    capture_id=summary['capture_id'], scene_token=summary['scene_token'],
                    scene_identity=summary['scene_identity'], space='world', unit='mm', axis_order=['X', 'Y', 'Z'],
                    group_inventory=inventory, export=summary['export'])
    snapshot['snapshot_id'] = mesh._hash(snapshot)
    return snapshot


def _closed_views(current):
    from .dispatch import _module

    _module('native_setup').configure_canvas()
    reply = _module('canvas').views({'view': 'all', 'budget_ms': 1500})
    result = reply.get('result') or {}
    views = result.get('views') or []
    _require(reply['status'] == 'SUCCEEDED' and reply['effects_started'] is False
             and result.get('scene_token') == current['scene_token']
             and result.get('physical', {}).get('background_safe') is True
             and len(views) == 2 and {view['view'] for view in views} == {'2d', '3d'}
             and all(view.get('scene_item_grabber_absent') is True for view in views),
             'The original scene must have idle background 2D and 3D views')
    frames._require_stopped_simulation(current['scene_token'])


def _companion(evidence, current, fresh):
    from .dispatch import _module

    companion = evidence['companion_preflight']
    refresh = evidence['refresh_outcome']
    if companion is None:
        _require(refresh is None, 'An unexpected Topstitch step was recorded')
        return (), None
    binding = companion['assignment']
    original = companion['original_offset_sdk']
    _require(type(original) is float and math.isfinite(original), 'The pre-rotation offset is missing')
    if refresh is not None:
        child = refresh.get('result') or {}
        no_write = (refresh.get('status') == 'REJECTED' and refresh.get('effects_started') is False
                    and refresh.get('requires_recovery') is False and type(child.get('sdk_set_calls')) is int
                    and child['sdk_set_calls'] == 0)
        closed = (refresh.get('status') in ('SUCCEEDED', 'FAILED') and refresh.get('effects_started') is True
                  and refresh.get('requires_recovery') is False and child.get('transaction_closed') is True
                  and child.get('offset_restored') is True and child.get('assignment') == binding
                  and child.get('original_offset_sdk') == original and type(child.get('sdk_set_calls')) is int
                  and child['sdk_set_calls'] in (1, 2))
        _require(no_write or closed, 'The original Topstitch offset action did not close')
    api = import_module('pattern_api')
    native = _module('world_assigned_topstitch')
    _require(native.bind(current, evidence['pattern_uuid'], fresh, api=api) == binding,
             'The original Texture Topstitch owner or style changed')
    offset = _module('topstitch_offset_rebuild')
    getter = offset._methods(api)[0]
    _require(getter is not None, 'The original Topstitch offset cannot be read')
    value = getter(binding['pattern_index'], binding['assigned_index'])
    _require(type(value) is float and value == original, 'The current Topstitch offset differs from its pre-rotation value')
    return (binding['exported_group'],), value


def reconcile(params):
    from . import dispatch, transforms

    try:
        blocked = params['blocked_request_id']
        token = params['scene_token']
        original = params['original_params']
        _require(set(params) == {'blocked_request_id', 'scene_token', 'original_params'} and type(original) is dict,
                 'Expected the original journaled rotation parameters')
        prior = dispatch._RECEIPTS[blocked]
        evidence = prior['result']['rotation_recovery']
        fingerprint = digest({'operation': 'transforms.rotate', 'params': original, 'scene_token': token})
        owner = hold_key(evidence)
        state = world._state()
        _require(prior['operation_id'] == blocked and prior['status'] in ('UNKNOWN', 'PARTIAL')
                 and prior['effects_started'] is True and prior['requires_recovery'] is True
                 and evidence['schema'] == SCHEMA and evidence['parent_request_id'] == blocked
                 and fingerprint == evidence['parent_fingerprint'] == dispatch._PAYLOADS[blocked]
                 and evidence['scene_token'] == original['scene_token'] == token
                 and state.get('blocked') is True and state.get(HOLD_KEY) == owner and not state.get('in_call'),
                 'Only the matching held world rotation can be observed')
        native, request = evidence['native'], evidence['native_request']
        target, name = evidence['pattern_uuid'], evidence['group_name']
        plan = original['ring_plan']
        _require(target == original['pattern_uuid'] and request['operation'] == 'gizmo_drag'
                 and request['purpose'] == 'rotation_ring' and native['status'] == 'SUCCEEDED'
                 and all(request[key] == world._xy(plan[key]) for key in ('start', 'activation', 'end')),
                 'The original five-event ring dispatch does not match the held request')
        world._validate_raw(native, 0, request)

        current = check_scene_token(token)
        baseline = _baseline(evidence)
        _require(baseline['scene_identity'] == current['identity'] == evidence['scene_identity']
                 and baseline['scene_token'] == token
                 and all(state['process'][key] == current['identity'][key] for key in ('pid', 'creation_filetime')),
                 'The original MD process, scene or exported baseline changed')
        _closed_views(current)
        selected = world._selection(current, target)
        _require(selected == evidence['selection'], 'The original selected UUID or SDK selection changed')
        first = world._mesh_read()
        companions, offset = _companion(evidence, current, first)
        _require(bool(companions) is original.get('include_assigned_topstitch', False),
                 'The current assigned Topstitch scope differs from the original request')
        second = world._mesh_read()
        _require(len({baseline['capture_id'], first['capture_id'], second['capture_id']}) == 3
                 and all(snapshot['scene_identity'] == current['identity'] and snapshot['scene_token'] == token
                         for snapshot in (first, second))
                 and all(first[key] == second[key] for key in frames.MESH_FIELDS)
                 and _companion(evidence, current, second) == (companions, offset),
                 'Two fresh complete current exports and assignment readbacks must agree')
        _closed_views(current)
        _require(check_scene_token(token) == current and world._selection(current, target) == selected
                 and state.get('blocked') is True and state.get(HOLD_KEY) == owner and not state.get('in_call'),
                 'The current scene, selection or held request changed during observation')

        matrix = transforms.rotation_matrix(original['axis_world'], original['angle_degrees'], original['pivot_world_mm'])
        tolerance = original['tolerance_mm']
        profile = original.get('verification_profile', 'strict')
        _require(profile in ('strict', 'positions'), 'The original verification profile is unavailable')
        proof = world.verify_rigid_transform_readback(current, current, baseline, second, target, name, matrix, tolerance,
                                                       companion_groups=companions)
        measurement = proof['observed']
        _require(all(proof.get(key) is True for key in ('same_scene', 'distinct_captures', 'stable_label_bijection',
                    'normal_readback_complete', 'uv_readback_complete', 'outside_target_export_arrays_exact'))
                 and all(measurement.get(key) is True for key in ('topology_and_index_maps_exact', 'uv_exact',
                                                                  'other_export_groups_all_exact')),
                 'Complete topology, UV, labels and non-target geometry are not preserved')
        matches = proof['exported_geometry_matches_requested_matrix'] if profile == 'strict' else proof['exported_positions_match_requested_matrix']
        current_sha = _arrays(second)
        unchanged = current_sha == evidence['before_world_arrays_sha256']
        _require(matches is True or unchanged or current_sha == evidence['closed_terminal_arrays_sha256'],
                 'The observed mismatch differs from the previously closed final geometry')
        result = {'verified': True, 'recovery_action': OBSERVED, 'recovered_operation_id': blocked,
                  'original_mutation_status': prior['status'], 'parent_fingerprint': fingerprint,
                  'evidence_sha256': owner['evidence_sha256'], 'scene_token': token, 'scene_identity': current['identity'],
                  'observation': 'REQUESTED' if matches else 'UNCHANGED' if unchanged else 'MISMATCH',
                  'requested_matrix_observed': matches, 'current_world_arrays_sha256': current_sha,
                  'capture_ids': [first['capture_id'], second['capture_id']], 'topstitch_original_offset_sdk': offset,
                  'matrix_acceptance': proof, 'native_input_calls': 0, 'sdk_setter_calls': 0}
        state['blocked'] = False
        del state[HOLD_KEY]
        return {'status': 'SUCCEEDED', 'result': result, 'error': None,
                'effects_started': False, 'requires_recovery': False}
    except Exception as error:
        return {'status': 'REJECTED', 'result': None,
                'error': {'code': 'WORLD_ROTATION_RECONCILE_REJECTED', 'message': str(error)[:500]},
                'effects_started': False, 'requires_recovery': False}


OPERATIONS = {OPERATION: reconcile}
