"""Stopped Selected Rest Update/Reset with independent garment archive evidence."""

from functools import partial
import math
from pathlib import Path
import re
import struct

from md_mcp_plugin.errors import PluginError
from . import mesh, rest_scope, selection, world_adapter, world_frame
from .common import check_scene_token, digest


def _rows(evidence, inventory):
    rows = {row['pattern_uuid']: row for row in evidence['patterns']}
    if len(rows) != len(evidence['patterns']) or {uid: row['pattern_name'] for uid, row in rows.items()} != inventory:
        raise ValueError('Garment archive and complete SDK owner inventory differ')
    return rows


def _reference(row):
    return {key: row[key] for key in ('serialized_flag_value', 'solidify_enabled', 'position_field_present',
                                      'position_count', 'position_array_sha256')}


def _reference_inventory(rows):
    return {uid: _reference(row) for uid, row in rows.items()}


def _six_decimal_obj(snapshot):
    asset = snapshot['export']['obj_asset']
    raw, observed = mesh._asset(Path(asset['path']))
    vertex_lines = [line for line in raw.splitlines() if re.match(rb'[ \t]*v[ \t]+', line)]
    return (observed == asset and len(vertex_lines) == snapshot['vertex_count']
            and all(re.fullmatch(rb'[ \t]*v(?:[ \t]+[+-]?\d+\.\d{6}){3}[ \t]*', line) for line in vertex_lines))


def _update_precision(row, positions, six_decimal_obj):
    stored = row['serialized_positions_mm']
    if (row['serialized_flag_value'] is not True or row['solidify_enabled'] is not False
            or stored is None or len(stored) != len(positions) or not positions):
        return None
    if all(struct.pack('<3f', *a) == struct.pack('<3f', *b) for a, b in zip(stored, positions, strict=True)):
        return 'ordered_float32'
    if six_decimal_obj and all([round(value, 6) for value in a] == list(b) for a, b in zip(stored, positions, strict=True)):
        return 'ordered_obj_six_decimal'
    return None


def _matches_reset(row):
    return (row['serialized_flag_value'] is False and row['solidify_enabled'] is False
            and row['position_field_present'] is False)


def _position_diagnostic(row, positions):
    stored = row['serialized_positions_mm']
    result = {'archive_vertex_count': row['position_count'], 'obj_vertex_count': len(positions),
              'archive_position_sha256': row['position_array_sha256']}
    if stored is not None and len(stored) == len(positions):
        result.update(ordered_maximum_error_mm=max((math.dist(a, b) for a, b in zip(stored, positions, strict=True)), default=0),
                      float32_position_multisets_equal=sorted(struct.pack('<3f', *row) for row in stored)
                      == sorted(struct.pack('<3f', *row) for row in positions))
    return result


def original_override_restored(payload):
    enabled = payload['override_enable']
    restored = payload.get('override_restore', enabled)
    return (enabled['setting_read_back'] is True and restored['setting_read_back'] is True
            and enabled['original_page_restored'] is True and restored['original_page_restored'] is True
            and type(enabled['original_ui_value']) is bool and restored['final_ui_value'] is enabled['original_ui_value']
            and enabled['native_setting']['original_stack'] == restored['native_setting']['original_stack'])


def closed_reference_observation(payload):
    native = payload['native_selected']
    return (payload['native_button_clicks'] == 1 and native['transaction_closed'] is True
            and native['current_obj_unchanged'] is True and native['before']['world_arrays_sha256'] == native['after']['world_arrays_sha256']
            and original_override_restored(payload)
            and isinstance(payload['before_references'], dict) and payload['pattern_uuid'] in payload['before_references'])


def _verified(action, before, after, target, positions, *, six_decimal_obj=False):
    if set(before) != set(after) or any(_reference(before[uid]) != _reference(after[uid]) for uid in before if uid != target):
        raise ValueError('A non-target garment reference changed')
    row = after[target]
    if action == 'reset':
        if not _matches_reset(row):
            raise ValueError('The selected archive still has a fixed Rest reference after Reset')
        return {'selected_fixed_reference_removed': True}
    precision = _update_precision(row, positions, six_decimal_obj)
    if precision is None:
        raise ValueError('The selected archive Rest XYZ differs from the independently ordered current OBJ')
    return {'selected_rest_matches_current_float32': precision == 'ordered_float32',
            'rest_xyz_readback_precision': precision, 'selected_vertex_count': len(positions),
            'maximum_rest_position_error_mm': max(math.dist(a, b) for a, b in zip(
                row['serialized_positions_mm'], positions, strict=True))}


