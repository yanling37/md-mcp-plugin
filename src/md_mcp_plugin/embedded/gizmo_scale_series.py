"""Observe two fixed scale increments and restore each captured display state."""

from functools import partial
import math
from pathlib import Path

from . import gizmo_scale as scale
from . import mesh, world_frame
from .common import digest, scene


def _settled_counts(receipt, ticket, increases, decreases):
    state = receipt.get('pair_state') or {}
    return (state.get('transaction_id') == ticket and state.get('native_phases_settled') is True
            and all(state.get(f'{direction}_{stage}') == count for direction, count in
                    (('increase', increases), ('decrease', decreases)) for stage in ('attempts', 'returns', 'verified')))


def _counted(receipt, ticket, increases, decreases, *, closed=False):
    return (receipt.get('verified') is True and receipt.get('pair_pending') is (not closed)
            and _settled_counts(receipt, ticket, increases, decreases))


def closed_rotation_scale(result, ticket):
    try:
        scale_result = result['gizmo_scale']
        increases = len(scale_result['increases'])
        return (scale_result['schema'] == 'md-gizmo-scaled-rotation/2' and increases in (1, 2)
                and scale_result['transaction_closed'] is scale_result['display_scale_restoration_verified'] is True
                and _counted(scale_result['close'], ticket, increases, increases, closed=True)
                and len(scale_result['restored_frames']) == len(scale_result['decreases']) == increases
                and all(_counted(row, ticket, count, 0) for count, row in enumerate(scale_result['increases'], 1))
                and all(_counted(row, ticket, increases, count) for count, row in enumerate(scale_result['decreases'], 1)))
    except (KeyError, TypeError, AttributeError):
        return False


def _observe(current, roi=None, *, capture):
    evidence, decoded = capture(current)
    size = evidence['frame']['image']['viewport_size']
    bounds = [0, 0, *size] if roi is None else roi
    observation = scale._red_observation(evidence, decoded, bounds)
    return dict(evidence, red=scale._qualified_red(observation), roi_px=bounds)


def _ratio(before, after):
    a, b = before['red'], after['red']
    if a is None or b is None:
        return None
    ratios = [last / first for first, last in zip(a['ellipse_semiaxes_px'], b['ellipse_semiaxes_px'], strict=True)]
    if min(ratios) >= 1.1 and max(ratios) <= 2.5 and max(ratios) / min(ratios) <= 1.1 and math.dist(
            a['ellipse_center_px'], b['ellipse_center_px']) <= 8.:
        return ratios
    return None


def _display_restoration(expected, actual):
    if not scale._same_frame_scope(expected, actual):
        return None
    if expected['pixels_sha256'] == actual['pixels_sha256']:
        return {'exact_pixels': True, 'outside_red_ring_noise_pixels': 0}
    red = expected['red']
    if red is None:
        return None
    b = scale.decode_png(scale._frame_bytes(actual['frame']['image']))
    observed = scale._qualified_red(scale._red_observation(actual, b, expected['roi_px']))
    if observed != red:
        return None
    a = scale.decode_png(scale._frame_bytes(expected['frame']['image']))
    if (a['width'], a['height']) != (b['width'], b['height']):
        return None
    left, top, right, bottom = red['support_pixel_bounds_xyxy_inclusive']
    first, last = memoryview(a['pixels']).cast('I'), memoryview(b['pixels']).cast('I')
    count = 0
    for index, (before, after) in enumerate(zip(first, last, strict=True)):
        if before == after:
            continue
        y, x = divmod(index, a['width'])
        count += 1
        if (count > 16 or left - 2 <= x <= right + 2 and top - 2 <= y <= bottom + 2
                or (before >> 24) != (after >> 24)
                or any(abs(((before >> shift) & 255) - ((after >> shift) & 255)) > 1 for shift in (0, 8, 16))):
            return None
    return {'exact_pixels': False, 'outside_red_ring_noise_pixels': count}


def _restore(current, result, ticket, view, *, capture):
    increases = len(result['increases'])
    for count in range(len(result['decreases']) + 1, increases + 1):
        expected = result['display_frames'][increases - count]
        decreased = scale._native('decrease', transaction=ticket, view=view, abi=2)
        result['decreases'].append(decreased)
        scale._require(_counted(decreased, ticket, increases, count), 'A verified decrease did not return for the original series')
        restored, _ = capture(current)
        result['restored_frames'].append(restored)
        comparison = _display_restoration(expected, restored)
        scale._require(comparison is not None, 'Intermediate ring, geometry or view did not restore')
        result.setdefault('display_pixel_comparisons', []).append(comparison)
    final_scene = scene()
    scale._require(final_scene == current, 'The original scene scope changed')
    _close(result, ticket, view, increases)


