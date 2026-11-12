"""Receipt checks for the bounded pre-selection MeshBox inverse."""

import re


WRITE = 'cloth.set_positions'
RECOVERY = 'cloth.recover_positions'
RESTORED = 'CLOTH_MESHBOX_MODE_RESTORED'


def hex_string(value, size):
    return type(value) is str and re.fullmatch(f'[a-f0-9]{{{size}}}', value) is not None


def frame_endpoint_failure(raw, identity):
    """The framebuffer was cleaned, but the endpoint guard still quarantined it."""
    if type(raw) is not dict:
        return False
    before, after = raw.get('before'), raw.get('after')
    return (raw.get('schema') == 'md-qt-world-frame-probe/1' and raw.get('operation') == 'capture'
            and raw.get('status') == 'PARTIAL' and raw.get('return_code') == -10
            and raw.get('error_code') == 'FRAME_CAPTURE_ENDPOINT_CHANGED'
            and raw.get('process_id') == identity['pid']
            and raw.get('process_creation_filetime') == str(identity['creation_filetime'])
            and raw.get('capture_effects_started') is raw.get('requires_recovery') is True
            and raw.get('effects_started') is raw.get('verified') is raw.get('background_endpoints_verified') is False
            and all(raw.get(key) is True for key in ('gl_state_restored', 'capture_recovery_verified',
                    'original_Qt_context_surface_restored', 'original_WGL_context_dc_restored',
                    'original_framebuffer_bindings_restored', 'owned_resources_cleanup_verified'))
            and all(type(raw.get(key)) is int and raw[key] == 0 for key in ('OS_input_calls', 'focus_calls',
                    'activation_calls', 'camera_switch_calls', 'model_setter_calls', 'scene_input_calls'))
            and type(before) is dict and before == after and before.get('scene_item_grabber_absent') is True)


def parent_witness(receipt, request_id, fingerprint, token):
    try:
        result = receipt['result']
        proof = result['position_recovery']
        entry = result['entry']
        native = entry['native']
        baseline = result['mesh_before']
        identity = result['scene_identity']
        return (receipt['operation_id'] == proof['request_id'] == request_id and hex_string(request_id, 32)
                and proof['fingerprint'] == fingerprint and hex_string(fingerprint, 64)
                and receipt['status'] == 'UNKNOWN' and receipt['error']['code'] == 'CLOTH_POSITION_RECOVERY_REQUIRED'
                and receipt['effects_started'] is receipt['requires_recovery'] is True
                and result['scene_token'] == entry['scene_token'] == token and hex_string(token, 64)
                and result['requested_position_verified'] is False and result['entry_verified'] is True
                and result['mode_before']['mode'] == 'MovePattern' and result['mode_during']['mode'] == 'MeshBox'
                and entry['status'] == 'MESHBOX_VERIFIED' and entry['ticket'] == result['ticket']
                and entry['scene_stable'] is entry['sample_transaction_held'] is True
                and native['mode_readback_verified'] is native['original_scope_preserved'] is native['physical_state_unchanged'] is True
                and not {'selection_3d_before', 'selection_dispatch', 'drag', 'restore', 'position_verification'} & result.keys()
                and hex_string(proof['source_sha256'], 64) and hex_string(proof['frame_bridge_sha256'], 64)
                and hex_string(proof['camera']['cmt_sha256'], 64) and hex_string(proof['camera']['cpt_sha256'], 64)
                and type(proof['camera']['mapping']) is dict and hex_string(baseline['arrays_sha256'], 64)
                and frame_endpoint_failure(proof['frame_failure'], identity))
    except (AttributeError, KeyError, TypeError):
        return False


def closed_inverse(restored, original):
    try:
        native, identity = restored['native'], original['scene_identity']
        status = restored['status']
        return (status == native['status'] and status in ('MOVE_RESTORED', 'MOVE_ALREADY_OBSERVED')
                and restored['scene_token'] == original['scene_token']
                and restored['ticket'] == native['ticket'] == original['ticket']
                and native['process_id'] == identity['pid']
                and native['creation_filetime'] == str(identity['creation_filetime'])
                and native['root_window'] == original['entry']['native']['root_window']
                and native['schema'] == 'md-qt-cloth-meshbox-entry/1' and native['command'] == 'restore'
                and native['return_code'] == 0 and restored['scene_stable'] is True
                and restored['sample_transaction_held'] is native['transaction_held'] is False
                and native['requires_recovery'] is native['restore_required'] is False
                and native['mode_readback_verified'] is native['original_scope_preserved'] is native['physical_state_unchanged'] is True
                and native['QAction_trigger_calls'] == int(status == 'MOVE_RESTORED')
                and all(type(native.get(key)) is int and native[key] == 0 for key in ('global_input_calls', 'focus_change_calls',
                        'event_dispatch_calls', 'point_selection_calls', 'geometry_setter_calls')))
    except (AttributeError, KeyError, TypeError):
        return False
