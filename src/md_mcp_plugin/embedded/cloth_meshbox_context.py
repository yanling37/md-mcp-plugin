"""Shared MeshBox context readback and transaction checks."""

import math
import sys

from . import cloth_vertex_context


# Keep the existing process key so a hot reload preserves an open transaction.
STATE_KEY = '_md_mcp_w01_meshbox_acceptance_v1'
MODES = ('MovePattern', 'MeshBox')
MESH_FIELDS = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices',
               'face_normal_indices', 'face_labels', 'topology_fingerprint')


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {'pending': None, 'sample': None}
        setattr(sys, STATE_KEY, state)
    return state


def _mode(token, identity):
    reply = cloth_vertex_context.inspect({'scene_token': token})
    value = reply['result']
    if (reply['status'] != 'SUCCEEDED' or reply['effects_started'] is not False
            or value.get('context_verified') is not True or value.get('scene_identity') != identity
            or value.get('scene_token') != token or value.get('root_window_stable') is not True
            or value.get('sdk_pattern_selection_stable') is not True
            or value.get('sdk_pattern_selection_corroborated') is not True):
        raise ValueError(f'Independent Cloth context unavailable: {reply.get("error")}')
    qt = value['qt']
    context = qt['context']
    if context['mesh_mode'] not in MODES or context['world_coordinate_checked'] is not True:
        raise ValueError('Expected an independently observed MovePattern or MeshBox with World enabled')
    return {'mode': context['mesh_mode'], 'world_coordinate_checked': True, 'scene_token': token,
            'physical_state_unchanged': qt['physical_state_unchanged'],
            'sdk_selection': value['sdk_pattern_selection_after'],
            'native_vertex_identity': value['native_vertex_identity']}


def _frame_2d(token):
    from . import native_setup

    if native_setup.configure_scene_adapter().get('configured') is not True:
        raise ValueError('The packaged 2D frame provider is unavailable')
    adapter = native_setup.get_scene_adapter()
    inspected = adapter.inspect_2d({'budget_ms': 1500})
    current = inspected.get('result') or {}
    if (inspected.get('status') != 'SUCCEEDED' or inspected.get('effects_started') is not False
            or inspected.get('requires_recovery') is not False or current.get('scene_token') != token):
        raise ValueError(f'MeshBox 2D view readback failed: {inspected.get("error")}')
    view = current['after']
    captured = adapter.capture_2d({'view_ref': view['view_ref'], 'view_fingerprint': view['view_fingerprint'],
                                   'scene_token': token, 'budget_ms': 1500})
    result = captured.get('result') or {}
    if (captured.get('status') != 'SUCCEEDED' or captured.get('effects_started') is not False
            or captured.get('requires_recovery') is not False or result.get('scene_token_after') != token
            or result.get('before') != view or result.get('after') != view):
        raise ValueError(f'MeshBox 2D frame capture failed: {captured.get("error")}')
    return {'image': result['image'], 'view': view, 'scene_token': token}


def _response(status, result, *, effects=False, recovery=False, code=None, message=None):
    return {'status': status, 'result': result, 'effects_started': effects, 'requires_recovery': recovery,
            'error': {'code': code, 'message': message} if code else None}


_FRAME_GL_DIAGNOSTICS = (
    'verified', 'effects_started', 'requires_recovery', 'capture_effects_started',
    'capture_recovery_verified', 'previous_recovery_pending', 'gl_state_restored',
    'background_endpoints_verified', 'original_Qt_context_surface_restored',
    'original_WGL_context_dc_restored', 'original_framebuffer_bindings_restored',
    'owned_resources_cleanup_verified', 'owned_resource_cleanup_failed',
    'owned_live_object_count', 'source_metadata_before_after_verified',
    'app_GL_observed_state_unchanged', 'paint_event_sent', 'paint_event_returned',
    'OS_input_calls', 'focus_calls', 'activation_calls', 'camera_switch_calls', 'model_setter_calls',
)
_FRAME_PHYSICAL_DIAGNOSTICS = (
    'background_safe', 'foreground_is_other_process', 'cursor_query_known',
    'foreground_focus_capture_query_known', 'foreground_capture_absent',
    'thread_focus_absent', 'thread_capture_absent', 'Qt_mouse_grabber_absent',
    'Qt_keyboard_grabber_absent', 'no_modal_or_popup', 'Qt_minimized', 'OS_minimized',
    'Qt_active', 'physical_keys_idle',
)
_FRAME_MAPPING_DIAGNOSTICS = (
    'viewport_size', 'device_pixel_ratio', 'view_geometry', 'viewport_geometry',
    'view_transform', 'viewport_transform', 'scroll_values', 'global_origin', 'checked_actions',
    'relative_visible', 'enabled', 'drag_mode', 'start_drag_distance',
)


def _frame_diagnostic_fields(value, names):
    return {key: value[key] for key in names if type(value.get(key)) is bool
            or (type(value.get(key)) is int and -2**31 <= value[key] < 2**31)}


