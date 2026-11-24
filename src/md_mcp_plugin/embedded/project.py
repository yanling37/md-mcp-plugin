from pathlib import Path
import hashlib
import base64
import json
import math

from md_mcp_plugin.errors import PluginError
from .common import scene


def open_project(params):
    import ApiTypes
    import import_api
    before = scene()
    path = Path(params.get("path", ""))
    if not path.is_absolute() or not path.is_file() or path.suffix.lower() != ".zprj":
        raise PluginError("INVALID_PROJECT_PATH", "An existing absolute .zprj path is required", effects_started=False)
    path = path.resolve()
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if before["pattern_count"] and params.get("replace_current") is not True:
        raise PluginError("REPLACE_CURRENT_REQUIRED", "Opening this project replaces the live scene; pass replace_current explicitly", current_project=before["identity"]["project_path"], effects_started=False)
    options = ApiTypes.ImportZPRJOption()
    fields = {"bAppend": False, "bLoadGarment": True, "bLoadAvatar": True,
              "bLoadSceneAndProps": False, "bLoadRenderProperties": False,
              "bLoadCustomView": False, "bLoadDisplaySettings": False}
    for name, value in fields.items():
        if not hasattr(options, name):
            raise PluginError("UNSUPPORTED_IMPORT_OPTIONS", "This MD version lacks a required non-dialog option", field=name, effects_started=False)
        setattr(options, name, value)
    returned = import_api.ImportZprjW(str(path), options)
    after = scene()
    unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == original_hash
    verified = returned is True and Path(after["identity"]["project_path"]).resolve() == path and unchanged
    return {"verified": verified, "api_return": returned, "source_file_unchanged": unchanged,
            "source_sha256": original_hash, "options": fields, "scene": after, "before_identity": before["identity"]}


def save_copy(params):
    import export_api
    before = scene()
    path = Path(params.get("path", ""))
    if not path.is_absolute() or path.suffix.lower() != ".zprj" or path.exists() or not path.parent.is_dir():
        raise PluginError("INVALID_OUTPUT_PATH", "A new absolute .zprj path in an existing directory is required", effects_started=False)
    path = path.resolve()
    returned = export_api.ExportZPrjW(str(path), False)
    after = scene()
    verified = path.is_file() and path.stat().st_size > 0
    return {"verified": verified, "api_return": returned, "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if verified else None,
            "before_identity": before["identity"], "scene": after}


def _closed_derived_sewing_failure(previous, payload, provider, batches):
    proof = payload.get('verification') or {}
    derived = proof.get('derived_native_data_verification') or {}
    movement = derived.get('sewing_endpoint_max_error_mm')
    paths = derived.get('normalized_paths')
    if (previous.get('status') != 'PARTIAL' or previous.get('effects_started') is not True
            or previous.get('requires_recovery') is not True or payload.get('write_attempted') is not True
            or payload.get('automatic_retry_allowed') is not False
            or provider.get('status') != 'PARTIAL' or (provider.get('error') or {}).get('code') != 'NON_TARGET_NATIVE_CHANGE'
            or provider.get('geometry_effects_started') is not True
            or not batches or any(batch.get('requires_recovery') is not False for batch in batches)
            or proof.get('scene_identity_preserved') is not True or proof.get('handle_identity_preserved') is not True
            or derived.get('verified') is not False or derived.get('reason') != 'Physical sewing endpoint moved beyond tolerance'
            or type(movement) not in (int, float) or not math.isfinite(movement) or movement <= 0
            or not isinstance(paths, list) or not paths):
        return False

    for batch in batches:
        events = batch.get('events')
        if (batch.get('status') != 'SUCCEEDED' or batch.get('paired_release_verified') is not True
                or type(events) is not list or len(events) != 4
                or [event.get('stage') for event in events] != ['hover', 'press', 'move', 'release']
                or any(event.get('sent') is not True or event.get('returned') is not True for event in events)
                or events[-1].get('buttons') != 0):
            return False

    seam_seen = False
    for path in paths:
        if not isinstance(path, list):
            return False
        if (len(path) == 9 and path[::2] == ['PatternList', 'InternalLineList', 'LineList', 'PointList', 'Position']
                and all(type(index) is int and index >= 0 for index in path[1::2])):
            continue
        if (len(path) == 7 and path[0] == 'SeamLinePairGroupList' and path[2] == 'PairList'
                and all(type(path[index]) is int and path[index] >= 0 for index in (1, 3))
                and path[4] in ('First', 'Second') and path[5] == 'LengthParam' and path[6] in ('fStart', 'fEnd')):
            seam_seen = True
            continue
        return False
    return seam_seen