def _close(result, ticket, view, increases):
    closed = result['close'] = scale._native('close', transaction=ticket, view=view, abi=2)
    scale._require(_counted(closed, ticket, increases, increases, closed=True), 'Native scale series could not close')
    result.update(verified=True, transaction_closed=True, display_scale_restoration_verified=True)


def prove(params, operation_id):
    result = {'schema': 'md-gizmo-scale-series/2', 'series_growth_verified': False,
              'transaction_closed': False, 'display_scale_restoration_verified': False,
              'display_frames': [], 'increases': [], 'decreases': [], 'restored_frames': [], 'observed_radius_ratios': []}
    effects = False
    reversible = True
    try:
        keys = {'scene_token', 'roi_px', 'increase_steps', 'pattern_uuid'}
        scale._require(params.keys() == keys and params['increase_steps'] == 2 and type(params['pattern_uuid']) is str
                       and bool(params['pattern_uuid']), 'Use two increments with a current scene and selected pattern UUID')
        target = params['pattern_uuid']
        current = scale.check_scene_token(params['scene_token'])
        capture = partial(scale._capture_frame, pattern_uuid=target)
        baseline = _observe(current, params['roi_px'], capture=capture)
        result['display_frames'].append(baseline)
        prepared = result['native_inspection'] = scale._native('inspect', abi=2)
        view = prepared['before']
        scale._require(baseline['red'] is not None and prepared['verified'] is True and prepared['pair_pending'] is False
                       and prepared.get('pair_state') is None
                       and all(prepared[name]['identified'] is prepared[name]['enabled'] is True
                               for name in ('increase_action', 'decrease_action'))
                       and view['mapping_fingerprint'] == baseline['frame']['view']['mapping_fingerprint'],
                       'The baseline red ring or native action scope is unverified')
        for step in (1, 2):
            reversible = False
            increased = scale._native('increase', transaction=operation_id, view=view, abi=2)
            if increased['status'] == 'REJECTED' and increased['effects_started'] is False:
                reversible = (increased['pair_pending'] is False if step == 1 else
                              increased['pair_pending'] is True and _settled_counts(increased, operation_id, step - 1, 0))
                scale._require(False, 'Native scope rejected the next increase')
            effects = True
            result['increases'].append(increased)
            scale._require(_counted(increased, operation_id, step, 0), 'A verified increase did not return for the original series')
            reversible = True
            previous = result['display_frames'][-1]
            enlarged = _observe(current, capture=capture)
            result['display_frames'].append(enlarged)
            scope_matches = scale._same_frame_scope(previous, enlarged)
            if not scope_matches:
                reversible = False
            scale._require(scope_matches, 'A capture differed in full world geometry, camera, selection or viewport')
            ratios = _ratio(previous, enlarged)
            scale._require(ratios is not None and enlarged['pixels_sha256'] != previous['pixels_sha256'],
                           'The viewport did not provide a qualified enlarged red contour')
            result['observed_radius_ratios'].append(ratios)
        result['series_growth_verified'] = True
    except Exception as exc:
        result['observation_error'] = str(exc)[:500]
    if not reversible:
        effects = True
    elif effects:
        try:
            _restore(current, result, operation_id, view, capture=capture)
        except Exception as exc:
            result['restore_error'] = str(exc)[:500]
    if result['transaction_closed']:
        return {'status': 'SUCCEEDED', 'result': result, 'effects_started': True, 'requires_recovery': False, 'error': None}
    return {'status': 'UNKNOWN' if effects else 'REJECTED', 'result': result, 'effects_started': effects,
            'requires_recovery': effects, 'error': {'code': 'GIZMO_SCALE_SERIES_UNVERIFIED',
                                                   'message': result.get('restore_error') or result.get('observation_error', '')}}


def pending_series(prior, ticket, token):
    try:
        increases, decreases = prior['increases'], prior['decreases']
        return (prior['schema'] == 'md-gizmo-scale-series/2' and prior['transaction_closed'] is False
                and 'close' not in prior and 1 <= len(increases) <= 2 and len(decreases) <= len(increases)
                and all(_counted(row, ticket, step, 0) for step, row in enumerate(increases, 1))
                and all(_counted(row, ticket, len(increases), step) for step, row in enumerate(decreases, 1))
                and len(prior['display_frames']) > len(increases) - len(decreases)
                and prior['display_frames'][0]['frame']['scene_token'] == token)
    except (KeyError, TypeError, AttributeError):
        return False


