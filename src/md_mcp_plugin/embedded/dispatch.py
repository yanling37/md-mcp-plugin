"""Fixed operation routing; host parameters never become Python statements."""
import importlib
import hashlib
from pathlib import Path
import sys
import traceback

from md_mcp_plugin.errors import PluginError
from .common import check_scene_token, digest

PROTOCOL = "md-mcp-plugin/1"
MODULES = frozenset(("scene", "project", "transforms", "curves", "cloth", "cloth_vertex_context", "cut", "cut_qt", "canvas", "selection", "mesh", "scene_adapter", "rest_adapter", "world_adapter", "world_frame", "world_grid_step", "world_numeric_probe", "rest_dock_identity", "rest_visible_contents", "rest_visible_recovery", "rest_pinching_override", "rest_simulation_context", "rest_cold", "gizmo_scale"))
_RECEIPTS = {}
_PAYLOADS = {}


def _module(name):
    """Refresh changed packaged Python without losing sys-scoped native state."""
    fullname = "md_mcp_plugin.embedded." + name
    existed = fullname in sys.modules
    module = importlib.import_module(fullname)
    revision = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
    previous = getattr(module, "_MDP_SOURCE_REVISION", None)
    if existed and previous != revision:
        module = importlib.reload(module)
    module._MDP_SOURCE_REVISION = revision
    return module


