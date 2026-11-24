import re
import threading

from .errors import PluginError
from .embedded.cloth_position_recovery import RECOVERY as CLOTH_POSITION_RECOVERY, RESTORED as CLOTH_POSITION_RESTORED, WRITE as CLOTH_POSITION
from .embedded import curve_release_recovery as curve_release
from .journal import (CLOTH_REST, CLOTH_REST_OBSERVED, CLOTH_REST_RECONCILE,
                      GIZMO_SCALE_PAIR, GIZMO_SCALE_RECOVERY, GIZMO_SCALE_RESTORED,
                      GRID_AXIS, GRID_AXIS_RECOVERY, GRID_AXIS_RESTORED,
                      Journal,
                      WORLD_CAMERA, WORLD_CAMERA_RECOVERY, WORLD_CAMERA_OBSERVED,
                      WORLD_ROTATION, WORLD_ROTATION_OBSERVED, WORLD_ROTATION_RECONCILE)
from .transport import Transport


REST_COLD_START = 'rest_cold.begin'


class Context:
    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport or Transport(config)
        self.journal = Journal(config.data_dir)
        self.lock = threading.RLock()

    @staticmethod
    def failure(error, operation_id=None):
        return {"status": "FAILED", "operation_id": operation_id, "result": None, "error": error.as_dict()}

    def connect(self):
        try:
            with self.lock:
                pong = self.transport.ping()
                current = self.transport.execute("scene.snapshot", {})
            if current["status"] != "SUCCEEDED":
                return current
            return {"status": "SUCCEEDED", "operation_id": None, "result": {"connected": True, "listener": pong, "scene": current["result"], "background_only": True}, "error": None}
        except PluginError as exc:
            return self.failure(exc)

    def call(self, operation, params, *, mutation=False, request_id=None, scene_token=None):
        try:
            if not isinstance(params, dict) or not isinstance(operation, str) or re.fullmatch(r"[a-z_][a-z_0-9]*\.[a-z_][a-z_0-9]*", operation) is None:
                raise PluginError("INVALID_CALL", "A fixed operation name and parameter object are required")
            with self.lock:
                if not mutation:
                    if scene_token is not None and (not isinstance(scene_token, str) or re.fullmatch("[0-9a-f]{64}", scene_token) is None):
                        raise PluginError("INVALID_SCENE_TOKEN", "Supply a current scene_token from md_scene or omit it for an unscoped read")
                    return self.transport.execute(operation, params, scene_token=scene_token)
                if not isinstance(scene_token, str) or re.fullmatch("[0-9a-f]{64}", scene_token) is None:
                    raise PluginError("INVALID_SCENE_TOKEN", "Read md_scene and supply its current scene_token")
                record, fresh = self.journal.claim(request_id, operation, params, scene_token)
                if not fresh:
                    return record
                try:
                    receipt = self.transport.execute(operation, params, operation_id=request_id, scene_token=scene_token)
                except PluginError as exc:
                    status = "FAILED" if exc.details.get("outcome") == "not_sent" else "UNKNOWN"
                    receipt = {"status": status, "operation_id": request_id, "result": None, "error": exc.as_dict()}
                    if operation in (GRID_AXIS, REST_COLD_START) and status == 'FAILED':
                        receipt.update(effects_started=False, requires_recovery=False)
                if operation == REST_COLD_START:
                    if receipt['status'] == 'PENDING':
                        return self._rest_cold_pending(request_id, receipt)
                    return self._finish_rest_cold(request_id, receipt)
                return self.journal.finish(request_id, receipt)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def operation_status(self, request_id):
        try:
            with self.lock:
                record = self.journal.get(request_id)
                if record['operation'] == REST_COLD_START and record['status'] in ('PENDING', 'UNKNOWN'):
                    return self._observe_rest_cold(record)
                if record['operation'] == CLOTH_POSITION and (record.get('recovery') or {}).get('status') == CLOTH_POSITION_RESTORED:
                    if self.journal._matching_lease(request_id, CLOTH_POSITION):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] == CLOTH_REST and (record.get('recovery') or {}).get('status') == CLOTH_REST_OBSERVED:
                    if self.journal._matching_lease(request_id, CLOTH_REST):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] == GIZMO_SCALE_PAIR and (record.get('recovery') or {}).get('status') == GIZMO_SCALE_RESTORED:
                    if self.journal._matching_lease(request_id, GIZMO_SCALE_PAIR):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] == WORLD_ROTATION and (record.get('recovery') or {}).get('status') == WORLD_ROTATION_OBSERVED:
                    if self.journal._matching_lease(request_id, WORLD_ROTATION):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] == WORLD_CAMERA and (record.get('recovery') or {}).get('status') == WORLD_CAMERA_OBSERVED:
                    return record
                if record['operation'] == GRID_AXIS and (record.get('recovery') or {}).get('status') == GRID_AXIS_RESTORED:
                    if self.journal._matching_lease(request_id, GRID_AXIS):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] == GRID_AXIS and record['status'] not in ('PENDING', 'UNKNOWN'):
                    if self.journal._matching_lease(request_id, GRID_AXIS):
                        return self.journal.finish(request_id, record['receipt'])
                    return record
                if record['operation'] == curve_release.WRITE and (record.get('recovery') or {}).get('status') == curve_release.REPLACED:
                    if self.journal._matching_lease(request_id, curve_release.WRITE):
                        child = self.journal.get(record['recovery']['request_id'])
                        self.journal.finish(child['request_id'], child['receipt'])
                    return record
                if record['operation'] in (GRID_AXIS_RECOVERY, CLOTH_POSITION_RECOVERY, CLOTH_REST_RECONCILE, GIZMO_SCALE_RECOVERY,
                                           WORLD_ROTATION_RECONCILE, curve_release.RECOVERY) and record['status'] == 'SUCCEEDED':
                    return self.journal.finish(request_id, record['receipt'])
                if record["status"] not in ("PENDING", "UNKNOWN"):
                    return record
                receipt = self.transport.execute("runtime.receipt", {"operation_id": request_id})
                found = receipt.get("result", {})
                if found.get("found"):
                    return self.journal.finish(request_id, found["receipt"])
                return record
        except PluginError as exc:
            return self.failure(exc, request_id)

    def _observe_rest_cold(self, record):
        request_id = record['request_id']
        receipt = self.transport.execute('rest_cold.poll', {'request_id': request_id})
        if receipt['status'] == 'NO_OP':
            lookup = self.transport.execute('runtime.receipt', {'operation_id': request_id})
            cached = lookup.get('result') or {}
            receipt = cached.get('receipt') if cached.get('found') else None
            if receipt is None:
                return self.journal.get(request_id)
        if receipt['status'] == 'PENDING':
            return self._rest_cold_pending(request_id, receipt)
        return self._finish_rest_cold(request_id, dict(receipt, operation_id=request_id))

    def _rest_cold_pending(self, request_id, receipt):
        record = self.journal.get(request_id)
        if record['status'] in ('PENDING', 'UNKNOWN'):
            record['result'] = receipt.get('result')
        return record

    def _finish_rest_cold(self, request_id, receipt):
        with self.journal._rest_reconcile_finalization():
            record = self.journal.get(request_id)
            if record['status'] not in ('PENDING', 'UNKNOWN'):
                return record
            return self.journal.finish(request_id, receipt)

    def recover_curve_operation(self, blocked_request_id, request_id, scene_token, expected_source_sha256):
        return self._recover_project_operation('project.recover_curve', blocked_request_id, request_id, scene_token, expected_source_sha256)

    def reconcile_released_curve_drag(self, blocked_request_id, request_id, scene_token, expected_source_sha256):
        return self._recover_project_operation(curve_release.RECOVERY, blocked_request_id, request_id, scene_token, expected_source_sha256)

    def recover_cut_operation(self, blocked_request_id, request_id, scene_token, expected_source_sha256):
        return self._recover_project_operation('project.recover_cut', blocked_request_id, request_id, scene_token, expected_source_sha256)

    def reconcile_world_camera(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the current scene before reconciliation')
            original = self.journal.get(blocked_request_id)
            original_params = original.get('params')
            if type(original_params) is not dict or original_params.get('view') not in ('front', 'left', 'quarter'):
                raise PluginError('RECOVERY_NOT_APPLICABLE', 'An original fixed camera view is required')
            params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token, 'view': original_params['view']}
            if 'fit_garment' in original_params:
                if type(original_params['fit_garment']) is not bool:
                    raise PluginError('RECOVERY_NOT_APPLICABLE', 'The original camera fit option must be a boolean')
                params['fit_garment'] = original_params['fit_garment']
            return self._call_recovery(WORLD_CAMERA_RECOVERY, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def recover_grid_axis(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the current scene before Grid recovery')
            params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token}
            with self.lock:
                original = self.journal.get(blocked_request_id)
                if original['operation'] != GRID_AXIS or original['scene_token'] != scene_token:
                    raise PluginError('RECOVERY_NOT_APPLICABLE', 'Grid recovery requires its original request and scene')
                if original['status'] in ('PENDING', 'UNKNOWN') and not original.get('recovery'):
                    if (original.get('result') or {}).get('grid_recovery_available') is not True:
                        self.operation_status(blocked_request_id)
                return self._call_recovery(GRID_AXIS_RECOVERY, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def reconcile_cloth_rest_archive(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the current scene before recovery')
            params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token}
            return self._call_recovery(CLOTH_REST_RECONCILE, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def recover_cloth_positions(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the original current scene before Cloth recovery')
            with self.lock:
                original = self.journal.get(blocked_request_id)
                if original['operation'] != CLOTH_POSITION or original['scene_token'] != scene_token:
                    raise PluginError('RECOVERY_NOT_APPLICABLE', 'Cloth recovery requires its original position request and scene')
                if original['status'] in ('PENDING', 'UNKNOWN') and not original.get('recovery'):
                    if not (original.get('result') or {}).get('position_recovery'):
                        self.operation_status(blocked_request_id)
                params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token}
                return self._call_recovery(CLOTH_POSITION_RECOVERY, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def reconcile_world_rotation(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the original current scene before rotation observation')
            with self.lock:
                parent = self.journal.get(blocked_request_id)
                params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token,
                          'original_params': parent['params']}
                return self._call_recovery(WORLD_ROTATION_RECONCILE, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def recover_gizmo_scale_pair(self, blocked_request_id, request_id, scene_token):
        try:
            if type(scene_token) is not str or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the current scene before recovery')
            params = {'blocked_request_id': blocked_request_id, 'scene_token': scene_token}
            return self._call_recovery(GIZMO_SCALE_RECOVERY, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def _recover_project_operation(self, operation, blocked_request_id, request_id, scene_token, expected_source_sha256):
        try:
            if not isinstance(scene_token, str) or re.fullmatch('[0-9a-f]{64}', scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN', 'Read the current scene before recovery')
            if not isinstance(expected_source_sha256, str) or re.fullmatch('[0-9a-f]{64}', expected_source_sha256) is None:
                raise PluginError('INVALID_SOURCE_HASH', 'The saved project SHA256 is required')
            params = {'blocked_request_id': blocked_request_id, 'expected_source_sha256': expected_source_sha256}
            return self._call_recovery(operation, blocked_request_id, request_id, scene_token, params)
        except PluginError as exc:
            return self.failure(exc, request_id)

    def _call_recovery(self, operation, blocked_request_id, request_id, scene_token, params):
        with self.lock:
            record, fresh = self.journal.claim_recovery(request_id, blocked_request_id, params, scene_token, operation=operation)
            if not fresh:
                if operation in (GRID_AXIS_RECOVERY, CLOTH_POSITION_RECOVERY, CLOTH_REST_RECONCILE,
                                 GIZMO_SCALE_RECOVERY, WORLD_ROTATION_RECONCILE, curve_release.RECOVERY) and record['status'] == 'SUCCEEDED':
                    return self.journal.finish(request_id, record['receipt'])
                return record
            try:
                receipt = self.transport.execute(operation, params, operation_id=request_id, scene_token=scene_token)
            except PluginError as exc:
                receipt = {'status': 'FAILED' if exc.details.get('outcome') == 'not_sent' else 'UNKNOWN',
                           'operation_id': request_id, 'result': None, 'error': exc.as_dict()}
                if operation == curve_release.RECOVERY and receipt['status'] == 'FAILED':
                    receipt.update(effects_started=False, requires_recovery=False)
            return self.journal.finish(request_id, receipt)

    def reconcile_property_operation(self, blocked_request_id, request_id, scene_token):
        try:
            if not isinstance(scene_token,str) or re.fullmatch('[0-9a-f]{64}',scene_token) is None:
                raise PluginError('INVALID_SCENE_TOKEN','Read the current scene before reconciliation')
            operation='rest_visible_recovery.reconcile_open'
            params={'blocked_request_id':blocked_request_id,'scene_token':scene_token}
            with self.lock:
                record,fresh=self.journal.claim_recovery(request_id,blocked_request_id,params,scene_token,operation=operation)
                if not fresh:return record
                try:
                    receipt=self.transport.execute(operation,params,operation_id=request_id,scene_token=scene_token)
                except PluginError as exc:
                    receipt={'status':'FAILED' if exc.details.get('outcome')=='not_sent' else 'UNKNOWN',
                             'operation_id':request_id,'result':None,'error':exc.as_dict()}
                return self.journal.finish(request_id,receipt)
        except PluginError as exc:
            return self.failure(exc,request_id)