def recover_curve(params):
    from . import dispatch, curves, canvas
    from .native_setup import get_scene_adapter
    scene_adapter = get_scene_adapter()
    try:
        previous = dispatch._RECEIPTS.get(params['blocked_request_id'])
        if not previous or previous['status'] not in ('PARTIAL', 'UNKNOWN', 'FAILED', 'REJECTED'):
            raise ValueError('MD has no completed unresolved curve receipt')
        payload = previous.get('result') or {}
        provider = payload.get('provider_receipt') or {}
        batches = [x for x in provider.get('trace', []) if x.get('operation') == 'bezier_drag']
        initialization_failure = (not provider and not batches
            and (payload.get('provider_error') or {}).get('type') == 'ImportError'
            and payload.get('verification',{}).get('scene_unchanged') is True)
        derived_sewing = _closed_derived_sewing_failure(previous, payload, provider, batches)
        if (payload.get('provider') != 'fresh_native_2d_handle_provider' or not (batches or initialization_failure)
                or any(x['status'] != 'SUCCEEDED' or x.get('paired_release_verified') is not True for x in batches)
                or (payload.get('verification', {}).get('fixed_endpoints_and_other_semantics_preserved') is not True
                    and not derived_sewing)
                or scene_adapter._state()['blocked']):
            raise ValueError('Recovery requires closed native input and a verified rollback case')
        rollback_fingerprints = payload.get('rollback_native_fingerprints')
        if derived_sewing:
            if (type(rollback_fingerprints) is not dict
                    or set(rollback_fingerprints) != set(curves.NATIVE_FINGERPRINT_FIELDS)
                    or not all(type(value) is str and len(value) == 64 for value in rollback_fingerprints.values())):
                raise ValueError('The original pre-edit native fingerprints are required')
            if curves._state().get('derived_curve_reopen') is not None:
                raise ValueError('A previous saved-project import needs reconciliation')
        readback = payload['readback']
        current = curves._read_current(readback['pattern_uuid'])
        ref = readback['curves'][0]['handles'][0]['handle_ref'][len(curves._PREFIX):]
        identity = json.loads(base64.urlsafe_b64decode(ref + '=' * (-len(ref) % 4)))
        if curves._digest(current['scope']) != identity['scope'] or any(
                current['public'][key] != readback[key] for key in curves.NATIVE_FINGERPRINT_FIELDS):
            raise ValueError('Current project changed after the partial result')
        source = Path(current['scope']['project'])
        if not source.is_file() or source.suffix.lower() != '.zprj' or hashlib.sha256(source.read_bytes()).hexdigest() != params['expected_source_sha256']:
            raise ValueError('Saved source project differs from the recovery checkpoint')
        observed = canvas.views({'view':'all'})
        state = observed.get('result') or {}
        if observed['status'] != 'SUCCEEDED' or not state.get('physical', {}).get('background_safe') or any(not v.get('scene_item_grabber_absent') for v in state.get('views', [])):
            raise ValueError('Current background input state is not closed')
    except Exception as exc:
        raise PluginError('CURVE_RECOVERY_PREFLIGHT_FAILED', str(exc), effects_started=False) from exc
    if derived_sewing:
        curves._state()['derived_curve_reopen'] = params['blocked_request_id']
    try:
        result = open_project({'path': str(source), 'replace_current': True})
    except PluginError as exc:
        if derived_sewing and exc.details.get('effects_started') is False:
            curves._state().pop('derived_curve_reopen', None)
        raise
    if derived_sewing:
        try:
            reopened = curves._read_current(readback['pattern_uuid'])
            if (result.get('verified') is not True or result.get('source_sha256') != params['expected_source_sha256']
                    or result.get('source_file_unchanged') is not True
                    or Path(reopened['scope']['project']).resolve() != source.resolve()
                    or reopened['scope']['pid'] != current['scope']['pid']
                    or any(reopened['public'][key] != value for key, value in rollback_fingerprints.items())):
                raise ValueError('Native pattern and sewing readback differs from the saved pre-edit fingerprints')
        except Exception as exc:
            raise PluginError('CURVE_RECOVERY_REOPEN_NOT_VERIFIED', str(exc),
                              effects_started=True, requires_recovery=True) from exc
        result['saved_native_pattern_and_sewing_verified'] = True
        curves._state().pop('derived_curve_reopen', None)
    basis = ('provider_import_failure_and_closed_current_input' if initialization_failure else
             'closed_native_input_with_derived_sewing_change' if derived_sewing else 'known_complete_native_batches')
    result.update(recovered_operation_id=params['blocked_request_id'], recovery_action='SAVED_PROJECT_RELOADED', original_mutation_status=previous['status'],
                  recovery_basis=basis)
    return result