def _require_hidden_rest_host(token, selected_trial):
    from .dispatch import _module

    try:
        setup = _module('native_setup').configure_rest_host_controls()
        if setup.get('configured') is not True:
            raise ValueError('The read-only Rest host catalog is unavailable')
        observed = _module('rest_adapter').inspect_host_controls({'scene_token': token, 'budget_ms': 1500})
        catalog = observed.get('result') or {}
        if (observed['status'] != 'SUCCEEDED' or observed['effects_started'] is not False
                or observed['requires_recovery'] is not False or catalog.get('verified') is not True
                or catalog.get('scene_token') != token or catalog.get('catalog_complete') is not True):
            reason = (observed.get('error') or {}).get('message')
            raise ValueError(reason or 'The current Rest host catalog could not be verified')
        if catalog['override_instance_count'] == 0:
            raise ValueError('The current MD window has no initialized Rest host')
        if catalog['override_instance_count'] != 1 or len(catalog['override_instances']) != 1:
            raise ValueError('The current MD window must own one Rest host')
        host = catalog['override_instances'][0]
        owner, host_path = host['owner_path'][-2:]
        if ((owner['class_name'], owner['object_name'], host_path['class_name'], host_path['object_name']) != (
                'MDMainWindow', 'CLO', 'Marvelous::DialogOverrideSettings', 'DialogOverrideSettings')
                or host['enabled'] is not True or host['visible'] is not False):
            raise ValueError('The current MD window must own an enabled hidden Rest host')

        for index, text in enumerate(selected_trial._CONTROL_TEXTS):
            kind = 'CloUICommon::TextButton' if index < 2 else 'CloUICommon::RadioButton'
            path = [*selected_trial._CONTROL_ANCESTRY, [kind, '']]
            rows = [row for row in host['host_standard_controls'] if row['text'] == text
                    and row['standard_control'] == 'QAbstractButton' and row['path_from_host'] == path]
            if (len(rows) != 1 or rows[0]['enabled'] is not True or rows[0]['visible'] is not False
                    or rows[0]['text_truncated'] is not False or rows[0]['checkable'] is not (index >= 2)
                    or rows[0]['checked'] is not (index == 2)):
                raise ValueError('The hidden Rest controls must be ready in Selected Patterns scope')
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise PluginError('CLOTH_REST_HOST_NOT_READY', str(error)[:500], effects_started=False) from error


