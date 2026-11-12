"""One bounded, current-scene Cloth position write through the MeshBox tool."""

import ctypes
from hashlib import file_digest, sha256
import json
from pathlib import Path
from secrets import randbits
import sys

from . import cloth_meshbox_context as meshbox, cloth_vertex_context, common, mesh
from . import cloth_position_recovery as recovery
from .dispatch import _module


_STATE_KEY = '_md_mcp_cloth_numeric_write_pending_v1'
_CALIBRATED_CPT = 'e6ad841c70980090bad94a77be6c7a82aaa01f23d7d2b687eab3d4c939a23f06'
_FOCAL_PER_PIXEL_HEIGHT = 2798.976252631622 / 737


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(asset):
    path = Path(asset['path'])
    _require(path.is_file() and not path.is_symlink() and 0 < asset['byte_count'] <= 32 * 1024 * 1024,
             'A bounded local capture asset is required')
    with path.open('rb') as stream:
        data = stream.read(asset['byte_count'] + 1)
    _require(len(data) == asset['byte_count'] and sha256(data).hexdigest() == asset['sha256'],
             'The current capture bytes differ from the receipt')
    return data


def _export(current):
    reply = mesh.OPERATIONS['mesh.read_scene']({})
    snapshot = reply.get('snapshot') or {}
    _require(reply.get('status') == 'SUCCEEDED' and reply.get('effects_started') is False
             and reply.get('verified') is True and snapshot.get('scene_before_after_exact') is True
             and snapshot.get('scene_token') == current['scene_token']
             and snapshot.get('scene_identity') == current['identity'],
             'A fresh complete world OBJ must bind to the current scene')
    return snapshot


def _summary(snapshot):
    return {
        'capture_id': snapshot['capture_id'], 'topology_fingerprint': snapshot['topology_fingerprint'],
        'arrays_sha256': common.digest({key: snapshot[key] for key in meshbox.MESH_FIELDS}),
        'obj_asset': snapshot['export']['obj_asset'], 'export_options': snapshot['export']['options'],
    }


def _frame_2d(current):
    return dict(meshbox._frame_2d(current['scene_token']), scene_identity=current['identity'])