def _frame_3d_failure(reply, result):
    evidence = _frame_diagnostic_fields(reply, ('effects_started', 'requires_recovery'))
    error = reply.get('error') or {}
    if type(reply.get('status')) is str:
        evidence['status'] = reply['status'][:32]
    if type(error) is dict and type(error.get('code')) is str:
        evidence['error_code'] = error['code'][:96]
    native = result.get('native')
    if type(native) is not dict:
        return evidence
    details = _frame_diagnostic_fields(native, (*_FRAME_GL_DIAGNOSTICS, 'return_code'))
    for key in ('status', 'error_code', 'mapping_fingerprint'):
        if type(native.get(key)) is str:
            details[key] = native[key][:96]
    for key in ('physical_before', 'physical_after'):
        physical = native.get(key)
        if type(physical) is dict:
            sample = _frame_diagnostic_fields(physical, _FRAME_PHYSICAL_DIAGNOSTICS)
            cursor = physical.get('cursor_position')
            if (type(cursor) is list and len(cursor) == 2
                    and all(type(coordinate) is int and -2**31 <= coordinate < 2**31 for coordinate in cursor)):
                sample['cursor_position'] = cursor.copy()
            details[key] = sample
    before, after = native.get('before'), native.get('after')
    for key, snapshot in (('before', before), ('after', after)):
        if type(snapshot) is dict:
            sample = _frame_diagnostic_fields(snapshot, ('scene_item_grabber_absent',))
            for field in ('view', 'mapping_fingerprint'):
                if type(snapshot.get(field)) is str:
                    sample[field] = snapshot[field][:64]
            details[key] = sample
    if type(before) is dict and type(after) is dict:
        details['snapshot_equal'] = before == after
        mapping_before, mapping_after = before.get('mapping'), after.get('mapping')
        if type(mapping_before) is dict and type(mapping_after) is dict:
            details['mapping_changed_fields'] = [
                key for key in _FRAME_MAPPING_DIAGNOSTICS if mapping_before.get(key) != mapping_after.get(key)
            ]
    evidence['native'] = details
    return evidence


def _frame_3d(pending):
    from .dispatch import _module

    if _module('native_setup').configure_world_adapter().get('configured') is not True:
        raise ValueError('The packaged 3D frame provider is unavailable')
    scene = pending['scene']
    reply = _module('world_frame').capture({'scene_token': scene['scene_token']})
    pending['frame_unresolved'] = reply.get('requires_recovery') is not False
    result = reply.get('result') or {}
    if pending.get('pre_selection_frame') and pending['frame_unresolved']:
        pending['frame_failure'] = result.get('native')
    if (reply.get('status') != 'SUCCEEDED' or pending['frame_unresolved'] or result.get('verified') is not True
            or result.get('scene_identity') != scene['identity'] or result.get('scene_token') != scene['scene_token']):
        pending['report']['frame_3d_failure'] = _frame_3d_failure(reply, result)
        raise ValueError(f'MeshBox 3D frame capture failed: {reply.get("error")}')
    return result


def _native_verified(reply, statuses, held):
    return (type(reply) is dict and reply.get('status') in statuses and reply.get('scene_stable') is True
            and reply.get('sample_transaction_held') is held
            and (reply.get('native') or {}).get('mode_readback_verified') is True
            and reply['native'].get('original_scope_preserved') is True
            and reply['native'].get('physical_state_unchanged') is True)


_SCHEMA = 'md-qt-mesh-vertex-3d-bounded-scene-drag-v1'
_EVENTS = ('hover', 'press', 'move', 'refine', 'release')
_EVENT_FLAGS = tuple(f'{event}_{suffix}' for event in _EVENTS for suffix in ('sent', 'returned'))
_EVENT_STAGES = {'before', 'after_hover', 'before_press', 'after_press', 'after_move', 'after_refine', 'after_release', 'final'}
_ZERO_FIELDS = ('physical_OS_input_calls', 'OS_cursor_setter_calls', 'focus_calls', 'activation_calls',
                'global_input_cache_setter_calls', 'event_pump_calls', 'direct_model_setter_calls',
                'preference_setter_calls', 'GL_function_calls', 'QObject_property_calls', 'QMetaObject_invoke_calls',
                'direct_graphics_item_enumeration_calls', 'viewport_mouse_sendEvent_calls', 'key_sendEvent_calls',
                'QAction_trigger_calls', 'QGraphicsView_mouse_cache_setter_calls')


def _require(value, message):
    if not value:
        raise ValueError(message)