def run(current, target, action):
    from .dispatch import _module

    archive = _module('rest_garment_archive')
    parser = _module('rest_pac_evidence')
    selected_trial = _module('rest_selected_trial')
    override = _module('rest_scoped_pinching_override')
    token = current['scene_token']
    result = {'pattern_uuid': target, 'action': action, 'scene_token': token,
              'solver_rest_write_verified': False, 'native_button_clicks': 0}
    effects = False
    scope_restored = True

    def read_selection():
        return selected_trial.selected_owner(current, target)

    def read_pose():
        snapshot = selected_trial.current_mesh(current)
        return digest({key: snapshot[key] for key in world_frame.MESH_FIELDS})

    def capture():
        evidence = archive.capture(current, pose, scope, selected, read_current=partial(check_scene_token, token),
                                   read_pose=read_pose, read_scope=partial(rest_scope._read_scope, current),
                                   read_selection=read_selection)
        observed = parser.inspect_project_bytes(evidence.payload, evidence.sha256, inventory)
        return evidence.sha256, _rows(observed, inventory)

    def settings(enabled):
        nonlocal effects
        response = override.set_scoped({'scene_token': token, 'enabled': enabled, 'budget_ms': 1500},
                                       expected_current=current, pattern_uuid=target)
        effects = effects or response['effects_started']
        state = response['result']
        result['override_enable' if enabled else 'override_restore'] = state
        if response['requires_recovery']:
            raise ValueError('The scoped Pinching setting needs recovery')
        if response['status'] != 'SUCCEEDED' or state['setting_read_back'] is not True:
            raise ValueError((response.get('error') or {}).get('message') or 'The scoped Pinching setting is unavailable')
        return state

    def reselect(state):
        nonlocal effects
        if state['sdk_selection_cleared']:
            effects = True
            changed = selection.select_pattern({'pattern_uuid': target})
            if changed['status'] not in ('NO_OP', 'SUCCEEDED') or changed['verified'] is not True:
                raise ValueError('The original singleton selection needs recovery')
        if read_selection() != selected:
            raise ValueError('The original singleton selection changed')

    try:
        if current['identity']['md_version'] != rest_scope._BUILD:
            raise ValueError('This garment archive profile requires MD 2026.1.237')
        world_frame._require_stopped_simulation(token)
        _, label = world_adapter._target(current, target)
        selected = read_selection()
        scope = rest_scope._read_scope(current)
        snapshot = selected_trial.current_mesh(current)
        world_adapter.bind_export_labels(current, snapshot, target, label)
        positions = mesh.extract_group(snapshot, label)['positions_mm']
        six_decimal_obj = _six_decimal_obj(snapshot) if action == 'update' else False
        pose = digest({key: snapshot[key] for key in world_frame.MESH_FIELDS})
        inventory = {row['pattern_uuid']: row['information']['name'] for row in current['patterns']}
        result['before_archive_sha256'], before = capture()
        result['before_references'] = _reference_inventory(before)
        result['rest_fixed_before'] = before[target]['serialized_flag_value']
        if before[target]['solidify_enabled'] is not False:
            raise ValueError('This reference is shared with a Solidify-enabled target')
        already_matches = (_update_precision(before[target], positions, six_decimal_obj) is not None
                           if action == 'update' else _matches_reset(before[target]))
        if already_matches:
            result.update(verified=True, requested_rest_state_verified=True, no_op=True)
            return {'status': 'NO_OP', 'result': result, 'error': None,
                    'effects_started': False, 'requires_recovery': False}

        _require_hidden_rest_host(token, selected_trial)
        result['rest_host_preflight_verified'] = True
        scope_restored = False
        enabled = settings(True)
        original_value = enabled['original_ui_value']
        reselect(enabled)
        clicked = selected_trial.trial({'scene_token': token, 'action': action},
                                       expected_current=current, pattern_uuid=target)
        effects = effects or clicked['effects_started']
        result['native_selected'] = clicked['result']
        result['native_button_clicks'] = clicked['result']['button_click_calls']
        if clicked['requires_recovery']:
            raise ValueError('The Selected Rest button has an unresolved native outcome')
        if original_value is False:
            restored = settings(False)
            reselect(restored)
            if restored['native_setting']['original_stack'] != enabled['native_setting']['original_stack']:
                raise ValueError('The original property page could not be verified')
        scope_restored = True
        result['original_pinching_override_restored'] = original_override_restored(result)
        if not result['original_pinching_override_restored']:
            raise ValueError('The original Pinching override scope could not be verified')
        if clicked['status'] != 'SUCCEEDED':
            if clicked['effects_started'] is False and result['native_button_clicks'] == 0:
                result['after_archive_sha256'], after = capture()
                result['after_references'] = _reference_inventory(after)
                result['closed_no_rest_change'] = result['before_references'] == result['after_references']
            raise ValueError((clicked.get('error') or {}).get('message') or 'Selected Rest was rejected')
        result['after_archive_sha256'], after = capture()
        result['after_references'] = _reference_inventory(after)
        result['rest_fixed_after'] = after[target]['serialized_flag_value']
        try:
            verification = _verified(action, before, after, target, positions, six_decimal_obj=six_decimal_obj)
        except ValueError:
            result['position_diagnostic'] = _position_diagnostic(after[target], positions)
            raise
        result.update(verification, verified=True,
                      requested_rest_state_verified=True, solver_rest_write_verified=True,
                      simulation_response_verified=False,
                      current_world_obj_unchanged=True, non_target_references_unchanged=True,
                      original_pinching_override_restored=True)
        return {'status': 'SUCCEEDED', 'result': result, 'error': None,
                'effects_started': True, 'requires_recovery': False}
    except Exception as error:
        if isinstance(error, PluginError):
            effects = effects or error.details.get('effects_started') is True
            detail = error.as_dict()
        else:
            detail = {'code': 'CLOTH_REST_ARCHIVE_NOT_VERIFIED', 'message': str(error)[:500]}
        unresolved = effects and (not scope_restored or result.get('closed_no_rest_change') is not True
                                  or isinstance(error, PluginError) and error.details.get('requires_recovery') is True)
        return {'status': 'UNKNOWN' if unresolved else 'FAILED' if effects else 'REJECTED',
                'result': dict(result, verified=False), 'error': detail,
                'effects_started': effects, 'requires_recovery': unresolved}