def reconcile_released_curve_drag(params):
    from . import canvas, curves, dispatch, selection
    from .curve_release_recovery import released_curve_drag_proof
    from .native_setup import _packaged_scene_adapter, get_scene_adapter

    try:
        original_id = params['blocked_request_id']
        expected_source = params['expected_source_sha256']
        previous = dispatch._RECEIPTS.get(original_id)
        proof = released_curve_drag_proof(previous, original_id)
        initial = scene()
        source = Path(initial['identity']['project_path'])
        if (not source.is_absolute() or not source.is_file() or source.is_symlink()
                or source.suffix.lower() != '.zprj' or hashlib.sha256(source.read_bytes()).hexdigest() != expected_source):
            raise ValueError('The original active, saved project must still match its source SHA-256')

        def verify_current():
            current = curves._read_current(proof['pattern_uuid'])
            binding = current['public'].get('native_endpoint_binding') or {}
            if (Path(current['scope']['project']).resolve() != source.resolve() or binding.get('verified') is not True
                    or any(current['public'].get(key) != value for key, value in proof['native_fingerprints'].items())):
                raise ValueError('Native paper and scope must still match the exact partial operation')
            _verify_closed_background_input(canvas, budget_ms=1500)
            selected = selection.inspect_selection({})['selected']
            if [item['pattern_uuid'] for item in selected] != [proof['pattern_uuid']] or scene() != initial:
                raise ValueError('The released pattern must still be the sole selected pattern in the original project')

        verify_current()
        adapter = get_scene_adapter()
        old, successor, evidence = adapter.prepare_closed_drag_successor(_packaged_scene_adapter())
        verify_current()
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected_source:
            raise ValueError('The original project file changed during independent observation')
        identity = initial['identity']
        process = {key: identity[key] for key in ('pid', 'creation_filetime')}
        if old['loaded_process'] != process:
            raise ValueError('The original blocked native bridge belongs to a different process lifetime')
        result = dict(evidence, verified=True, recovered_operation_id=original_id,
                      recovery_action='RELEASED_CURVE_BRIDGE_REPLACED', original_mutation_status='PARTIAL',
                      source_sha256=expected_source, scene_token=initial['scene_token'], process_identity=process,
                      native_fingerprints=proof['native_fingerprints'], target_xy_mm=proof['target_xy_mm'],
                      native_scene_unchanged=True, old_bridge_retired=True, requires_recovery=False)
        adapter.adopt_closed_drag_successor(old, successor)
        return result
    except Exception as exc:
        raise PluginError('RELEASED_CURVE_RECOVERY_REJECTED', str(exc), effects_started=False) from exc


