"""Durable request identity and a cross-process mutation lease."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .errors import PluginError
from .embedded import cloth_position_recovery as cloth_position, curve_release_recovery as curve_release

CLOTH_REST = 'cloth.rest_shape'
CLOTH_REST_RECONCILE = 'cloth.reconcile_rest_archive'
CLOTH_REST_OBSERVED = 'REST_ARCHIVE_OBSERVED'
GIZMO_SCALE_PAIR = 'gizmo_scale.prove_pair'
GIZMO_SCALE_RECOVERY = 'gizmo_scale.recover_pair'
GIZMO_SCALE_RESTORED = 'GIZMO_SCALE_RESTORED'
WORLD_CAMERA = 'world_adapter.set_canonical_view'
WORLD_CAMERA_RECOVERY = 'world_adapter.reconcile_camera'
WORLD_CAMERA_OBSERVED = 'WORLD_CAMERA_OBSERVED'
WORLD_CAMERA_FAILURE = 'CANONICAL_VIEW_NOT_VERIFIED'
WORLD_ROTATION = 'transforms.rotate'
WORLD_ROTATION_RECONCILE = 'world_adapter.reconcile_rotation'
WORLD_ROTATION_OBSERVED = 'WORLD_ROTATION_OBSERVED'
GRID_AXIS = 'world_grid_step.translate_axis'
GRID_AXIS_RECOVERY = 'world_grid_step.recover_axis'
GRID_AXIS_RESTORED = 'GRID_STEP_WIDGET_RESTORED'


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Journal:
    def __init__(self, root):
        self.root = Path(root)
        self.requests = self.root / "requests"
        self.lease = self.root / "mutation.lock"
        self.requests.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _replace(temporary, target):
        for attempt, pause in enumerate((0.01, 0.03, 0.1, 0)):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(pause)

    def _path(self, request_id):
        if not isinstance(request_id, str) or re.fullmatch("[0-9a-f]{32}", request_id) is None:
            raise PluginError("INVALID_REQUEST_ID", "A 32-character lowercase UUID hex request_id is required")
        return self.requests / (request_id + ".json")

    def get(self, request_id):
        path = self._path(request_id)
        if not path.is_file():
            raise PluginError("UNKNOWN_REQUEST", "This data directory has no such request")
        return json.loads(path.read_text(encoding="utf8"))

    def claim(self, request_id, operation, params, scene_token):
        path = self._path(request_id)
        payload = {"operation": operation, "params": params, "scene_token": scene_token}
        fingerprint = hashlib.sha256(encoded(payload).encode()).hexdigest()
        if path.exists():
            existing = self.get(request_id)
            if existing["fingerprint"] != fingerprint:
                raise PluginError("REQUEST_ID_REUSED", "The same request_id cannot identify different parameters")
            return existing, False
        lease = {"request_id": request_id, "operation": operation, "host_pid": os.getpid()}
        try:
            with self.lease.open("x", encoding="utf8") as stream:
                stream.write(encoded(lease))
        except FileExistsError as exc:
            raise PluginError("MUTATION_BUSY", "Another or unresolved mutation owns the lease", lease=str(self.lease)) from exc
        record = {"request_id": request_id, "operation_id": request_id, "fingerprint": fingerprint, **payload, "status": "PENDING", "result": None, "error": None}
        try:
            with path.open("x", encoding="utf8") as stream:
                stream.write(encoded(record))
        except Exception:
            self.release(request_id)
            raise
        return record, True

    def finish(self, request_id, receipt):
        record = self.get(request_id)
        if record['operation'] == curve_release.RECOVERY:
            return self._finish_curve_release_recovery(request_id, receipt)
        if record['operation'] == cloth_position.RECOVERY and receipt['status'] == 'SUCCEEDED':
            return self._finish_cloth_position_recovery(request_id, record, receipt)
        if record['operation'] == WORLD_ROTATION_RECONCILE and receipt['status'] == 'SUCCEEDED':
            return self._finish_rotation_reconcile(request_id, record, receipt)
        if record['operation'] == CLOTH_REST_RECONCILE and receipt['status'] == 'SUCCEEDED':
            return self._finish_rest_reconcile(request_id, record, receipt)
        if record['operation'] == GIZMO_SCALE_RECOVERY and receipt['status'] == 'SUCCEEDED':
            return self._finish_gizmo_scale_recovery(request_id, record, receipt)
        if record['operation'] == GRID_AXIS_RECOVERY and receipt['status'] == 'SUCCEEDED':
            return self._finish_grid_axis_recovery(request_id, record, receipt)
        if record['operation'] in ('project.recover_cut', WORLD_CAMERA_RECOVERY) and receipt['status'] == 'SUCCEEDED':
            return self._finish_bound_recovery(request_id, record, receipt)
        record.update(status=receipt["status"], result=receipt.get("result"), error=receipt.get("error"), receipt=receipt)
        safe_terminal = record["status"] in ("SUCCEEDED", "NO_OP") or (
            record["status"] in ("FAILED", "UNSUPPORTED", "REJECTED")
            and receipt.get("effects_started") is False
            and not receipt.get("requires_recovery"))
        payload=receipt.get('result') or {}
        provider=payload.get('provider_receipt') or {}
        verification=payload.get('verification') or {}
        safe_terminal=safe_terminal or (record['operation']=='curves.set_handles'
            and record['status'] in ('FAILED','UNSUPPORTED','REJECTED') and not receipt.get('requires_recovery')
            and provider.get('geometry_effects_started') is False and provider.get('closed_input_batch_verified') is True
            and verification.get('scene_unchanged') is True and verification.get('scene_identity_preserved') is True)
        if not safe_terminal and record['operation'] == 'transforms.rotate':
            safe_terminal = self._closed_world_rotation_mismatch(record, receipt)
        if record['operation'] == 'transforms.rotate' and (record['params'].get('ring_plan') or {}).get('gizmo_scale_steps') == 2:
            from .embedded.gizmo_scale_series import closed_rotation_scale

            if receipt.get('effects_started') is True:
                scale_closed = closed_rotation_scale(payload, request_id)
                safe_terminal = scale_closed and receipt.get('requires_recovery') is False and (
                    safe_terminal or record['status'] == 'REJECTED' and payload.get('geometry_write_attempted') is False)
                if not safe_terminal and record['status'] in ('SUCCEEDED', 'NO_OP'):
                    record.update(status='UNKNOWN', error={'code': 'GIZMO_SCALE_ROTATION_NOT_CLOSED',
                                  'message': 'The scaled rotation did not prove the display state was restored'})
        if not safe_terminal and record['operation'] == CLOTH_REST:
            from .embedded.cloth_rest_runtime import original_override_restored

            try:
                safe_terminal = (record['status'] == 'FAILED' and receipt.get('requires_recovery') is False
                                 and receipt.get('operation_id') == request_id and payload.get('closed_no_rest_change') is True
                                 and payload.get('native_button_clicks') == 0 and original_override_restored(payload))
            except (KeyError, TypeError):
                safe_terminal = False
        if record['operation'] == GRID_AXIS:
            safe_terminal = receipt.get('operation_id') == request_id and receipt.get('requires_recovery') is False and (
                receipt.get('effects_started') is False and record['status'] in ('REJECTED', 'UNSUPPORTED', 'FAILED', 'NO_OP')
                or receipt.get('effects_started') is True and record['status'] in ('SUCCEEDED', 'FAILED', 'NO_OP')
                and payload.get('closed_grid_axis_transaction') is True
                and payload.get('original_step_widget_restored') is True
                and (record['status'] != 'SUCCEEDED' or self._grid_axis_move_verified(record, payload)))
            if not safe_terminal and record['status'] in ('SUCCEEDED', 'NO_OP'):
                record.update(status='UNKNOWN', result=None,
                              error={'code': 'GRID_AXIS_RECEIPT_NOT_VERIFIED',
                                     'message': 'Grid mutation receipt did not prove a closed transaction'})
        self._write_record(request_id, record)
        if safe_terminal:
            self.release(request_id)
        recovery_labels={'project.recover_curve':'SAVED_PROJECT_RELOADED',
                         'rest_visible_recovery.reconcile_open':'PROPERTY_COMMAND_OBSERVED'}
        if record['operation'] in recovery_labels and record['status'] == 'SUCCEEDED' and payload.get('verified') is True:
            parent_id = record['params']['blocked_request_id']
            parent = self.get(parent_id)
            parent['recovery'] = {'request_id': request_id, 'status': recovery_labels[record['operation']], 'original_status': parent['status']}
            parent_path = self._path(parent_id)
            parent_tmp = parent_path.with_name(parent_path.name + '.' + uuid.uuid4().hex + '.tmp')
            parent_tmp.write_text(encoded(parent), encoding='utf8')
            self._replace(parent_tmp, parent_path)
            self.release(parent_id)
        return record

    @contextmanager
    def _rest_reconcile_finalization(self):
        with self._reconciliation_lock('rest-reconcile.guard'):
            yield

    @contextmanager
    def _reconciliation_lock(self, name):
        # The OS releases this lock on process exit. A second reconciler can
        # never delete a new mutation lease while completing the old parent.
        with (self.root / name).open('a+b') as stream:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    @staticmethod
    def _curve_release_parent(parent, parent_id):
        if (type(parent) is not dict or parent.get('request_id') != parent_id or parent.get('operation_id') != parent_id
                or parent.get('operation') != curve_release.WRITE or parent.get('status') != 'PARTIAL'
                or not curve_release.is_sha256(parent.get('scene_token')) or type(parent.get('params')) is not dict):
            raise ValueError('Original partial curve request did not match')
        payload = {key: parent[key] for key in ('operation', 'params', 'scene_token')}
        expected = hashlib.sha256(encoded(payload).encode()).hexdigest()
        original = parent.get('receipt')
        if (parent.get('fingerprint') != expected or type(original) is not dict
                or parent.get('result') != original.get('result') or parent.get('error') != original.get('error')):
            raise ValueError('Original curve payload or receipt no longer matches its sealed request')
        return curve_release.released_curve_drag_proof(original, parent_id, original_params=parent['params'])

    def _claim_curve_release_recovery(self, request_id, parent_id, payload, fingerprint, path):
        params = payload['params']
        if (request_id == parent_id or set(params) != {'blocked_request_id', 'expected_source_sha256'}
                or params.get('blocked_request_id') != parent_id or not curve_release.is_sha256(params.get('expected_source_sha256'))
                or not curve_release.is_sha256(payload['scene_token'])):
            raise PluginError('RECOVERY_NOT_APPLICABLE', 'A distinct recovery ID, current scene token and source SHA are required')
        with self._reconciliation_lock('curve-release-reconcile.guard'):
            if path.exists():
                existing = self.get(request_id)
                if existing.get('fingerprint') != fingerprint:
                    raise PluginError('REQUEST_ID_REUSED', 'Recovery ID identifies different parameters')
                return existing, False
            parent = self.get(parent_id)
            try:
                self._curve_release_parent(parent, parent_id)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PluginError('RECOVERY_NOT_APPLICABLE', str(exc)) from exc
            if not self._matching_lease(parent_id, curve_release.WRITE):
                raise PluginError('RECOVERY_LEASE_MISMATCH', 'This unresolved curve operation must own the mutation lease')
            if parent.get('recovery') is not None:
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'The curve receipt already has a verified recovery')
            prior_id = parent.get('curve_release_recovery_request_id')
            if prior_id is not None:
                prior = self.get(prior_id)
                receipt = prior.get('receipt') or {}
                if not (prior.get('status') in ('FAILED', 'REJECTED', 'UNSUPPORTED')
                        and receipt.get('effects_started') is False and receipt.get('requires_recovery') is False):
                    raise PluginError('RECOVERY_PENDING', 'Read the existing curve recovery request before attempting another',
                                      request_id=prior_id)
            record = {'request_id': request_id, 'operation_id': request_id, 'fingerprint': fingerprint, **payload,
                      'status': 'PENDING', 'result': None, 'error': None, 'recovery_for': parent_id,
                      'original_request_fingerprint': parent['fingerprint']}
            with path.open('x', encoding='utf8') as stream:
                stream.write(encoded(record))
            parent['curve_release_recovery_request_id'] = request_id
            self._write_record(parent_id, parent)
            return record, True

    def _finish_curve_release_recovery(self, request_id, receipt):
        with self._reconciliation_lock('curve-release-reconcile.guard'):
            record = self.get(request_id)
            parent_id = record['recovery_for']
            parent = self.get(parent_id)
            linked = {'request_id': request_id, 'status': curve_release.REPLACED, 'original_status': 'PARTIAL'}
            has_lease = self._matching_lease(parent_id, curve_release.WRITE)
            if parent.get('recovery') == linked and record['status'] == 'SUCCEEDED' and not has_lease:
                return record
            result = receipt.get('result')
            error = receipt.get('error')
            status = receipt['status']
            if status == 'SUCCEEDED':
                try:
                    proof = self._curve_release_parent(parent, parent_id)
                except (TypeError, ValueError, OverflowError):
                    proof = None
                valid = (proof is not None and has_lease and parent.get('recovery') in (None, linked)
                         and parent.get('curve_release_recovery_request_id') == request_id
                         and parent['fingerprint'] == record.get('original_request_fingerprint')
                         and receipt.get('operation_id') == request_id and receipt.get('requires_recovery') is False
                         and error is None and type(result) is dict and result.get('recovered_operation_id') == parent_id
                         and result.get('recovery_action') == curve_release.REPLACED
                         and result.get('source_sha256') == record['params']['expected_source_sha256']
                         and result.get('scene_token') == record['scene_token']
                         and result.get('native_fingerprints') == proof['native_fingerprints']
                         and encoded(result.get('target_xy_mm')) == encoded(proof['target_xy_mm'])
                         and all(result.get(key) is True for key in ('verified', 'native_scene_unchanged',
                                                                    'old_bridge_retired', 'successor_bridge_verified'))
                         and self._curve_successor_attested(result))
                if not valid:
                    status, result = 'UNKNOWN', None
                    error = {'code': 'CURVE_RELEASE_RECOVERY_RECEIPT_NOT_VERIFIED',
                             'message': 'Bridge replacement could not be bound to the exact partial curve receipt and lease'}
            record.update(status=status, result=result, error=error, receipt=receipt)
            self._write_record(request_id, record)
            if status == 'SUCCEEDED':
                parent['recovery'] = linked
                self._write_record(parent_id, parent)
                if self._matching_lease(parent_id, curve_release.WRITE):
                    self.lease.unlink()
            return record

    @staticmethod
    def _curve_successor_attested(result):
        process = result.get('process_identity')
        old = result.get('predecessor_bridge_sha256')
        new = result.get('successor_bridge_sha256')
        epoch = result.get('native_successor_epoch')
        return (type(process) is dict and set(process) == {'pid', 'creation_filetime'}
                and all(type(value) is int and value > 0 for value in process.values())
                and curve_release.is_sha256(old) and curve_release.is_sha256(new) and old != new
                and type(epoch) is int and epoch == 1)

    def _finish_rest_reconcile(self, request_id, record, receipt):
        parent_id = record['params']['blocked_request_id']
        payload = receipt.get('result') or {}
        with self._rest_reconcile_finalization():
            parent = self.get(parent_id)
            prior = parent.get('recovery')
            before = (parent.get('result') or {}).get('before_references') or {}
            observed = payload.get('current_references') or {}
            target = parent['params'].get('pattern_uuid')
            valid = (receipt.get('operation_id') == request_id and receipt.get('effects_started') is False
                     and receipt.get('requires_recovery') is False and payload.get('verified') is True
                     and payload.get('recovery_action') == CLOTH_REST_OBSERVED
                     and payload.get('original_mutation_status') == 'UNKNOWN' and parent['status'] == 'UNKNOWN'
                     and parent['operation'] == CLOTH_REST and payload.get('recovered_operation_id') == record.get('recovery_for') == parent_id
                     and payload.get('scene_token') == record['scene_token'] == parent['scene_token']
                     and payload.get('repeated_native_button_clicks') == 0 and type(before) is dict and type(observed) is dict and target in before
                     and before.keys() == observed.keys() and all(before[uid] == observed[uid] for uid in before if uid != target))
            if valid and prior is not None and prior['request_id'] != request_id:
                record.update(status='NO_OP', result={'prior_reconciliation_request_id': prior['request_id']},
                              error=None, receipt=receipt)
            elif valid and (prior is not None or self._matching_lease(parent_id, CLOTH_REST)):
                record.update(status='SUCCEEDED', result=payload, error=None, receipt=receipt)
                self._write_record(request_id, record)
                if prior is None:
                    parent['recovery'] = {'request_id': request_id, 'status': CLOTH_REST_OBSERVED,
                                          'original_status': parent['status']}
                    self._write_record(parent_id, parent)
                self.release(parent_id)
                return record
            else:
                record.update(status='UNKNOWN', result=None, receipt=receipt,
                              error={'code': 'REST_ARCHIVE_RECONCILE_NOT_VERIFIED',
                                     'message': 'The original native Rest operation is still held'})
            self._write_record(request_id, record)
            return record

    @staticmethod
    def _cloth_position_parent(parent, blocked_id, token):
        receipt = parent.get('receipt') or {}
        return (parent.get('operation') == cloth_position.WRITE and parent.get('status') == 'UNKNOWN'
                and parent.get('request_id') == parent.get('operation_id') == blocked_id
                and parent.get('scene_token') == token and parent.get('result') == receipt.get('result')
                and (parent.get('params') or {}).get('method') == 'meshbox_plane'
                and cloth_position.parent_witness(receipt, blocked_id, parent.get('fingerprint'), token))

    def _finish_cloth_position_recovery(self, request_id, record, receipt):
        parent_id, token = record['recovery_for'], record['scene_token']
        payload = receipt.get('result') or {}
        link = {'request_id': request_id, 'status': cloth_position.RESTORED, 'original_status': 'UNKNOWN'}
        with self._rest_reconcile_finalization():
            parent = self.get(parent_id)
            original = parent.get('result') or {}
            proof = original.get('position_recovery') or {}
            original_mode, mode = original.get('mode_before') or {}, payload.get('mode_after') or {}
            baseline, fresh = original.get('mesh_before') or {}, payload.get('mesh_after') or {}
            prior = parent.get('recovery')
            valid = (self._cloth_position_parent(parent, parent_id, token)
                     and receipt.get('operation_id') == request_id and receipt.get('requires_recovery') is False
                     and record.get('params') == {'blocked_request_id': parent_id, 'scene_token': token}
                     and payload.get('blocked_request_id') == parent_id and payload.get('scene_token') == token
                     and payload.get('scene_identity') == original.get('scene_identity')
                     and payload.get('recovery_action') == cloth_position.RESTORED
                     and payload.get('original_mutation_status') == 'UNKNOWN'
                     and all(payload.get(key) is True for key in ('verified', 'source_file_unchanged', 'frame_quarantined', 'transaction_closed'))
                     and cloth_position.closed_inverse(payload.get('restore'), original)
                     and mode.get('mode') == 'MovePattern' and mode.get('physical_state_unchanged') is True
                     and mode.get('sdk_selection') == original_mode.get('sdk_selection')
                     and payload.get('camera_after') == proof.get('camera')
                     and type(fresh.get('capture_id')) is str and fresh['capture_id'] != baseline.get('capture_id')
                     and all(fresh.get(key) == baseline.get(key) for key in ('arrays_sha256', 'topology_fingerprint', 'export_options'))
                     and (prior == link or prior is None and self._matching_lease(parent_id, cloth_position.WRITE)))
            if not valid:
                record.update(status='UNKNOWN', result=None, receipt=receipt, error={
                    'code': 'CLOTH_POSITION_RECOVERY_RECEIPT_NOT_VERIFIED',
                    'message': 'The original Cloth position request remains held'})
                self._write_record(request_id, record)
                return record
            record.update(status='SUCCEEDED', result=payload, error=None, receipt=receipt)
            self._write_record(request_id, record)
            if prior is None:
                parent['recovery'] = link
                self._write_record(parent_id, parent)
            self.release(parent_id)
            return record

    @staticmethod
    def _open_gizmo_scale_pair(parent):
        try:
            result = parent['result']
            from .embedded.gizmo_scale_series import pending_series

            return (result.get('schema') == 'md-gizmo-scale-series/2' and parent['status'] == 'UNKNOWN'
                    and parent['operation'] == GIZMO_SCALE_PAIR and parent['receipt']['requires_recovery'] is True
                    and pending_series(result, parent['request_id'], parent['scene_token']))
        except (TypeError, KeyError):
            return False

    def _finish_gizmo_scale_recovery(self, request_id, record, receipt):
        parent_id = record['params']['blocked_request_id']
        result = receipt.get('result') or {}
        with self._rest_reconcile_finalization():
            parent = self.get(parent_id)
            prior = parent.get('recovery')
            valid = (self._open_gizmo_scale_pair(parent) and receipt.get('operation_id') == request_id
                     and receipt.get('effects_started') is True and receipt.get('requires_recovery') is False
                     and result.get('blocked_request_id') == result.get('reconciles_request_id') == record.get('recovery_for') == parent_id
                     and result.get('display_scale_restoration_verified') is True and result.get('transaction_closed') is True)
            if valid and prior is not None and prior['request_id'] != request_id:
                record.update(status='NO_OP', result={'prior_recovery_request_id': prior['request_id']}, error=None, receipt=receipt)
            elif valid and (prior is not None or self._matching_lease(parent_id, GIZMO_SCALE_PAIR)):
                record.update(status='SUCCEEDED', result=result, error=None, receipt=receipt)
                self._write_record(request_id, record)
                if prior is None:
                    parent['recovery'] = {'request_id': request_id, 'status': GIZMO_SCALE_RESTORED,
                                          'original_status': parent['status']}
                    self._write_record(parent_id, parent)
                self.release(parent_id)
                return record
            else:
                record.update(status='UNKNOWN', result=result, receipt=receipt,
                              error={'code': 'GIZMO_SCALE_RECOVERY_NOT_VERIFIED', 'message': 'The original scale operation is still held'})
            self._write_record(request_id, record)
            return record

    @staticmethod
    def _rotation_observation_parent(parent, params, token):
        from .embedded.world_rotation_reconciliation import SCHEMA

        try:
            receipt = parent['receipt']
            evidence = receipt['result']['rotation_recovery']
            native = evidence['native']
            blocked = parent['request_id']
            fingerprint = hashlib.sha256(encoded({'operation': WORLD_ROTATION, 'params': parent['params'],
                                                 'scene_token': token}).encode()).hexdigest()
            if (parent['operation'] == WORLD_ROTATION and parent['status'] in ('UNKNOWN', 'PARTIAL')
                    and parent['status'] == receipt['status'] and receipt['operation_id'] == blocked
                    and receipt['effects_started'] is receipt['requires_recovery'] is True
                    and parent['result'] == receipt['result'] and evidence['schema'] == SCHEMA
                    and evidence['parent_request_id'] == blocked and parent['scene_token'] == evidence['scene_token'] == token
                    and parent['fingerprint'] == evidence['parent_fingerprint'] == fingerprint
                    and params == {'blocked_request_id': blocked, 'scene_token': token, 'original_params': parent['params']}
                    and native['status'] == 'SUCCEEDED' and native['purpose'] == 'rotation_ring'
                    and native['dispatch_completed'] is native['paired_release_verified'] is True
                    and native['press_outstanding'] is False):
                return evidence
        except (KeyError, TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _rotation_observation_verified(evidence, parent, result):
        from .embedded.transforms import rotation_matrix

        try:
            observation = result['observation']
            matches = result['requested_matrix_observed']
            sha = result['current_world_arrays_sha256']
            first, second = result['capture_ids']
            offset = evidence['companion_preflight']
            proof = result['matrix_acceptance']
            params = parent['params']
            profile = params.get('verification_profile', 'strict')
            matrix_flag = {'strict': 'exported_geometry_matches_requested_matrix',
                           'positions': 'exported_positions_match_requested_matrix'}[profile]
            return (result['verified'] is True and result['recovery_action'] == WORLD_ROTATION_OBSERVED
                    and result['recovered_operation_id'] == parent['request_id']
                    and result['original_mutation_status'] == parent['status']
                    and result['scene_identity'] == evidence['scene_identity']
                    and result['scene_token'] == parent['scene_token']
                    and result['parent_fingerprint'] == parent['fingerprint']
                    and result['evidence_sha256'] == hashlib.sha256(encoded(evidence).encode()).hexdigest()
                    and all(type(result[key]) is int and result[key] == 0 for key in ('native_input_calls', 'sdk_setter_calls'))
                    and type(sha) is str and re.fullmatch('[0-9a-f]{64}', sha) is not None
                    and all(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None for value in (first, second))
                    and first != second and type(matches) is bool and proof[matrix_flag] is matches
                    and proof['matrix_world_mm'] == rotation_matrix(params['axis_world'], params['angle_degrees'], params['pivot_world_mm'])
                    and proof['tolerance_mm'] == params['tolerance_mm']
                    and all(proof.get(key) is True for key in ('same_scene', 'distinct_captures', 'stable_label_bijection',
                        'normal_readback_complete', 'uv_readback_complete', 'outside_target_export_arrays_exact'))
                    and all(proof['observed'].get(key) is True for key in ('topology_and_index_maps_exact', 'uv_exact',
                                                                         'other_export_groups_all_exact'))
                    and (result['topstitch_original_offset_sdk'] is None if offset is None else
                         type(result['topstitch_original_offset_sdk']) is float
                         and result['topstitch_original_offset_sdk'] == offset['original_offset_sdk'])
                    and (observation == 'REQUESTED' and matches is True
                         or observation == 'UNCHANGED' and matches is False and sha == evidence['before_world_arrays_sha256']
                         or observation == 'MISMATCH' and matches is False and sha == evidence['closed_terminal_arrays_sha256']))
        except (KeyError, TypeError, ValueError):
            return False

    def _finish_rotation_reconcile(self, request_id, record, receipt):
        blocked = record['params']['blocked_request_id']
        result = receipt.get('result') or {}
        with self._rest_reconcile_finalization():
            parent = self.get(blocked)
            prior = parent.get('recovery')
            evidence = self._rotation_observation_parent(parent, record['params'], record['scene_token'])
            valid = (evidence is not None and record.get('recovery_for') == blocked and receipt.get('operation_id') == request_id
                     and receipt.get('effects_started') is False and receipt.get('requires_recovery') is False
                     and self._rotation_observation_verified(evidence, parent, result))
            if valid and prior is not None and prior['request_id'] != request_id:
                record.update(status='NO_OP', result={'prior_reconciliation_request_id': prior['request_id']},
                              error=None, receipt=receipt)
            elif valid and (prior is not None or self._matching_lease(blocked, WORLD_ROTATION)):
                record.update(status='SUCCEEDED', result=result, error=None, receipt=receipt)
                self._write_record(request_id, record)
                if prior is None:
                    parent['recovery'] = {'request_id': request_id, 'status': WORLD_ROTATION_OBSERVED,
                                          'original_status': parent['status']}
                    self._write_record(blocked, parent)
                if self._matching_lease(blocked, WORLD_ROTATION):
                    self.release(blocked)
                return record
            else:
                record.update(status='UNKNOWN', result=None, receipt=receipt,
                              error={'code': 'WORLD_ROTATION_OBSERVATION_NOT_VERIFIED',
                                     'message': 'The original world rotation is still held'})
            self._write_record(request_id, record)
            return record

    @staticmethod
    def _closed_world_rotation_mismatch(record, receipt):
        from .embedded.transforms import rotation_matrix
        from .embedded.world_rotation_adapter import MISMATCH, ROUTE, closed_matrix_result

        try:
            payload, params = receipt['result'], record['params']
            ring, frame = params['ring_plan'], payload['ring_frame']
            profile = params.get('verification_profile', 'strict')
            native = payload['native']
            if (record['request_id'] != receipt['operation_id'] or receipt['status'] != 'FAILED'
                    or receipt['error']['code'] != MISMATCH or receipt['effects_started'] is not True
                    or receipt['requires_recovery'] is not False or payload['route'] != ROUTE
                    or payload['verified'] is not False or payload['requested_world_rotation_verified'] is not False
                    or payload['closed_world_rotation_mismatch_verified'] is not True
                    or payload['pattern_uuid'] != params['pattern_uuid']
                    or payload['selection']['pattern_uuid'] != params['pattern_uuid']
                    or profile not in ('strict', 'positions') or payload.get('verification_profile', 'strict') != profile
                    or frame.get('ring_color', 'blue') != ring.get('ring_color', 'blue')
                    or payload.get('include_assigned_topstitch', False) is not params.get('include_assigned_topstitch', False)
                    or params.get('include_assigned_topstitch') is True and payload.get('assigned_topstitch_refresh') is None
                    or not record['scene_token'] == params['scene_token'] == frame['scene_token']
                    or frame['frame_request_id'] != ring['frame_request_id'] or frame['roi_px'] != ring['roi_px']
                    or frame['points'] != [ring[key] for key in ('start', 'activation', 'end')]
                    or frame['view_fingerprint'] != native['before']['view_fingerprint']):
                return False
            points = frame['points']
            events = native['mouse_events']
            before, after = (payload[key] for key in ('before_mesh', 'after_mesh'))
            if (native['purpose'] != 'rotation_ring' or native['status'] != 'SUCCEEDED'
                    or [event['stage'] for event in events] != ['hover', 'press', 'activation', 'move', 'release']
                    or [event['viewport_logical'] for event in events] != [points[0], *points, points[-1]]
                    or not all(event['sent'] is True and event['returned'] is True for event in events)
                    or not before['scene_token'] == after['scene_token'] == record['scene_token']
                    or before['scene_identity'] != after['scene_identity'] or before['capture_id'] == after['capture_id']):
                return False
            proof = closed_matrix_result(payload)
            requested = rotation_matrix(params['axis_world'], params['angle_degrees'], params['pivot_world_mm'])
            tolerance = proof['tolerance_mm']
            if profile == 'positions' and (proof.get('exported_positions_match_requested_matrix') is not False
                                           or payload.get('requested_world_position_matrix_verified') is not False):
                return False
            return (proof['exported_geometry_matches_requested_matrix'] is False
                    and proof['matrix_world_mm'] == requested and tolerance == params['tolerance_mm']
                    and (proof['maximum_vertex_error_mm'] > tolerance or profile == 'strict' and proof['maximum_normal_error'] > 1e-6
                         or proof['expected_maximum_motion_mm'] <= 2*tolerance
                         or proof['observed_maximum_motion_mm'] <= tolerance))
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError):
            return False

    def _write_record(self, request_id, record):
        target = self._path(request_id)
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(encoded(record), encoding='utf8')
        self._replace(temporary, target)

    @staticmethod
    def _grid_axis_move_verified(record, payload):
        profile = record['params'].get('verification_profile', 'strict')
        if profile == 'strict':
            return payload.get('requested_matrix_verified') is True
        try:
            if (profile != 'position_with_obj_normal_quantization' or payload.get('verification_profile') != profile
                    or payload.get('requested_position_verified') is not True
                    or payload.get('target_exported_normals_within_obj_quantum') is not True):
                return False
            params, identity = record['params'], payload['scene_identity']
            target, evidence = payload['target_scope'], payload['obj_normal_quantization']
            motion = payload['movement']['result']
            before, after = (motion[key] for key in ('before_mesh', 'after_mesh'))
            quantum_max = evidence['maximum_component_delta_quanta']
            count, changed = evidence['target_normal_count'], evidence['changed_target_normal_count']
            axis, distance = params['axis'], params['distance_mm']
            token = record['scene_token']
            return (axis in ('x', 'y', 'z') and payload['requested_delta_mm'] == [distance if c == axis else 0 for c in 'xyz']
                    and payload['scene_token'] == params['scene_token'] == token
                    and params['pattern_uuid'] == target['pattern_uuid'] == motion['selection']['pattern_uuid']
                    and target['group_name'] == motion['matrix_acceptance']['observed']['group_name']
                    and before['scene_identity'] == after['scene_identity'] == identity
                    and before['scene_token'] == after['scene_token'] == token
                    and evidence['verified'] is True and evidence['native_exact_normals_verified'] is False
                    and evidence['decimal_places'] == 6 and evidence['quantum'] == 1e-6
                    and type(count) is int and count == motion['matrix_acceptance']['target_normal_count'] and count > 0
                    and type(changed) is int and 0 <= changed <= count
                    and type(quantum_max) is list and len(quantum_max) == 3
                    and all(type(value) is int and value in (0, 1) for value in quantum_max)
                    and all(type(sha) is str and re.fullmatch('[a-f0-9]{64}', sha) and sha == capture['export']['obj_asset']['sha256']
                            for sha, capture in ((evidence['before_obj_sha256'], before), (evidence['after_obj_sha256'], after))))
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _grid_axis_parent(parent, params, token):
        blocked_id = params.get('blocked_request_id')
        receipt = parent.get('receipt') or {}
        result = receipt.get('result') or {}
        temporary = (result.get('temporary_step') or {}).get('result') or {}
        identity = result.get('scene_identity') or {}
        step = temporary.get('original_step_mm')
        return (set(params) == {'blocked_request_id', 'scene_token'}
                and parent.get('request_id') == parent.get('operation_id') == receipt.get('operation_id') == blocked_id
                and parent.get('operation') == GRID_AXIS and parent.get('status') == receipt.get('status')
                and parent.get('status') in ('UNKNOWN', 'PARTIAL')
                and parent.get('scene_token') == result.get('scene_token') == params.get('scene_token') == token
                and parent.get('result') == result and receipt.get('effects_started') is True
                and receipt.get('requires_recovery') is True and result.get('grid_recovery_available') is True
                and result.get('geometry_closed_or_absent') is True
                and result.get('original_step_widget_restored') is False
                and type(identity.get('pid')) is int and identity['pid'] > 0
                and type(identity.get('creation_filetime')) is int and identity['creation_filetime'] > 0
                and temporary.get('operation') == 'set_temporary'
                and temporary.get('process_id') == identity['pid']
                and temporary.get('process_creation_filetime') == str(identity['creation_filetime'])
                and temporary.get('restore_required') is True and temporary.get('original_page_restored') is True
                and temporary.get('return_pair_closed') is True and type(step) in (int, float) and 0 < step <= 100000)

    def _finish_grid_axis_recovery(self, request_id, record, receipt):
        params = record['params']
        parent_id = record['recovery_for']
        parent = self.get(parent_id)
        linked = {'request_id': request_id, 'status': GRID_AXIS_RESTORED, 'original_status': parent['status']}
        if (parent.get('recovery') == linked and record['status'] == 'SUCCEEDED'
                and not self._matching_lease(parent_id, GRID_AXIS)):
            return record
        result = receipt.get('result') or {}
        original = parent.get('result') or {}
        temporary = (original.get('temporary_step') or {}).get('result') or {}
        identity = original.get('scene_identity') or {}
        restored = result.get('restore') or {}
        raw = restored.get('result') or {}
        step = temporary.get('original_step_mm')
        valid = (self._grid_axis_parent(parent, params, record['scene_token'])
                 and params.get('blocked_request_id') == result.get('blocked_request_id') == parent_id
                 and receipt.get('operation_id') == request_id and receipt.get('effects_started') is True
                 and receipt.get('requires_recovery') is False and result.get('verified') is True
                 and result.get('grid_restore_verified') is True and result.get('original_step_widget_restored') is True
                 and result.get('scene_token') == record['scene_token'] and result.get('scene_identity') == identity
                 and type(result.get('restored_step_mm')) in (float, int)
                 and result.get('original_step_mm') == result.get('restored_step_mm') == step
                 and restored.get('status') == raw.get('status') == 'SUCCEEDED' and raw.get('operation') == 'restore'
                 and raw.get('process_id') == identity.get('pid')
                 and raw.get('process_creation_filetime') == str(identity.get('creation_filetime'))
                 and raw.get('step_mm') == raw.get('original_step_mm') == step
                 and raw.get('restore_required') is False and raw.get('return_pair_closed') is True
                 and raw.get('grid_widget_commit_verified') is True and raw.get('original_page_restored') is True
                 and all(type(raw.get(k)) is int and raw[k] == 0 for k in ('OS_input_calls', 'focus_calls', 'event_pump_calls'))
                 and self._matching_lease(parent_id, GRID_AXIS))
        if not valid:
            record.update(status='UNKNOWN', result=None, receipt=receipt,
                          error={'code': 'GRID_RECOVERY_RECEIPT_NOT_VERIFIED',
                                 'message': 'Grid restoration could not be bound to the original request and lease'})
            self._write_record(request_id, record)
            return record
        record.update(status='SUCCEEDED', result=result, error=None, receipt=receipt)
        self._write_record(request_id, record)
        parent['recovery'] = linked
        self._write_record(parent_id, parent)
        self.release(parent_id)
        return record

    def _finish_bound_recovery(self, request_id, record, receipt):
        params = record['params']
        parent_id = params.get('blocked_request_id')
        is_cut = record['operation'] == 'project.recover_cut'
        is_camera = record['operation'] == WORLD_CAMERA_RECOVERY
        if is_cut:
            parent_operation, recovery_action, kind = 'cut_qt.execute', 'SAVED_PROJECT_RELOADED', 'cut'
            error_message = 'Reload receipt could not be bound to the original Cut lease'
        else:
            parent_operation, recovery_action, kind = WORLD_CAMERA, WORLD_CAMERA_OBSERVED, 'world_camera'
            error_message = 'Camera observation could not be bound to the original request and lease'
        lease_check = f'original_{kind}_lease'
        result = receipt.get('result')
        result = result if type(result) is dict else {}
        checks = {
            'child_operation_id': receipt.get('operation_id') == request_id,
            'child_parent_binding': record.get('recovery_for') == parent_id and parent_id != request_id,
            'verified': result.get('verified') is True,
            'requires_recovery_false': receipt.get('requires_recovery') is False,
            'recovered_operation_id': result.get('recovered_operation_id') == parent_id,
            'recovery_action': result.get('recovery_action') == recovery_action,
        }
        try:
            parent = self.get(parent_id)
        except (PluginError, OSError, ValueError):
            parent = {}
        if type(parent) is not dict:
            parent = {}
        original_record_matches = parent.get('request_id') == parent_id and parent.get('operation') == parent_operation
        if is_cut:
            source_hash = params.get('expected_source_sha256')
            checks['source_file_unchanged'] = result.get('source_file_unchanged') is True
            checks['source_sha256'] = (type(source_hash) is str and re.fullmatch('[0-9a-f]{64}', source_hash) is not None
                                       and result.get('source_sha256') == source_hash)
            checks['original_cut_record'] = (original_record_matches
                                             and parent.get('status') in ('PARTIAL', 'UNKNOWN', 'FAILED', 'REJECTED'))
        elif is_camera:
            token = record.get('scene_token')
            checks['original_world_camera_record'] = self._matches_original_camera(parent, params, token)
            checks['current_scene_token'] = result.get('scene_token') == token
            checks['view'] = result.get('view') == params.get('view')
            checks['current_camera_observed'] = result.get('current_camera_observed') is True
            if 'fit_garment' in params:
                checks['fit_garment'] = result.get('fit_garment') is params['fit_garment']
                checks['fit_observation_read_only'] = (receipt.get('effects_started') is False
                                                        and result.get('requested_framing_verified') is False)
        checks[lease_check] = self._matching_lease(parent_id, parent_operation)

        def unverified(failures):
            record.update(status='UNKNOWN', result=None, receipt=receipt,
                          error={'code': f'{kind.upper()}_RECOVERY_RECEIPT_NOT_VERIFIED',
                                 'message': error_message,
                                 'failed_checks': failures})
            self._write_record(request_id, record)
            return record

        failed = [key for key, passed in checks.items() if not passed]
        if failed:
            return unverified(failed)

        # Persist the verified recovery before linking it to the original outcome.
        record.update(status='SUCCEEDED', result=receipt['result'], error=receipt.get('error'), receipt=receipt)
        self._write_record(request_id, record)
        if not self._matching_lease(parent_id, parent_operation):
            return unverified([lease_check])
        parent['recovery'] = {'request_id': request_id, 'status': recovery_action, 'original_status': parent['status']}
        self._write_record(parent_id, parent)
        if not self._matching_lease(parent_id, parent_operation):
            return unverified([lease_check])
        self.lease.unlink()
        return record

    def _matching_lease(self, request_id, operation):
        try:
            lease = json.loads(self.lease.read_text(encoding='utf8'))
        except (OSError, ValueError):
            return False
        return type(lease) is dict and lease.get('request_id') == request_id and lease.get('operation') == operation

    @staticmethod
    def _matches_original_camera(parent, params, scene_token):
        blocked_id = params.get('blocked_request_id')
        view = params.get('view')
        receipt, error = parent.get('receipt'), parent.get('error')
        if type(receipt) is not dict or type(error) is not dict:
            return False
        receipt_error = receipt.get('error')
        original_params = {'view': view}
        if 'fit_garment' in params:
            if type(params['fit_garment']) is not bool:
                return False
            original_params['fit_garment'] = params['fit_garment']
        return (type(scene_token) is str and re.fullmatch('[0-9a-f]{64}', scene_token) is not None
                and params.get('scene_token') == scene_token and parent.get('scene_token') == scene_token
                and set(params) <= {'blocked_request_id', 'scene_token', 'view', 'fit_garment'}
                and view in ('front', 'left', 'quarter') and parent.get('params') == original_params
                and parent.get('request_id') == blocked_id and parent.get('operation_id') == blocked_id
                and parent.get('operation') == WORLD_CAMERA and parent.get('status') == 'UNKNOWN'
                and error.get('code') == WORLD_CAMERA_FAILURE and receipt.get('operation_id') == blocked_id
                and receipt.get('status') == 'UNKNOWN' and type(receipt_error) is dict
                and receipt_error.get('code') == WORLD_CAMERA_FAILURE)

    def claim_recovery(self, request_id, blocked_id, params, scene_token, *, operation='project.recover_curve'):
        parents={'project.recover_curve':'curves.set_handles',
                 'project.recover_cut':'cut_qt.execute',
                 curve_release.RECOVERY:curve_release.WRITE,
                 cloth_position.RECOVERY:cloth_position.WRITE,
                 CLOTH_REST_RECONCILE:CLOTH_REST,
                 WORLD_ROTATION_RECONCILE:WORLD_ROTATION,
                 GIZMO_SCALE_RECOVERY:GIZMO_SCALE_PAIR,
                 WORLD_CAMERA_RECOVERY:WORLD_CAMERA,
                 GRID_AXIS_RECOVERY:GRID_AXIS,
                 'rest_visible_recovery.reconcile_open':'rest_visible_contents.open_simulation_properties'}
        if operation not in parents:
            raise PluginError('UNKNOWN_RECOVERY_OPERATION','Recovery must have an explicit known parent operation')
        path = self._path(request_id)
        payload = {'operation': operation, 'params': params, 'scene_token': scene_token}
        fingerprint = hashlib.sha256(encoded(payload).encode()).hexdigest()
        if operation == curve_release.RECOVERY:
            return self._claim_curve_release_recovery(request_id, blocked_id, payload, fingerprint, path)
        if path.exists():
            record = self.get(request_id)
            if record['fingerprint'] != fingerprint:
                raise PluginError('REQUEST_ID_REUSED', 'Recovery ID identifies different parameters')
            return record, False
        parent = self.get(blocked_id)
        if parent['operation'] != parents[operation] or parent['status'] not in ('PARTIAL', 'UNKNOWN', 'FAILED', 'REJECTED'):
            raise PluginError('RECOVERY_NOT_APPLICABLE', 'Recovery must match its original unresolved operation')
        if operation == cloth_position.RECOVERY:
            if (parent.get('recovery') is not None or params != {'blocked_request_id': blocked_id, 'scene_token': scene_token}
                    or not self._cloth_position_parent(parent, blocked_id, scene_token)):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Recovery requires the original pre-selection Cloth frame witness')
            lease_matches = self._matching_lease(blocked_id, cloth_position.WRITE)
        elif operation == CLOTH_REST_RECONCILE:
            from .embedded.cloth_rest_runtime import closed_reference_observation

            try:
                closed = closed_reference_observation(parent['result'])
            except (KeyError, TypeError):
                closed = False
            if (not closed or parent['status'] != 'UNKNOWN' or parent.get('recovery') is not None
                    or parent['params'].get('method') != 'native_archive' or parent.get('scene_token') != scene_token
                    or params != {'blocked_request_id': blocked_id, 'scene_token': scene_token}
                    or (parent.get('receipt') or {}).get('requires_recovery') is not True):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Recovery requires the original closed Rest archive observation')
            lease_matches = self._matching_lease(blocked_id, CLOTH_REST)
        elif operation == GIZMO_SCALE_RECOVERY:
            if (parent.get('recovery') is not None or not self._open_gizmo_scale_pair(parent)
                    or parent.get('scene_token') != scene_token
                    or params != {'blocked_request_id': blocked_id, 'scene_token': scene_token}):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Recovery requires the original closed Increase and pending inverse')
            lease_matches = self._matching_lease(blocked_id, GIZMO_SCALE_PAIR)
        elif operation == WORLD_ROTATION_RECONCILE:
            if parent.get('recovery') is not None or self._rotation_observation_parent(parent, params, scene_token) is None:
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Observation requires the original closed world rotation receipt')
            lease_matches = self._matching_lease(blocked_id, WORLD_ROTATION)
        elif operation == WORLD_CAMERA_RECOVERY:
            if params.get('blocked_request_id') != blocked_id or not self._matches_original_camera(parent, params, scene_token):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Camera recovery requires the original scene, view and readback failure')
            lease_matches = self._matching_lease(blocked_id, WORLD_CAMERA)
        elif operation == GRID_AXIS_RECOVERY:
            if (params.get('blocked_request_id') != blocked_id or parent.get('recovery') is not None
                    or not self._grid_axis_parent(parent, params, scene_token)):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'Grid recovery requires the original settled native write receipt')
            lease_matches = self._matching_lease(blocked_id, GRID_AXIS)
        else:
            lease_matches = self.lease.is_file() and json.loads(self.lease.read_text(encoding='utf8')).get('request_id') == blocked_id
        if not lease_matches:
            raise PluginError('RECOVERY_LEASE_MISMATCH', 'This unresolved operation must still own the mutation lease')
        record = {'request_id': request_id, 'operation_id': request_id, 'fingerprint': fingerprint, **payload,
                  'status': 'PENDING', 'result': None, 'error': None, 'recovery_for': blocked_id}
        with path.open('x', encoding='utf8') as stream:
            stream.write(encoded(record))
        return record, True

    def release(self, request_id):
        if self.lease.is_file():
            lease = json.loads(self.lease.read_text(encoding="utf8"))
            if lease.get("request_id") == request_id:
                self.lease.unlink()