def reconcile(params):
    from .dispatch import _RECEIPTS, _module

    block = params['blocked_request_id']
    token = params['scene_token']
    try:
        receipt = _RECEIPTS[block]
        previous = receipt['result']
        if (receipt['operation_id'] != block or receipt['status'] != 'UNKNOWN' or receipt['requires_recovery'] is not True
                or previous['scene_token'] != token or not closed_reference_observation(previous)):
            raise ValueError('Expected the original closed native Rest action with a captured archive')
        current = check_scene_token(token)
        target = previous['pattern_uuid']
        native = previous['native_selected']
        if current['identity'] != native['scene_identity'] or target != native['selected_pattern_uuid']:
            raise ValueError('The current Rest scene or selected target changed')
        selected_trial = _module('rest_selected_trial')
        world_frame._require_stopped_simulation(token)
        selected = selected_trial.selected_owner(current, target)
        scope = rest_scope._read_scope(current)
        snapshot = selected_trial.current_mesh(current)
        pose = digest({key: snapshot[key] for key in world_frame.MESH_FIELDS})
        if pose != native['after']['world_arrays_sha256']:
            raise ValueError('The complete current OBJ differs from the closed Rest action')

        def read_pose():
            observed = selected_trial.current_mesh(current)
            return digest({key: observed[key] for key in world_frame.MESH_FIELDS})

        evidence = _module('rest_garment_archive').capture(
            current, pose, scope, selected, read_current=partial(check_scene_token, token), read_pose=read_pose,
            read_scope=partial(rest_scope._read_scope, current),
            read_selection=partial(selected_trial.selected_owner, current, target))
        inventory = {row['pattern_uuid']: row['information']['name'] for row in current['patterns']}
        decoded = _module('rest_pac_evidence').inspect_project_bytes(evidence.payload, evidence.sha256, inventory)
        rows = _rows(decoded, inventory)
        references = _reference_inventory(rows)
        before = previous['before_references']
        if set(before) != set(references) or any(before[uid] != references[uid] for uid in before if uid != target):
            raise ValueError('The current non-target references do not match the original baseline')
        observed = rows[target]
        positions = mesh.extract_group(snapshot, inventory[target])['positions_mm']
        if 'after_references' in previous:
            if references != previous['after_references']:
                raise ValueError('The current Rest references do not match the recorded postcondition')
        else:
            # The original action closed, but its first post-capture failed. A new
            # capture must establish the requested target and preserved partners.
            _verified(previous['action'], before, rows, target, positions,
                      six_decimal_obj=_six_decimal_obj(snapshot))
        result = {'verified': True, 'recovered_operation_id': block, 'scene_token': token,
                  'original_mutation_status': 'UNKNOWN', 'recovery_action': 'REST_ARCHIVE_OBSERVED',
                  'repeated_native_button_clicks': 0, 'current_world_arrays_sha256': pose,
                  'archive_sha256': evidence.sha256, 'rest_fixed': observed['serialized_flag_value'],
                  'current_references': references, 'non_target_references_unchanged': True,
                  'position_diagnostic': _position_diagnostic(observed, positions)}
        return {'status': 'SUCCEEDED', 'result': result, 'error': None, 'effects_started': False, 'requires_recovery': False}
    except (KeyError, ValueError, PluginError) as error:
        details = error.as_dict() if isinstance(error, PluginError) else {
            'code': 'REST_ARCHIVE_RECONCILE_REJECTED', 'message': str(error)[:500]}
        uncertain = isinstance(error, PluginError) and error.details.get('requires_recovery') is True
        return {'status': 'UNKNOWN' if uncertain else 'REJECTED', 'result': None, 'error': details,
                'effects_started': uncertain, 'requires_recovery': uncertain}