def _fingerprint(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def _dispatch(function, identity, window, frame, points):
    mapping = frame['view']['mapping']
    width, height = mapping['viewport_size']
    dpr = mapping['device_pixel_ratio']
    output = ctypes.create_string_buffer(1024 * 1024)
    written, needed = ctypes.c_uint32(), ctypes.c_uint32()
    coordinates = [value for key in ('start', 'activation', 'end') for value in points[key]]
    code = function(identity['pid'], identity['creation_filetime'], window, *coordinates, width, height, dpr, 1000,
                    output, len(output), ctypes.byref(written), ctypes.byref(needed))
    _require(0 < written.value < len(output) and needed.value == written.value + 1, 'Incomplete Cloth native receipt')
    raw = json.loads(output.raw[:written.value])
    return raw, meshbox._native_outcome(raw, code, points, viewport_size=(width, height), dpr=dpr, mapping=mapping)


def _select(native, planning, current, window, pending, snapshot, target):
    report = pending['report']
    before = _frame_2d(current)
    asset = snapshot['export']['obj_asset']
    candidate = planning.plan_current_meshbox_box(current, snapshot, target['vertex_ref'], before,
                                                   _read(before['image']), _read(asset),
                                                   sdk_selection=report['mode_during']['sdk_selection'])
    report.update(meshbox_frame=before, selection_plan=candidate['plan'])
    pending['pre_selection_frame'] = True
    report['selection_3d_before'] = meshbox._frame_3d(pending)
    pending['pre_selection_frame'] = False
    latest = _frame_2d(current)
    verified = planning.verify_fresh_box_frame(current, candidate, latest, _read(latest['image']),
                                                sdk_selection=cloth_vertex_context._sdk_selection())
    plan = verified['plan']
    start, end = plan['box_start_logical_px'], plan['box_end_logical_px']
    dispatch = native.select_box(current, window, latest['view']['mapping'], start, end)
    report['selection_dispatch'] = dispatch
    pending['selector_unresolved'] = dispatch.get('requires_recovery') is not False
    _require(dispatch.get('status') == 'EVENTS_DISPATCHED' and not pending['selector_unresolved'],
             'The current vertex selection did not produce a closed input batch')
    after = _frame_2d(current)
    report['meshbox_after_selection'] = after
    readback = _module('cloth_vertex_mapping').verify_meshbox_vertex_marker(
        before, after, _read(before['image']), _read(after['image']), plan)
    report['selection_readback'] = readback
    _require(readback['verified'] is True, 'The current 2D box did not identify one blue vertex')
    report['selection_3d_after'] = meshbox._frame_3d(pending)


def _camera_signature(frame):
    camera = frame['camera']
    return {'cmt_sha256': camera['cmt_sha256'], 'cpt_sha256': camera['cpt_sha256'], 'mapping': frame['view']['mapping']}


def _frame_quarantine_matches(frame, identity, pin):
    state = _module('world_frame')._state()
    process = state.get('process') or {}
    return (state.get('blocked') is True and state.get('busy') is False and state.get('pin') == pin
            and state.get('blocked_frame_receipt_sha256') == common.digest(frame)
            and all(process.get(key) == identity[key] for key in ('pid', 'creation_filetime')))


def run(current, target, tolerance, *, request_id=None, fingerprint=None):
    token, identity = current['scene_token'], current['identity']
    report = {'scene_token': token, 'scene_identity': identity, 'requested_position_verified': False,
              'native_vertex_mapping_verified': False, 'solver_rest_write_verified': False}
    if getattr(sys, _STATE_KEY, None) is not None or meshbox._state()['pending'] is not None:
        return meshbox._response('REJECTED', report, code='CLOTH_MESHBOX_RECOVERY_PENDING',
                                 message='Resolve the original MeshBox request before another Cloth write')
    numeric = _module('cloth_numeric_adapter')
    snapshot = _export(current)
    bound = numeric.bind_position_target(current, snapshot, target)
    report['vertex_binding'] = {key: bound[key] for key in ('pattern_uuid', 'group_vertex_index', 'export_vertex_index',
                                                           'source_xyz_mm', 'requested_target_xyz_mm')}
    if bound['source_xyz_mm'] == bound['requested_target_xyz_mm']:
        report.update(verified=True, requested_position_verified=True, mutation_attempted=False)
        return meshbox._response('NO_OP', report)

    native = _module('cloth_meshbox_native')
    planning = _module('cloth_meshbox_current')
    window = cloth_vertex_context._root_window(identity['pid'])
    native.preflight(current, window)
    function, bridge_sha256 = _module('cloth_vertex_drag_native').load()
    _module('world_frame')._require_stopped_simulation(token)
    initial = meshbox._mode(token, identity)
    selected = initial['sdk_selection']['patterns']
    _require(initial['mode'] == 'MovePattern' and [row['pattern_uuid'] for row in selected] == [bound['pattern_uuid']],
             'Select the current target UUID in MovePattern before requesting its Cloth position')
    path = Path(identity['project_path'])
    source_hash = _fingerprint(path)
    pending = {'scene': current, 'window': window, 'report': report, 'ticket': randbits(62) + 1}
    original_view = meshbox._frame_3d(pending)
    _require(original_view['camera']['cpt_sha256'] == _CALIBRATED_CPT,
             'The current 3D camera needs a qualified focal seed')
    report.update(mode_before=initial, mesh_before=_summary(snapshot), bridge_sha256=bridge_sha256,
                  ticket=str(pending['ticket']))

    setattr(sys, _STATE_KEY, pending)
    try:
        entry = native.transition(current, window, pending['ticket'], 'enter')
    except (ValueError, OSError) as exc:
        setattr(sys, _STATE_KEY, None)
        return meshbox._response('REJECTED', report, code='MESHBOX_ENTRY_PREFLIGHT', message=str(exc))
    try:
        report['entry'] = entry
        if (entry.get('status') == 'REJECTED' and entry.get('sample_transaction_held') is False
                and (entry.get('native') or {}).get('QAction_trigger_calls') == 0):
            setattr(sys, _STATE_KEY, None)
            return meshbox._response('REJECTED', report, code='MESHBOX_ENTRY_REJECTED', message='Entry had zero actions')
        _require(meshbox._native_verified(entry, {'MESHBOX_VERIFIED'}, True), 'MeshBox entry outcome is unresolved')
        post = None
        try:
            active = meshbox._mode(token, identity)
            report.update(mode_during=active, entry_verified=active['mode'] == 'MeshBox')
            _require(report['entry_verified'], 'Readback did not confirm MeshBox/World')
            _select(native, planning, current, window, pending, snapshot, target)
            before, selected_frame = (report[key] for key in ('selection_3d_before', 'selection_3d_after'))
            focal_seed = _FOCAL_PER_PIXEL_HEIGHT * selected_frame['image']['height']
            plan = numeric.plan_position_write(current, snapshot, target, report, _read(before['image']),
                                               _read(selected_frame['image']), focal_seed_px=focal_seed, tolerance_mm=tolerance)
            report['numeric_plan'] = plan
            _require(plan['stage_required'] is True and plan['viewport_points'] is not None,
                     'The requested displacement has no qualified native path')
            live_before = _summary(_export(current))
            _require(live_before['arrays_sha256'] == report['mesh_before']['arrays_sha256'],
                     'The complete cloth mesh changed during selection')
            latest = meshbox._frame_3d(pending)
            numeric.verify_fresh_selected_frame(plan, latest, _read(latest['image']))
            native.check_scope(current, window)
            pending['drag_unresolved'] = True
            raw, dispatched = _dispatch(function, identity, window, latest, plan['viewport_points'])
            report['drag'] = raw
            pending['drag_unresolved'] = False
            _require(dispatched, 'The Cloth drag was rejected before sending any events')
            pending['drag_completed'] = True
            post = _export(current)
            report['position_verification'] = numeric.verify_position_write(current, snapshot, post, plan)
        except Exception as exc:
            report['position_error'] = {'code': getattr(exc, 'code', 'CLOTH_POSITION_NOT_VERIFIED'), 'message': str(exc)}
            if any(pending.get(key) for key in ('frame_unresolved', 'selector_unresolved', 'drag_unresolved', 'drag_completed')):
                raise

        restore = native.transition(current, window, pending['ticket'], 'restore')
        report['restore'] = restore
        _require(meshbox._native_verified(restore, {'MOVE_RESTORED', 'MOVE_ALREADY_OBSERVED'}, False),
                 'MovePattern restoration is unresolved')
        restored_mode = meshbox._mode(token, identity)
        _require(restored_mode['mode'] == 'MovePattern' and restored_mode['sdk_selection'] == initial['sdk_selection'],
                 'The original mode or SDK selection changed')
        restored_frame = meshbox._frame_3d(pending)
        _require(restored_frame['camera'] == original_view['camera'] and restored_frame['view']['mapping'] == original_view['view']['mapping'],
                 'The original 3D camera or viewport mapping changed')
        final = _summary(_export(current))
        expected = report['mesh_before'] if post is None else _summary(post)
        _require(final['arrays_sha256'] == expected['arrays_sha256'] and final['export_options'] == expected['export_options']
                 and final['capture_id'] != expected['capture_id'] and _fingerprint(path) == source_hash,
                 'Final geometry or the saved source changed outside the verified edit')
        report.update(mode_restored=True, transaction_closed=True, source_file_unchanged=True, mesh_after=final)
        if post is None:
            setattr(sys, _STATE_KEY, None)
            error = report['position_error']
            return meshbox._response('NO_OP', report, effects=True, code=error['code'], message=error['message'])
        verification = report['position_verification']
        if expected['arrays_sha256'] == report['mesh_before']['arrays_sha256']:
            setattr(sys, _STATE_KEY, None)
            return meshbox._response('NO_OP', report, effects=True, code='CLOTH_POSITION_UNCHANGED',
                                     message='Closed input and complete OBJ readback show the original mesh')
        _require(verification['verified'] is True and verification['absolute_target_reached'] is True,
                 'The independent full OBJ did not verify the requested absolute position')
        setattr(sys, _STATE_KEY, None)
        report.update(verified=True, requested_position_verified=True)
        return meshbox._response('SUCCEEDED', report, effects=True)
    except Exception as exc:
        failed_frame = pending.get('frame_failure')
        if (pending.get('pre_selection_frame') is True and recovery.hex_string(request_id, 32)
                and recovery.hex_string(fingerprint, 64) and recovery.frame_endpoint_failure(failed_frame, identity)):
            frame_pin = _module('world_frame')._state().get('pin')
            if recovery.hex_string(frame_pin, 64) and _frame_quarantine_matches(failed_frame, identity, frame_pin):
                report['position_recovery'] = {'request_id': request_id, 'fingerprint': fingerprint,
                                               'source_sha256': source_hash, 'camera': _camera_signature(original_view),
                                               'frame_bridge_sha256': frame_pin, 'frame_failure': failed_frame}
        return meshbox._response('UNKNOWN', report, effects=True, recovery=True, code='CLOTH_POSITION_RECOVERY_REQUIRED',
                                 message=str(exc))


def _observe_recovery(current, expected, proof, expected_mode, *, check_mapping):
    token, identity = current['scene_token'], current['identity']
    mode = meshbox._mode(token, identity)
    _require(mode['mode'] == expected_mode and mode['sdk_selection'] == expected['mode_before']['sdk_selection']
             and mode['physical_state_unchanged'] is True, 'The original Cloth mode and selection must be verified')
    camera = _module('world_adapter').read_camera({'scene_token': token})
    _require(camera['status'] == 'SUCCEEDED' and camera['effects_started'] is False
             and camera['requires_recovery'] is False and camera['result']['scene_token'] == token,
             'A verified read-only camera observation is required')
    signature = _camera_signature(camera['result'])
    keys = ('cmt_sha256', 'cpt_sha256', 'mapping') if check_mapping else ('cmt_sha256', 'cpt_sha256')
    _require(all(signature[key] == proof['camera'][key] for key in keys), 'The original camera or viewport changed')
    fresh = _summary(_export(current))
    before = expected['mesh_before']
    _require(fresh['capture_id'] != before['capture_id'] and all(fresh[key] == before[key] for key in
             ('arrays_sha256', 'export_options', 'topology_fingerprint')),
             'A fresh complete OBJ must equal the original Cloth geometry')
    _require(_fingerprint(Path(identity['project_path'])) == proof['source_sha256'], 'The original saved project changed')
    return mode, fresh, signature


def recover(params):
    """Restore only the original verified mode entry that never selected or dragged a vertex."""
    from . import dispatch

    token, parent_id = params['scene_token'], params['blocked_request_id']
    result = {'blocked_request_id': parent_id, 'scene_token': token}
    attempted = False
    pending = getattr(sys, _STATE_KEY, None)
    try:
        parent = dispatch._RECEIPTS.get(parent_id) or {}
        fingerprint = dispatch._PAYLOADS.get(parent_id)
        _require(recovery.parent_witness(parent, parent_id, fingerprint, token), 'The original Cloth receipt cannot be recovered')
        original = parent['result']
        proof = original['position_recovery']
        current = common.check_scene_token(token)
        identity = current['identity']
        _require(pending is not None and pending['scene'] == current and pending['report'] is original
                 and identity == original['scene_identity'] and str(pending['ticket']) == original['ticket']
                 and pending.get('pre_selection_frame') is True and pending.get('frame_unresolved') is True
                 and pending.get('frame_failure') == proof['frame_failure']
                 and not any(pending.get(key) for key in ('selector_unresolved', 'drag_unresolved', 'drag_completed'))
                 and _frame_quarantine_matches(proof['frame_failure'], identity, proof['frame_bridge_sha256']),
                 'The original pending Cloth and frame transactions must match')
        native = _module('cloth_meshbox_native')
        scope = native.check_scope(current, pending['window'])
        state = native._state(scope)
        _module('world_frame')._require_stopped_simulation(token)
        restored = pending.get('verified_mode_restore')
        if restored is None:
            _require(not pending.get('mode_restore_attempted') and state['transaction'] == {
                'binding': (scope, pending['ticket']), 'entry_verified': True,
                'selector_attempted': False, 'selector_unresolved': False},
                'Only the original verified ticket with no selector and no previous restore may be inverted')
            _, before, _ = _observe_recovery(current, original, proof, 'MeshBox', check_mapping=False)
            pending['mode_restore_attempted'] = True
            attempted = True
            restored = native.transition(current, pending['window'], pending['ticket'], 'restore')
            result['restore'] = restored
            _require(recovery.closed_inverse(restored, original), 'The original MeshBox inverse could not be verified')
            pending['verified_mode_restore'] = restored
            pending['recovery_before_capture'] = before['capture_id']
        result['restore'] = restored
        _require(state['transaction'] is None, 'The original MeshBox native ticket remains held')
        mode, final, camera = _observe_recovery(current, original, proof, 'MovePattern', check_mapping=True)
        _require(final['capture_id'] != pending['recovery_before_capture']
                 and _frame_quarantine_matches(proof['frame_failure'], identity, proof['frame_bridge_sha256']),
                 'The complete OBJ readback or frame quarantine differs')
        result.update(verified=True, scene_identity=identity, recovery_action=recovery.RESTORED,
                      original_mutation_status='UNKNOWN', mode_after=mode, mesh_after=final, camera_after=camera,
                      source_file_unchanged=True, frame_quarantined=True, transaction_closed=True)
        setattr(sys, _STATE_KEY, None)
        return meshbox._response('SUCCEEDED', result, effects=attempted)
    except Exception as exc:
        needs_recovery = attempted or (pending or {}).get('mode_restore_attempted') is True
        return meshbox._response('UNKNOWN' if needs_recovery else 'REJECTED', result, effects=attempted,
                                 recovery=needs_recovery, code='CLOTH_POSITION_RECOVERY_NOT_VERIFIED', message=str(exc))