def execute(request):
    operation = request.get("operation")
    operation_id = request.get("operation_id")
    if operation == "runtime.receipt":
        receipt = _RECEIPTS.get(request.get("params", {}).get("operation_id"))
        return {"protocol": PROTOCOL, "status": "SUCCEEDED", "result": {"found": receipt is not None, "receipt": receipt}}
    fingerprint = digest({"operation": operation, "params": request.get("params"), "scene_token": request.get("scene_token")})
    if operation_id and operation_id in _RECEIPTS:
        if _PAYLOADS[operation_id] != fingerprint:
            return {"protocol": PROTOCOL, "status": "FAILED", "operation_id": operation_id, "result": None,
                    "error": {"code": "REQUEST_ID_REUSED", "message": "MD already saw different parameters for this operation ID"}}
        return _RECEIPTS[operation_id]
    response = {"protocol": PROTOCOL, "operation_id": operation_id, "status": "FAILED", "result": None, "error": None, "effects_started": False, "requires_recovery": False}
    started = False
    try:
        if not isinstance(operation, str) or operation.split(".", 1)[0] not in MODULES:
            raise PluginError("UNKNOWN_OPERATION", "Operation is not in the plugin allowlist")
        params = request.get("params")
        if not isinstance(params, dict):
            raise PluginError("INVALID_PARAMETERS", "Parameters must be an object")
        if operation == 'cloth.recover_positions' and (operation_id is None or request.get('scene_token') != params.get('scene_token')):
            raise PluginError('INVALID_REQUEST', 'A journaled current scene is required for Cloth recovery')
        if operation == 'rest_cold.begin' and (operation_id is None or request.get('scene_token') != params.get('scene_token')):
            raise PluginError('INVALID_REQUEST', 'A journaled current scene is required for Rest initialization')
        module = _module(operation.split(".", 1)[0])
        if operation == 'world_adapter.reconcile_rotation':
            if operation_id is None or request.get('scene_token') != params.get('scene_token'):
                raise PluginError('INVALID_REQUEST', 'A journaled current scene token is required for rotation observation')
            module = _module('world_rotation_reconciliation')
        if operation.startswith('scene_adapter.'):
            module = _module('native_setup').get_scene_adapter()
        function = module.OPERATIONS.get(operation)
        if function is None and operation.startswith('scene_adapter.') and module.__name__.rsplit('.', 1)[-1] in (
                'scene_adapter_v4', 'scene_adapter_v5', 'scene_adapter_v6'):
            function = module.OPERATIONS.get(module.__name__.rsplit('.', 1)[-1] + '.' + operation.split('.', 1)[1])
        if not callable(function):
            raise PluginError('UNSUPPORTED_OPERATION', 'Operation is not implemented', operation=operation)
        if operation.startswith("canvas.") or operation == 'project.recover_curve':
            _module("native_setup").configure_canvas()
        elif operation.startswith("scene_adapter."):
            _module("native_setup").configure_scene_adapter()
        elif operation == 'rest_adapter.inspect_host_controls':
            _module('native_setup').configure_rest_host_controls()
        elif operation == 'cloth.rest_shape' and params.get('method') == 'native_archive':
            if operation_id is None or request.get('scene_token') != params.get('scene_token'):
                raise PluginError('INVALID_REQUEST', 'A journaled current scene token is required for native Rest')
            _module('rest_selected_trial')
            _module('native_setup').configure_rest_selected_trial()
            _module('native_setup').configure_world_adapter()
            _module('world_frame')
        elif operation == 'cloth.reconcile_rest_archive':
            if operation_id is None or request.get('scene_token') != params.get('scene_token'):
                raise PluginError('INVALID_REQUEST', 'A journaled current scene token is required for Rest reconciliation')
            _module('native_setup').configure_world_adapter()
            _module('world_frame')
        elif operation.startswith('world_adapter.') and operation not in (
                'world_adapter.read_group_binding', 'world_adapter.inspect_topstitch_assignments'):
            _module('native_setup').configure_world_adapter()
        elif operation == 'world_frame.capture':
            _module('native_setup').configure_world_adapter()
        elif operation.startswith('world_numeric_probe.'):
            _module('native_setup').configure_world_numeric_probe()
        elif operation.startswith('rest_dock_identity.'):
            _module('native_setup').configure_rest_dock_identity()
        elif operation.startswith('rest_visible_contents.'):
            _module('native_setup').configure_rest_visible_contents()
        elif operation.startswith('rest_visible_recovery.'):
            _module('native_setup').configure_rest_visible_recovery()
        elif operation.startswith('rest_pinching_override.'):
            _module('native_setup').configure_rest_pinching_override()
        elif operation.startswith('rest_simulation_context.'):
            _module('native_setup').configure_rest_simulation_context()
        elif operation == 'world_grid_step.translate_axis':
            _module('world_frame')
        elif operation in ('gizmo_scale.inspect', 'gizmo_scale.prove_pair', 'gizmo_scale.recover_pair'):
            if request.get('scene_token') != params.get('scene_token') or operation != 'gizmo_scale.inspect' and operation_id is None:
                raise PluginError('INVALID_REQUEST', 'A current scene and journaled scale mutation are required')
            _module('native_setup').configure_gizmo_scale()
            if operation != 'gizmo_scale.inspect':
                _module('native_setup').configure_world_adapter()
                _module('world_frame')
        elif operation.startswith('cut_qt.') and operation != 'cut_qt.inspect':
            _module('native_setup').configure_cut_qt()
        elif operation == 'curves.set_handles':
            _module('native_setup').configure_curve_writer()
        if operation_id or request.get("scene_token") is not None:
            check_scene_token(request.get("scene_token"))
        started = True
        if operation == 'transforms.rotate' and operation_id is not None:
            result = _module('world_rotation_reconciliation').call_bound(function, params, operation_id, fingerprint,
                                                                        request.get('scene_token'))
        elif operation == 'cloth.set_positions' and operation_id is not None:
            result = function(params, request_id=operation_id, fingerprint=fingerprint)
        elif operation in ('gizmo_scale.prove_pair', 'gizmo_scale.recover_pair'):
            result = function(params, operation_id=operation_id)
        elif operation == 'rest_cold.begin':
            result = function(params, request_id=operation_id)
        else:
            result = function(params)
        if not isinstance(result, dict):
            raise PluginError("INVALID_NATIVE_RESULT", "Operation must return a structured object")
        native_status = str(result.get("status", "")).upper()
        if {"status", "result", "error"} <= result.keys():
            response["result"], response["error"] = result["result"], result["error"]
            payload = result["result"] if isinstance(result["result"], dict) else {}
        else:
            response["result"], response["error"] = result, result.get("error")
            payload = result
        effect = result.get("effects_started", payload.get("write_attempted", payload.get("mutation_attempted")))
        if effect is None:
            effect = bool(operation_id and native_status not in ("UNSUPPORTED", "NO_OP"))
        response["effects_started"] = bool(effect)
        recovery = bool(result.get("requires_recovery", payload.get("requires_recovery", False)))
        aliases = {"ERROR": "FAILED", "NO_OP": "NO_OP"}
        native_status = aliases.get(native_status, native_status)
        if native_status in ("UNSUPPORTED", "FAILED", "REJECTED", "PARTIAL", "UNKNOWN", "SUCCEEDED", "NO_OP") or (
                operation in ('rest_cold.begin', 'rest_cold.poll') and native_status == 'PENDING'):
            response["status"] = native_status
        elif result.get("supported") is False:
            response["status"] = "UNSUPPORTED"
            response["effects_started"] = False
        elif result.get("verified") is False or operation_id and result.get("verified") is not True:
            response["status"] = "REJECTED"
            recovery = bool(operation_id)
        else:
            response["status"] = "SUCCEEDED"
        response["requires_recovery"] = recovery or response["status"] in ("PARTIAL", "UNKNOWN")
    except PluginError as exc:
        response["error"] = exc.as_dict()
        response["status"] = "UNKNOWN" if started and operation_id and exc.details.get("effects_started") is not False else "FAILED"
        response["effects_started"] = bool(started and operation_id and exc.details.get("effects_started") is not False)
        response["requires_recovery"] = response["status"] == "UNKNOWN"
    except Exception as exc:
        response["error"] = {"code": "NATIVE_EXCEPTION", "message": str(exc), "type": type(exc).__name__, "traceback": traceback.format_exc(limit=4)}
        response["status"] = "UNKNOWN" if started and operation_id else "FAILED"
        response["effects_started"] = bool(started and operation_id)
        response["requires_recovery"] = response["status"] == "UNKNOWN"
    if operation_id:
        _RECEIPTS[operation_id] = response
        _PAYLOADS[operation_id] = fingerprint
    return response