def _native_outcome(raw, code, points, *, viewport_size=(652, 737), dpr=1, mapping=None):
    _require(type(code) is int and isinstance(raw, dict) and raw.get('schema') == _SCHEMA
             and type(raw.get('return_code')) is int and raw['return_code'] == code, 'Native receipt header differs')
    zero = type(raw.get('direct_synthetic_event_calls')) is int and raw['direct_synthetic_event_calls'] == 0
    if (code < 0 and raw.get('status') == 'REJECTED_OR_INCOMPLETE' and zero
            and raw.get('gesture_not_started') is True and raw.get('sticky_unsafe_observed') is False
            and raw.get('requires_recovery') is False and all(raw.get(k) is False for k in _EVENT_FLAGS)):
        return False
    expected = points['start'] + points['activation'] + points['end']
    _require(code == 0 and raw.get('status') == 'MESH_VERTEX_3D_BOUNDED_SCENE_DRAG_EVENTS_DISPATCHED_EFFECT_UNVERIFIED'
             and raw.get('receiver') == 'QGraphicsScene/QtView3D' and raw.get('supplied_viewport_coordinates') == expected
             and raw.get('direct_synthetic_event_calls') == raw.get('scene_sendEvent_calls') == 5
             and raw.get('requires_recovery') is False and raw.get('sticky_unsafe_observed') is False
             and raw.get('event_modifiers') == 'NoModifier' and raw.get('viewport_size_before') == list(viewport_size)
             and raw.get('viewport_DPR_before') == dpr and raw.get('required_DPR') == dpr
             and raw.get('configured_activation_pixel_delta_x') == -12
             and type(raw.get('start_drag_distance')) is int and 0 <= raw['start_drag_distance'] <= 10
             and raw.get('release_position') == points['end'], 'Native fixed 3D drag receipt differs')
    flags = ('paired_release_verified', 'release_receiver_scope_verified', 'gesture_cleanup_verified',
             'dispatch_sequence_completed', 'all_events_nonspontaneous', 'physical_cursor_unchanged',
             'public_drag_threshold_gate_verified', 'viewport_to_scene_QPointF_unrounded')
    _require(all(raw.get(k) is True for k in (*_EVENT_FLAGS, *flags))
             and all(type(raw.get(k)) is int and raw[k] == 0 for k in _ZERO_FIELDS), 'Native input/cleanup proof differs')
    stages = raw['stages']
    stage_flags = ('verified', 'scope_refs_same', 'receiver_refs_same', 'physical_safe', 'view_unchanged', 'drag_distance_unchanged')
    _require(set(stages) == _EVENT_STAGES and all(all(stage.get(k) is True for k in stage_flags) for stage in stages.values())
             and stages['final'].get('scene_item_grabber_absent') is True, 'Native stage scope/physical proof differs')
    _require(_payloads_match(raw, points), 'Native viewport mapping or five event payloads differ')
    if mapping is not None:
        view = raw['view_before']
        checks = {'viewport_size': 'viewport_size', 'device_pixel_ratio': 'DPR', 'view_geometry': 'view_geometry',
                  'viewport_geometry': 'viewport_geometry', 'view_transform': 'transform',
                  'viewport_transform': 'viewport_transform'}
        _require(all(mapping[key] == view[native_key] for key, native_key in checks.items())
                 and mapping['scroll_values'] == [view['horizontal_scroll'], view['vertical_scroll']],
                 'The native drag viewport differs from the selected-frame mapping')
    return True


def _payloads_match(raw, points):
    view = raw['view_before']
    if view.get('verified') is not True:
        return False
    matrix = view['viewport_transform']
    scene = [view[key] for key in ('scene_point', 'end_scene_point', 'refine_scene_point')]
    screen = [view[key] for key in ('screen_point', 'end_screen_point', 'refine_screen_point')]
    positions = [points[key] for key in ('start', 'activation', 'end')]
    for (x, y), point in zip(scene, positions, strict=True):
        scale = matrix[2] * x + matrix[5] * y + matrix[8]
        if scale == 0 or not math.isfinite(scale):
            return False
        mapped = [(matrix[0] * x + matrix[3] * y + matrix[6]) / scale,
                  (matrix[1] * x + matrix[4] * y + matrix[7]) / scale]
        if math.dist(mapped, point) > 1e-9 or not all(map(math.isfinite, mapped)):
            return False
    if not all(len(row) == 2 and all(type(v) is int for v in row) for row in screen):
        return False
    schedule = ((155, 0, 0, 0, 0), (156, 0, 0, 1, 1), (155, 1, 0, 0, 1),
                (155, 2, 1, 0, 1), (157, 2, 2, 1, 0))
    rows = raw['prepared_event_payloads']
    if len(rows) != len(schedule):
        return False
    for row, (kind, current, last, button, buttons) in zip(rows, schedule, strict=True):
        expected = {'event_type': kind, 'button': button, 'buttons': buttons, 'modifiers': 0,
                    'scene_position': scene[current], 'last_scene_position': scene[last], 'button_down_scene_position': scene[0],
                    'screen_position': screen[current], 'last_screen_position': screen[last], 'button_down_screen_position': screen[0]}
        if (row.get('nonspontaneous') is not True or row.get('viewport_widget_verified') is not True
                or any(type(row.get(key)) is not type(value) or row[key] != value for key, value in expected.items())):
            return False
    return True