def recover_cut(params):
    from . import dispatch

    cut = dispatch._module('cut_qt')
    atomic = dispatch._module('cut_qt_atomic')
    canvas = dispatch._module('canvas')
    cut_state = cut._state()
    original_id = params['blocked_request_id']
    try:
        if cut_state.get('recovery_inflight') is not None:
            raise ValueError('A previous Cut recovery is unresolved')
        previous = dispatch._RECEIPTS.get(original_id) or {}
        result = previous.get('result') or {}
        adapter = result.get('adapter_response') or {}
        native = adapter.get('result') or {}
        if result.get('single_native_click_cut_batch') is not True:
            raise ValueError('Recovery requires the original atomic Cut receipt')
        atomic_state = atomic._state()
        batch_verified = (
            adapter.get('status') == 'SUCCEEDED'
            and adapter.get('requires_recovery') is False and native.get('Cut_action_returned') is True
            and native.get('dispatch_completed') is True and native.get('press_outstanding') is False
            and atomic_state['blocked'] is False
        )
        timed_out_after_return = (
            previous.get('status') in ('UNKNOWN', 'PARTIAL') and adapter.get('status') == 'PARTIAL'
            and adapter.get('requires_recovery') is True and adapter.get('effects_started') is True
            and atomic_state['blocked'] is True and atomic_state['busy'] is False
            and atomic_state['library'] is not None and atomic.closed_cut_before_observation_timeout(native)
        )
        deferred_readback = timed_out_after_return or (
            previous.get('status') in ('UNKNOWN', 'PARTIAL') and batch_verified
            and (result.get('error') or {}).get('code') == 'POST_CUT_READBACK_FAILED'
        )
        completed = previous.get('status') == 'PARTIAL' and batch_verified and not deferred_readback
        if not completed and not deferred_readback:
            raise ValueError('The original Cut return and closed input pair are required')
        current = scene()
        capture = cut._capture_current(cut._native._api())
        if capture['rows'] != current['patterns'] or scene() != current:
            raise ValueError('Current project changed during the native Cut readback')
        reconciliation = None
        if completed:
            if (current['identity'] != result.get('scene_identity_after')
                    or current['scene_token'] != result.get('scene_token_after')
                    or capture['scene_fingerprint'] != result.get('after_native_fingerprint')):
                raise ValueError('Current project no longer matches the Cut readback')
        else:
            process = atomic_state['process']
            if not process or any(current['identity'].get(key) != process.get(key)
                                  for key in ('pid', 'creation_filetime')):
                raise ValueError('Current MD process differs from the held Cut bridge')
            checkpoint = cut_state.get('pending_cut_readback')
            target = result['target']
            if checkpoint is None:
                raise ValueError('The saved in-process pre-Cut capture is unavailable')
            elif (checkpoint['target'] != target
                  or checkpoint['scene_token'] != result.get('scene_token_before')):
                raise ValueError('The retained Cut checkpoint belongs to another request')
            if (checkpoint['identity'] != current['identity']
                    or checkpoint['source_sha256'] != params['expected_source_sha256']):
                raise ValueError('The retained Cut checkpoint does not match this saved project')
            before = checkpoint['before']
            if (before['scene_fingerprint'] != target['scene_geometry_fingerprint']
                    or before['board_fingerprint'] != target['selection_board_fingerprint']):
                raise ValueError('The retained pre-Cut geometry does not match the original target')
            partition = cut._native._partition(before, capture, target['pattern_uuid'], target['curve'])
            if partition['native_partition_observed'] is not True:
                raise ValueError('Fresh native readback did not prove the original Cut partition')
            reconciliation = {
                'basis': 'closed_pair_and_return_with_fresh_native_partition',
                'scene_token': current['scene_token'], 'partition': partition,
            }
            surface_before = checkpoint.get('surface_before')
            if surface_before is not None:
                old_mesh, old_groups = surface_before
                try:
                    new_mesh, new_groups = cut._surface_capture(partition['resulting_pattern_uuids'], current['scene_token'])
                    reconciliation['surface'] = dispatch._module('cut_surface_readback').verify_cut_surface(
                        old_mesh, new_mesh, old_groups, new_groups,
                        native_boundary_partition_verified=partition['checks']['boundary_partition_verified'],
                    )
                except Exception as exc:
                    reconciliation['surface'] = {
                        'verified': False, 'error': {'code': getattr(exc, 'code', 'SURFACE_READBACK_FAILED'),
                                                      'message': str(exc)},
                    }
        source = Path(current['identity']['project_path'])
        if not source.is_file() or source.suffix.lower() != '.zprj' or hashlib.sha256(source.read_bytes()).hexdigest() != params['expected_source_sha256']:
            raise ValueError('Saved source project differs from the recovery checkpoint')
        if not canvas._state()['configuration']:
            dispatch._module('native_setup').configure_canvas()
        _verify_closed_background_input(canvas)
        if scene() != current:
            raise ValueError('Current project changed immediately before recovery')
    except Exception as exc:
        raise PluginError('CUT_RECOVERY_PREFLIGHT_FAILED', str(exc), effects_started=False) from exc

    cut_state['recovery_inflight'] = original_id
    try:
        restored = open_project({'path': str(source), 'replace_current': True})
    except PluginError as exc:
        if exc.details.get('effects_started') is False:
            cut_state.pop('recovery_inflight', None)
        raise
    try:
        reopened = restored['scene']
        if (restored.get('verified') is not True or restored.get('source_file_unchanged') is not True
                or restored.get('source_sha256') != params['expected_source_sha256']
                or reopened['identity'] != current['identity'] or scene() != reopened):
            raise ValueError('Saved project reload or process identity did not verify')
        if deferred_readback:
            baseline = cut._capture_current(cut._native._api())
            if baseline['rows'] != reopened['patterns'] or scene() != reopened:
                raise ValueError('Reopened native data did not match fresh runtime readback')
            _verify_closed_background_input(canvas)
            if scene() != reopened:
                raise ValueError('Reopened scene changed before the recovery commit')
            if timed_out_after_return:
                atomic._retire_recovered_bridge()
            restored['cut_reconciliation'] = reconciliation
        cut_state.update(blocked=False, tickets={})
        cut_state.pop('pending_cut_readback', None)
        cut_state.pop('recovery_inflight', None)
    except Exception as exc:
        raise PluginError('CUT_RECOVERY_READBACK_FAILED', str(exc), effects_started=True) from exc
    restored.update(recovered_operation_id=original_id, recovery_action='SAVED_PROJECT_RELOADED',
                    original_mutation_status=previous['status'], requires_recovery=False)
    return restored


def _verify_closed_background_input(canvas, **options):
    observed = canvas.views({'view': 'all', **options})
    if observed['status'] != 'SUCCEEDED':
        error = observed.get('error') or {}
        raise ValueError('Background input inspection was rejected: ' + error.get('code', observed['status']))
    result = observed.get('result') or {}
    views = result.get('views') or []
    if (result.get('physical', {}).get('background_safe') is not True
            or not views or any(view.get('scene_item_grabber_absent') is not True for view in views)):
        raise ValueError('Current background input state is not closed')


OPERATIONS = {
    "project.open": open_project,
    "project.save_copy": save_copy,
    'project.recover_curve': recover_curve,
    'project.reconcile_released_curve_drag': reconcile_released_curve_drag,
    'project.recover_cut': recover_cut,
}
