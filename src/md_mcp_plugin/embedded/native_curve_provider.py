"""Native 2D handle writer using fresh semantic/raster binding and measured readback."""
import math
from pathlib import Path

from . import curves, selection
from .curve_projection import solve_projection

_ROLE_KEYS = ('pattern_uuid', 'shape_kind', 'shape_key', 'line_key', 'handle_key', 'role')


def _resolve(snapshot, request):
    if snapshot['scope'] != request['scene_scope']:
        raise ValueError('Project/MD scope changed')
    analysis = curves._analyse(snapshot['native'], request['pattern_uuid'], snapshot['scope'])
    matches = [(ref, row) for ref, row in analysis['targets'].items()
               if all(row['identity'].get(k) == request['reference'].get(k) for k in _ROLE_KEYS)]
    if len(matches) != 1 or not matches[0][1]['independent']:
        raise ValueError('Current handle role does not resolve independently')
    return analysis, matches[0][0], matches[0][1]


def _scope(inspected):
    view = inspected['result']['after']
    return {'view_ref': view['view_ref'], 'view_fingerprint': view['view_fingerprint'],
            'scene_token': inspected['result']['scene_token']}


def set_handle(request):
    """One request, at most three known-complete measured correction steps.

    Preparation uses separate SDK-selection/mode/fit tools. R4 then refreshes
    selection and temporarily zooms for measured precision, restoring Fit
    Selected afterward. No version repeats an unknown dispatch.
    """
    from .native_setup import get_scene_adapter
    scene_adapter = get_scene_adapter()
    if hasattr(scene_adapter, 'bezier_drag_precise_unsnapped'):
        from .dispatch import _module
        return _module('native_curve_precision').set_handle(request, scene_adapter)
    trace, dispatched = [], False
    def reply(status, code=None, message=None, recovery=False, **details):
        return {'status': status, 'effects_started': dispatched, 'requires_recovery': recovery,
                'error': {'code': code, 'message': message} if code else None,
                'closed_input_batch_verified': bool(dispatched and not recovery), 'trace': trace,
                'verification_scope': 'native 2D handle/derived data and closed Qt input; 3D/solver Rest not certified', **details}
    def native(name, result):
        payload = result.get('result') or {}
        trace.append({'operation': name, 'status': result['status'], 'error': result.get('error'),
                      'event_calls': payload.get('event_calls', 0), 'requires_recovery': result.get('requires_recovery', False),
                      'paired_release_verified': payload.get('paired_release_verified', False)})
        return result['status'] == 'SUCCEEDED'
    try:
        selected = selection.inspect_selection({})['selected']
        if [p['pattern_uuid'] for p in selected] != [request['pattern_uuid']]:
            return reply('UNSUPPORTED', 'PATTERN_SELECTION_REQUIRED', 'Call md_select_pattern for this UUID before writing')
        initial = curves._read_current(request['pattern_uuid'])
        analysis, ref, target = _resolve(initial, request)
        if target['identity']['shape_kind'] != 'outer':
            return reply('UNSUPPORTED', 'OUTER_HANDLE_REQUIRED', 'Current raster binding supports independently referenced outer cubic handles')
        plan = curves.prepare_edit(initial['native'], request['pattern_uuid'], ref, request['target_xy_mm'],
                                   tolerance_mm=request['tolerance_mm'], scene_scope=initial['scope'])
        current = initial
        previous_error = math.dist(plan['current_xy_mm'], plan['target_xy_mm'])
        for step in range(3):
            if step:
                refreshed = selection.select_pattern({'pattern_uuid':request['pattern_uuid'],'refresh':True})
                if not native('refresh_pattern_selection', refreshed):
                    return reply('PARTIAL', 'PATTERN_REFRESH_FAILED', 'Whole-pattern display selection did not restore', recovery=True)
            inspected = scene_adapter.inspect_2d({})
            if not native('inspect', inspected):
                return reply(inspected['status'], 'CURVE_VIEW_UNAVAILABLE', str(inspected.get('error')), recovery=dispatched or inspected.get('requires_recovery', False))
            if inspected['result']['tools_after'].get('current_tool') != 'edit_curve_point':
                return reply('UNSUPPORTED' if not dispatched else 'PARTIAL', 'CURVE_MODE_REQUIRED', 'Call scene_adapter_set_tool(edit_curve_point)', recovery=dispatched)
            captured = scene_adapter.capture_2d(_scope(inspected))
            if not native('capture', captured):
                return reply(captured['status'], 'CURVE_CAPTURE_UNAVAILABLE', str(captured.get('error')), recovery=dispatched or captured.get('requires_recovery', False))
            analysis, _, target = _resolve(current, request)
            anchors = [{'id': ident, 'xy_mm': xy} for ident, xy in analysis['outer_endpoints'].items()]
            handles = [{'id': h['handle_uuid'], 'xy_mm': h['xy_mm']} for c in analysis['public']['curves']
                       if c['shape_kind'] == 'outer' for h in c['handles']]
            image = captured['result']['image']
            projection = solve_projection(Path(image['path']).read_bytes(), anchors, handles,
                                          target['native_ids']['handle_uuid'], device_pixel_ratio=image['device_pixel_ratio'])
            delta = [a - b for a, b in zip(plan['target_xy_mm'], target['current_xy_mm'])]
            matrix = projection['linear']
            pixels = [sum(row[i] * delta[i] for i in range(2)) for row in matrix]
            # The marker proves visibility/identity. Its raster centroid can
            # be biased by the adjoining thin line; use the all-anchor model
            # for the actual hit coordinate.
            start = [sum(row[i] * target['current_xy_mm'][i] for i in range(2)) + offset
                     for row, offset in zip(matrix, projection['translation'])]
            end = [a + b for a, b in zip(start, pixels)]
            width, height = image['viewport_size']
            if math.hypot(*pixels) > 32 or not (3 <= end[0] < width - 3 and 3 <= end[1] < height - 3):
                return reply('UNSUPPORTED' if not dispatched else 'PARTIAL', 'TARGET_OUTSIDE_DRAG_BUDGET', 'The target must be inside this view and within a 32-logical-pixel correction', recovery=dispatched)
            view = captured['result']['after']
            batch = scene_adapter.bezier_drag_unsnapped({'view_ref': view['view_ref'], 'view_fingerprint': view['view_fingerprint'],
                     'scene_token': captured['result']['scene_token_after'], 'start': start, 'end': end})
            dispatched = dispatched or batch.get('effects_started', False)
            batch_ok = native('bezier_drag', batch)
            trace[-1]['events'] = (batch.get('result') or {}).get('events', [])
            trace[-1]['snap_settings'] = (batch.get('result') or {}).get('snap_settings')
            if not batch_ok:
                return reply(batch['status'], 'HANDLE_BATCH_NOT_COMPLETE', str(batch.get('error')), recovery=batch.get('requires_recovery', False) or dispatched)
            measured = curves._read_current(request['pattern_uuid'])
            verification = curves.verify_edit(initial['native'], measured['native'], plan)
            trace.append({'operation': 'native_readback', 'step': step, 'target_error_mm': verification.get('target_error_mm'),
                          'anchor_max_error_px': projection['anchor_max_error_px'], 'target_marker_error_px': projection['target_marker_error_px'],
                          'image': {'path': image['path'], 'sha256': image['sha256']}})
            if not verification.get('fixed_endpoints_and_other_semantics_preserved', verification.get('fixed_endpoints_and_other_native_data_unchanged')):
                return reply('PARTIAL', 'NON_TARGET_NATIVE_CHANGE', 'Independent native preservation readback failed', recovery=True)
            if verification['target_reached']:
                return reply('SUCCEEDED', verification=verification)
            if verification['scene_unchanged']:
                return reply('SUCCEEDED', verification=verification, native_no_effect=True)
            error = verification['target_error_mm']
            if error >= previous_error - 1e-7:
                return reply('PARTIAL', 'TARGET_RESIDUAL_NOT_DECREASING', 'The measured target residual stopped decreasing', recovery=True, verification=verification)
            current, previous_error = measured, error
        return reply('PARTIAL', 'TARGET_TOLERANCE_NOT_REACHED', 'Three measured corrections did not reach the requested tolerance', recovery=True, verification=verification)
    except Exception as exc:
        return reply('PARTIAL' if dispatched else 'UNSUPPORTED', getattr(exc, 'code', 'HANDLE_PROVIDER_ERROR'), str(exc), recovery=dispatched)
