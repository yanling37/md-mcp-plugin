"""Recognize a narrow curve case before independently replacing its native bridge."""
from .curves import NATIVE_FINGERPRINT_FIELDS, _id, _tolerance, _xy
import math
import re


WRITE = 'curves.set_handles'
RECOVERY = 'project.reconcile_released_curve_drag'
REPLACED = 'RELEASED_CURVE_BRIDGE_REPLACED'
_BUDGET_ERROR = 'COOPERATIVE_BUDGET_EXCEEDED'
_TRACE = ('inspect', 'capture', 'native_readback', 'initial_semantic_projection', 'bezier_drag')
_EVENTS = (('hover', 0, 0, 0), ('press', 1, 1, 0),
           ('move', 0, 1, 134217728), ('release', 1, 0, 134217728))


def is_sha256(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(value):
    _require(type(value) is dict, 'Original curve receipt lacks a structured witness')
    return value


def _true(data, *keys):
    return all(data.get(key) is True for key in keys)


def _number(value):
    return type(value) in (float, int) and math.isfinite(value)


def _closed_trace(provider, target):
    trace = provider.get('trace')
    _require(type(trace) is list and tuple(_object(row).get('operation') for row in trace) == _TRACE,
             'Expected one normal curve input batch after the initial probes')
    inspect, capture, baseline, projection, drag = trace
    for probe in (inspect, capture):
        _require(probe.get('status') == 'SUCCEEDED' and probe.get('error') is None
                 and type(probe.get('event_calls')) is int and probe['event_calls'] == 0
                 and probe.get('requires_recovery') is False and probe.get('paired_release_verified') is False
                 and probe.get('events') == [] and 'snap_settings' in probe and probe['snap_settings'] is None,
                 'Initial curve probes did not complete without input')
    _require(inspect.get('image') is None and is_sha256(_object(capture.get('image')).get('sha256')),
             'Initial capture has no image witness')
    previous_target = _xy(baseline.get('actual_xy_mm'), 'initial readback')
    error = baseline.get('target_error_mm')
    _require(baseline.get('stage') == 'after_initial_capture'
             and baseline.get('fixed_endpoints_and_other_semantics_preserved') is True
             and _number(error) and error > 0 and math.isclose(error, math.dist(previous_target, target), rel_tol=1e-9)
             and projection.get('missing_anchor_ids') == [] and all(
                 _number(projection.get(key)) and projection[key] >= 0
                 for key in ('anchor_max_error_px', 'target_marker_error_px')),
             'Initial native readback or viewport projection is incomplete')
    events = drag.get('events')
    _require(drag.get('status') == 'PARTIAL' and _object(drag.get('error')).get('code') == _BUDGET_ERROR
             and type(drag.get('event_calls')) is int and drag['event_calls'] == len(_EVENTS)
             and _true(drag, 'requires_recovery', 'paired_release_verified') and drag.get('snap_settings') is None
             and drag.get('image') is None and type(events) is list and len(events) == len(_EVENTS),
             'Only the normal four-event drag with cooperative budget expiry can be reconciled')
    for event, (stage, button, buttons, modifiers) in zip(events, _EVENTS):
        event = _object(event)
        _require(event.get('stage') == stage and _true(event, 'sent', 'returned', 'accepted', 'send_return')
                 and all(type(event.get(key)) is int and event[key] == expected for key, expected in
                         (('button', button), ('buttons', buttons), ('modifiers', modifiers))),
                 'The normal hover, press, move and release must all have returned')
    for key in ('scene', 'viewport_logical', 'screen'):
        points = [_xy(event.get(key), key) for event in events]
        _require(points[0] == points[1] and points[2] == points[3] and points[0] != points[2],
                 'Native hover/press and move/release do not describe one closed drag')


def _checked_requested_handle(readback, fingerprints, original_params, target):
    from .curves import _reference_identity

    pattern = readback['pattern_uuid']
    _require(type(original_params) is dict and original_params.get('pattern_uuid') == pattern
             and _xy(original_params.get('target_xy_mm'), 'requested target') == target,
             'Verified curve target differs from the submitted request')
    _tolerance(original_params.get('tolerance_mm', .002))
    before = _reference_identity(original_params.get('handle_ref'))
    _require(before['pattern_uuid'] == pattern and before['scope'] == fingerprints['scope']
             and before['topology_fingerprint'] == fingerprints['topology_fingerprint']
             and all(is_sha256(before[key]) and before[key] != fingerprints[key]
                     for key in ('scene_fingerprint', 'geometry_fingerprint')),
             'The verified curve no longer matches the submitted project or topology')
    expected = {**before, **fingerprints}
    curves = readback.get('curves')
    _require(type(curves) is list, 'Native curve readback lacks handle identities')
    matches = []
    for curve in curves:
        handles = _object(curve).get('handles')
        _require(type(handles) is list, 'Native curve readback lacks handle identities')
        for handle in handles:
            handle = _object(handle)
            if _reference_identity(handle.get('handle_ref')) == expected:
                matches.append(handle)
    _require(len(matches) == 1 and matches[0].get('independently_addressable') is True
             and _xy(matches[0].get('xy_mm'), 'verified handle') == target,
             'The submitted handle is not uniquely verified at the requested XY')


def released_curve_drag_proof(receipt, blocked_request_id, *, original_params=None):
    """Return original curve evidence or raise ValueError. Never touch MD or its files."""
    receipt = _object(receipt)
    _require(type(blocked_request_id) is str and re.fullmatch('[0-9a-f]{32}', blocked_request_id) is not None
             and receipt.get('operation_id') == blocked_request_id and receipt.get('status') == 'PARTIAL'
             and _true(receipt, 'effects_started', 'requires_recovery')
             and _object(receipt.get('error')).get('code') == 'HANDLE_PROVIDER_RECOVERY_REQUIRED',
             'The cached receipt is not the original partial curve transaction')
    result = _object(receipt.get('result'))
    verify = _object(result.get('verification'))
    _require(result.get('provider') == 'fresh_native_2d_handle_provider'
             and _true(result, 'provider_called', 'write_attempted', 'requires_recovery')
             and result.get('provider_error') is None and result.get('automatic_retry_allowed') is False
             and _true(verify, 'verified', 'target_reached', 'handle_identity_preserved',
                       'fixed_endpoints_and_other_semantics_preserved', 'scene_identity_preserved')
             and verify.get('scene_unchanged') is False and _number(verify.get('target_error_mm'))
             and verify['target_error_mm'] == 0,
             'Original curve readback does not prove an exact isolated handle edit')
    derived = verify.get('derived_native_data_verification')
    _require(verify.get('fixed_endpoints_and_other_native_data_unchanged') is True
             or (verify.get('fixed_endpoints_and_other_native_data_unchanged') is False
                 and _object(derived).get('verified') is True),
             'Original curve readback did not verify fixed endpoints and the remaining native data')
    target = _xy(verify.get('actual_xy_mm'), 'actual target')
    provider = _object(result.get('provider_receipt'))
    _require(provider.get('status') == 'PARTIAL' and _true(provider, 'effects_started', 'geometry_effects_started', 'requires_recovery')
             and provider.get('closed_input_batch_verified') is False
             and _object(provider.get('error')).get('code') == 'CURVE_BEZIER_DRAG_FAILED',
             'Original provider does not report the expected incomplete inspection')
    _closed_trace(provider, target)
    readback = _object(result.get('readback'))
    fingerprints = {key: readback.get(key) for key in (*NATIVE_FINGERPRINT_FIELDS, 'scope')}
    pattern = _id(readback.get('pattern_uuid'), 'pattern_uuid')
    _require(readback.get('unit') == 'mm' and all(is_sha256(value) for value in fingerprints.values())
             and _object(readback.get('native_endpoint_binding')).get('verified') is True,
             'Original native readback is missing its endpoint, scene or scope binding')
    if original_params is not None:
        _checked_requested_handle(readback, fingerprints, original_params, target)
    return {'pattern_uuid': pattern, 'target_xy_mm': target, 'native_fingerprints': fingerprints, 'readback': readback}