def recover(params, operation_id, original):
    from . import dispatch

    ticket, token = params['blocked_request_id'], params['scene_token']
    result = {'schema': 'md-gizmo-scale-series-recovery/2', 'blocked_request_id': ticket,
              'transaction_closed': False, 'display_scale_restoration_verified': False}
    effects = False
    try:
        scale._require(set(params) == {'blocked_request_id', 'scene_token'} and operation_id != ticket
                       and original['status'] == 'UNKNOWN' and original['operation_id'] == ticket
                       and original['effects_started'] is original['requires_recovery'] is True,
                       'Require the original unresolved scale series')
        for record in dispatch._RECEIPTS.values():
            previous = record.get('result') or {}
            scale._require(not (record.get('effects_started') is True and previous.get('schema') == result['schema']
                                and previous.get('blocked_request_id') == ticket), 'The original series already has a recovery attempt')
        prior = original['result']
        scale._require(pending_series(prior, ticket, token), 'The original series lacks settled native phases')
        target = prior['display_frames'][0]['frame']['selection'][0]['pattern_uuid']
        original_params = {'scene_token': token, 'roi_px': prior['display_frames'][0]['roi_px'],
                           'increase_steps': 2, 'pattern_uuid': target}
        expected = digest({'operation': 'gizmo_scale.prove_pair', 'scene_token': token, 'params': original_params})
        scale._require(dispatch._PAYLOADS.get(ticket) == expected, 'The original series is not bound to these arguments')
        current = scale.check_scene_token(token)
        capture = partial(scale._capture_frame, pattern_uuid=target)
        view = prior['native_inspection']['before']
        prepared = result['native_inspection'] = scale._native('inspect', abi=2)
        increases, decreases = len(prior['increases']), len(prior['decreases'])
        scale._require(_counted(prepared, ticket, increases, decreases) and prepared['before'] == view,
                       'The pending native counters or view differ from the original series')
        fresh, _ = capture(current)
        comparison = _display_restoration(prior['display_frames'][increases - decreases], fresh)
        scale._require(comparison is not None, 'The current display does not match the exact settled phase')
        result.update(increases=prior['increases'], decreases=list(prior['decreases']), display_frames=prior['display_frames'],
                      restored_frames=list(prior['restored_frames']), display_pixel_comparisons=[comparison])
        effects = True
        _restore(current, result, ticket, view, capture=capture)
        result['reconciles_request_id'] = ticket
        return {'status': 'SUCCEEDED', 'result': result, 'effects_started': True, 'requires_recovery': False, 'error': None}
    except Exception as exc:
        return {'status': 'UNKNOWN' if effects else 'REJECTED', 'result': result, 'effects_started': effects,
                'requires_recovery': effects, 'error': {'code': 'GIZMO_SCALE_SERIES_RECOVERY_UNVERIFIED', 'message': str(exc)[:500]}}


def _after_geometry(payload):
    summary = payload['after_mesh']
    expected = summary['export']['obj_asset']
    raw, asset = mesh._asset(Path(expected['path']))
    scale._require(asset == expected, 'The retained post-rotation OBJ changed')
    parsed = mesh.parse_obj(raw)
    return digest({key: parsed[key] for key in world_frame.MESH_FIELDS})


def _restore_rotated(current, result, ticket, view, capture, geometry):
    enlarged = _observe(current, capture=capture)
    scope = result['display_frames'][-1]
    scope = {**scope, 'world_arrays_sha256': geometry}
    scale._require(scale._same_frame_scope(scope, enlarged) and enlarged['red'] is not None,
                   'The enlarged post-rotation frame does not bind the closed matrix readback')
    result['rotated_enlarged_frame'] = enlarged
    for count in (1, 2):
        index = 2 - count
        decreased = scale._native('decrease', transaction=ticket, view=view, abi=2)
        result['decreases'].append(decreased)
        scale._require(_counted(decreased, ticket, 2, count), 'The post-rotation decrease was not verified')
        reduced = _observe(current, capture=capture)
        result['restored_frames'].append(reduced)
        ratios = _ratio(reduced, enlarged)
        expected_ratios = result['observed_radius_ratios'][index]
        expected = result['display_frames'][index]['red']['ellipse_semiaxes_px']
        scale._require(scale._same_frame_scope(scope, reduced) and ratios is not None
                       and all(math.isclose(actual, prior, rel_tol=.04) for actual, prior in zip(ratios, expected_ratios, strict=True))
                       and all(math.isclose(actual, prior, rel_tol=.04) for actual, prior in
                               zip(reduced['red']['ellipse_semiaxes_px'], expected, strict=True)),
                       'Observed display scale or complete rotated geometry did not restore')
        enlarged = reduced
    scale._require(scene() == current, 'The original scene scope changed')
    _close(result, ticket, view, 2)


def rotate_scaled(target, scene_token, plan, ticket, rotate):
    """Qualify a fresh enlarged ring, run one closed rotation, and restore scale."""
    result = {'schema': 'md-gizmo-scaled-rotation/2', 'transaction_closed': False,
              'display_scale_restoration_verified': False, 'display_frames': [], 'increases': [],
              'decreases': [], 'restored_frames': [], 'observed_radius_ratios': []}
    effects = False
    reversible = True
    reply = None
    try:
        from .native_setup import configure_gizmo_scale

        retained, _ = world_frame._read_retained_frame(plan['frame_request_id'], scene_token)
        current = scale.check_scene_token(scene_token)
        capture = partial(scale._capture_frame, pattern_uuid=target)
        baseline = _observe(current, plan['roi_px'], capture=capture)
        result['display_frames'].append(baseline)
        frame = baseline['frame']
        scale._require(frame['camera'] == retained['camera'] and frame['view'] == retained['view']
                       and frame['selection'] == retained['selection']
                       and baseline['world_arrays_sha256'] == retained['mesh_proof']['world_arrays_sha256']
                       and baseline['red'] is not None, 'The current scene, camera, viewport or red ring changed')
        configure_gizmo_scale()
        prepared = result['native_inspection'] = scale._native('inspect', abi=2)
        view = prepared['before']
        scale._require(prepared['verified'] is True and prepared['pair_pending'] is False
                       and prepared.get('pair_state') is None and all(prepared[name]['identified'] is prepared[name]['enabled'] is True
                            for name in ('increase_action', 'decrease_action'))
                       and view['mapping_fingerprint'] == frame['view']['mapping_fingerprint'],
                       'The scale actions are unavailable in the qualified view')
        for step in (1, 2):
            reversible = False
            increase = scale._native('increase', transaction=ticket, view=view, abi=2)
            if increase['status'] == 'REJECTED' and increase['effects_started'] is False:
                reversible = (increase['pair_pending'] is False if step == 1 else
                              increase['pair_pending'] is True and _settled_counts(increase, ticket, step - 1, 0))
                scale._require(False, 'Native scope rejected the next increase')
            effects = True
            result['increases'].append(increase)
            scale._require(_counted(increase, ticket, step, 0), 'A verified scale increase did not return')
            reversible = True
            previous = result['display_frames'][-1]
            enlarged = _observe(current, capture=capture)
            result['display_frames'].append(enlarged)
            reversible = scale._same_frame_scope(previous, enlarged)
            scale._require(reversible, 'The view or complete geometry changed across the scale action')
            ratios = _ratio(previous, enlarged)
            scale._require(ratios is not None, 'The enlarged red contour could not be qualified')
            result['observed_radius_ratios'].append(ratios)
        reversible = False
        reply = rotate(enlarged['frame'], world_frame._verified_png(enlarged['frame']))
        if reply['effects_started'] is False and reply['requires_recovery'] is False:
            reversible = True
        elif reply['status'] in ('SUCCEEDED', 'FAILED') and reply['requires_recovery'] is False:
            geometry = _after_geometry(reply['result'])
            _restore_rotated(current, result, ticket, view, capture, geometry)
    except Exception as exc:
        result['error'] = str(exc)[:500]
    if effects and reversible and not result['transaction_closed']:
        try:
            _restore(current, result, ticket, view, capture=capture)
        except Exception as exc:
            result['error'] = str(exc)[:500]
    if result['transaction_closed']:
        if reply is not None and reply['effects_started'] is True:
            return {**reply, 'result': {**reply['result'], 'gizmo_scale': result}}
        return {'status': 'REJECTED', 'result': {'gizmo_scale': result, 'geometry_write_attempted': False},
                'effects_started': True, 'requires_recovery': False,
                'error': (reply or {}).get('error') or {'code': 'SCALED_RING_PRECHECK_FAILED', 'message': result.get('error', '')}}
    effects = effects or not reversible
    return {'status': 'UNKNOWN' if effects else 'REJECTED', 'result': {'gizmo_scale': result, 'rotation_reply': reply},
            'effects_started': effects, 'requires_recovery': effects,
            'error': {'code': 'SCALED_RING_UNVERIFIED', 'message': result.get('error', 'The scaled rotation could not be closed')}}
