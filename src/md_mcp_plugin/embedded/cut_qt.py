"""Trusted Qt Cut provider boundary with independent native partition readback."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

from . import cut as _native
from . import curves
from . import scene_adapter as _scene
from . import cut_qt_atomic as _atomic
from .common import digest, scene

STATE_KEY = "_md_mcp_plugin_cut_qt_state"
PREFIX = "cutqt1:"
TTL_SECONDS = 60.0
MAX_TICKETS = 32


class CutQtError(ValueError):
    def __init__(self, code, message, status="REJECTED"):
        self.code, self.status = code, status
        super().__init__(message)


def _state():
    value = getattr(sys, STATE_KEY, None)
    if value is None:
        value = {"selector": None, "reader": None, "tickets": {}, "busy": False, "blocked": False,
                 "effect_boundary_entered": False, "cut_boundary_entered": False}
        setattr(sys, STATE_KEY, value)
    return value


def _description(name, evidence):
    if type(name) is not str or not name.strip() or len(name) > 128 or type(evidence) is not str or not evidence.strip() or len(evidence) > 4096:
        raise ValueError("Trusted integration needs a bounded provider name and an explicit evidence description")


def install_line_selector(select=None, verify=None, *, name="", evidence=""):
    """Trusted core only; not a public certificate/coordinate/selection setter.

    select(plan) may select only plan's current whole native line. It returns an
    opaque provider receipt. verify(plan, receipt, current_adapter_inspection)
    must independently re-read its semantic binding and return the exact
    identity record below. An owner hit or generic coordinate picker is not a
    sufficient implementation. The hook is intentionally absent by default.
    """
    state = _state()
    if state["busy"]:
        raise RuntimeError("Cannot replace a provider during an operation")
    if select is None and verify is None:
        state.update(selector=None, tickets={})
        return
    if not callable(select) or not callable(verify):
        raise TypeError("Both trusted select and independent semantic verifier callbacks are required")
    _description(name, evidence)
    state.update(selector={"kind": "read_stable", "select": select, "verify": verify, "name": name, "evidence": evidence,
                           "epoch": uuid.uuid4().hex}, tickets={})


def install_atomic_line_selector(candidate=None, verify=None, *, name="", evidence="", surface_readback=False):
    """Trusted core only: prepare a hit candidate WITHOUT selecting anything.

    candidate(plan, complete_native_json) returns a bounded, internally derived
    {binding:{native_view_ref,view_fingerprint,point}, evidence:{...}}. verify
    independently re-establishes exact native-line→current-frame semantics.
    Its closed identity record is specified in _verify_atomic. A pure geometry
    fit alone is insufficient. No default implementation, public installer,
    arbitrary pixel or caller certificate exists. Both callbacks are read-only.
    Actual P/R and Cut execute in ONE C ABI call, with no intervening SDK read.
    """
    state = _state()
    if state['busy']: raise RuntimeError('Cannot replace a provider during an operation')
    if candidate is None and verify is None:
        state.update(selector=None,tickets={}); return
    if not callable(candidate) or not callable(verify): raise TypeError('Both internal candidate and semantic verifier required')
    if type(surface_readback) is not bool:
        raise TypeError('Surface readback configuration must be a boolean')
    _description(name,evidence)
    state.update(selector={'kind':'atomic','candidate':candidate,'verify':verify,'name':name,'evidence':evidence,
                           'epoch':uuid.uuid4().hex,'surface_readback':surface_readback},tickets={})


def _atomic_inspection():
    response = _atomic.inspect_current()
    value = response.get('result') if type(response) is dict else None
    if (type(value) is not dict or response.get('status')!='SUCCEEDED' or response.get('effects_started') is not False
            or response.get('requires_recovery') is not False or value.get('verified') is not True):
        raise CutQtError('ATOMIC_MODE1_VIEW_UNAVAILABLE','Fresh atomic bridge-owned Mode1 view required','UNSUPPORTED')
    after=value['after']
    return {'native_view_ref':_text(after.get('native_view_ref'),'native_view_ref'),
            'view_fingerprint':_token(after.get('view_fingerprint')),
            'mapping_fingerprint':_token(after.get('mapping_fingerprint')),
            'scene_token':_token(scene()['scene_token'])}


def _verify_atomic(provider, plan, receipt, inspection, capture):
    if (type(receipt) is not dict or set(receipt)!={'binding','evidence'} or type(receipt['evidence']) is not dict
            or type(receipt['binding']) is not dict or set(receipt['binding'])!={'native_view_ref','view_fingerprint','point'}):
        raise CutQtError('INVALID_ATOMIC_CANDIDATE','Closed internally generated current-frame candidate required','UNSUPPORTED')
    binding=receipt['binding']; _atomic._point(binding['point'])
    if any(binding[k]!=inspection[k] for k in ('native_view_ref','view_fingerprint')):
        raise CutQtError('STALE_ATOMIC_CANDIDATE','Candidate belongs to another current atomic view')
    expected={k:plan[k] for k in ('pattern_uuid','shape_uuid','line_uuid','topology_fingerprint')}
    expected.update(inspection,selection_nonce=plan['selection_nonce'],candidate_sha256=digest(receipt))
    observed=provider['verify'](copy.deepcopy(plan),copy.deepcopy(receipt),copy.deepcopy(inspection),copy.deepcopy(capture['native']))
    if type(observed) is not dict or set(observed)!=set(expected) or observed!=expected:
        raise CutQtError('SEMANTIC_LINE_CANDIDATE_UNPROVEN','Independent core verifier did not bind this native line to the fresh candidate/frame','UNSUPPORTED')
    return expected


def _prepare_atomic(provider, plan, target, before, api):
    receipt=provider['candidate'](copy.deepcopy(plan),copy.deepcopy(before['native']))
    if len(json.dumps(receipt,allow_nan=False,ensure_ascii=False).encode())>262144:
        raise CutQtError('ATOMIC_CANDIDATE_TOO_LARGE','Bounded evidence/path-SHA references required')
    after=_capture_current(api); _unchanged(before,after,target)
    current=_atomic_inspection()
    semantic=_verify_atomic(provider,plan,receipt,current,after)
    final=_capture_current(api); _unchanged(after,final,target)
    if scene()['scene_token']!=current['scene_token']: raise CutQtError('STALE_SCENE','Scene changed during read-only candidate preparation')
    ref=PREFIX+uuid.uuid4().hex
    _state()['tickets'][ref]={'plan':plan,'target':target,'capture':{'scene_fingerprint':before['scene_fingerprint']},
                            'receipt':copy.deepcopy(receipt),'inspection':current,'semantic':semantic,'expires':time.monotonic()+TTL_SECONDS}
    return {'status':'SUCCEEDED','verified':True,'fulfilled':False,'effects_started':False,'selection_ref':ref,
            'scene_token':current['scene_token'],'expires_in_seconds':TTL_SECONDS,'candidate_prepared_without_selection':True,
            'selector':{'name':provider['name'],'evidence':provider['evidence']},'candidate_receipt':receipt,
            'Qt_Cut_trigger_calls':0,'sdk_cut_calls':0,'verification_scope':'Trusted current native-line/frame candidate only; no click or Cut yet'}


def _surface_capture(pattern_uuids, expected_scene_token):
    from .dispatch import _module

    world = _module('world_adapter')
    current = scene()
    if current['scene_token'] != expected_scene_token:
        raise CutQtError('SURFACE_SCENE_CHANGED', 'OBJ capture must belong to the scene used for the Cut geometry readback')
    exported = world._mesh_read()
    group_names = []
    for identity in pattern_uuids:
        matches = [row for row in current['patterns'] if row['pattern_uuid'] == identity]
        if len(matches) != 1:
            raise CutQtError('SURFACE_OWNER_NOT_UNIQUE', 'A unique current UUID is required for every surface group')
        name = matches[0]['information']['name']
        world.bind_export_labels(current, exported, identity, name)
        group_names.append(name)
    if scene() != current:
        raise CutQtError('SURFACE_SCENE_CHANGED', 'Scene changed during OBJ surface capture')
    return exported, group_names


def _execute_atomic(provider, ticket, before, api, token, surface=False):
    target=ticket['target']
    surface_before = _surface_capture([target['pattern_uuid']], token) if surface else None
    current=_atomic_inspection()
    if current!=ticket['inspection']: raise CutQtError('STALE_ATOMIC_VIEW','Current frame/view/mode changed')
    _verify_atomic(provider,ticket['plan'],ticket['receipt'],current,before)
    _unchanged(before,_capture_current(api),target)
    # All SDK reads end BEFORE the one native P/R→Cut batch.
    before_scene = scene()
    if before_scene['scene_token']!=token: raise CutQtError('STALE_SCENE','Scene changed before atomic batch')
    source_sha256 = None
    source = Path(before_scene.get('identity', {}).get('project_path', ''))
    if source.is_absolute() and source.suffix.lower() == '.zprj':
        try:
            with source.open('rb') as stream:
                source_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError:
            pass  # A saved checkpoint is optional until recovery is needed.
    if scene() != before_scene:
        raise CutQtError('STALE_SCENE', 'Scene changed while binding the saved project')
    _state()['effect_boundary_entered']=True; _state()['cut_boundary_entered']=True
    action,exception=None,None
    try: action=_atomic.select_and_cut(copy.deepcopy(ticket['receipt']['binding']))
    except Exception as exc: exception={'type':type(exc).__name__,'message':str(exc)}
    raw_result = action.get('result') if type(action) is dict else None
    raw = raw_result if type(raw_result) is dict else {}
    calls=raw.get('QAction_trigger_calls'); known=calls if type(calls) is int and calls in (0,1) else None
    safezero=(type(action) is dict and action.get('status') in ('REJECTED','UNSUPPORTED')
              and action.get('effects_started') is False and action.get('requires_recovery') is False and exception is None
              and type(raw_result) is dict and raw.get('verified') is False
              and ('QAction_trigger_calls' not in raw or known==0)
              and ('event_calls' not in raw or type(raw['event_calls']) is int and raw['event_calls']==0))
    if safezero: known=0
    result={'status':'UNKNOWN','verified':False,'fulfilled':False,'effects_started':True,'requires_recovery':True,'do_not_retry':True,
            'sdk_cut_calls':0,'Qt_Cut_trigger_calls':known,'adapter_response':action,'adapter_exception':exception,'target':target,
            'surface_preservation':{'required':surface,'3d':False,'rest':False,'uv':False},'cut_and_sew_verified':False,
            'single_native_click_cut_batch':True}
    if surface_before is not None:
        source, groups = surface_before
        result['surface_preservation']['before_capture'] = {
            'capture_id': source.get('capture_id'), 'snapshot_id': source.get('snapshot_id'),
            'scene_token': source.get('scene_token'), 'groups': groups,
            'obj_asset': source.get('export', {}).get('obj_asset'),
        }
    action_complete = (type(action) is dict and action.get('status')=='SUCCEEDED' and action.get('effects_started') is True
            and action.get('requires_recovery') is False and raw.get('verified') is True and known==1
            and raw.get('Cut_action_returned') is True and raw.get('dispatch_completed') is True
            and exception is None)
    if source_sha256 is not None and (action_complete or _atomic.closed_cut_before_observation_timeout(raw)):
        _state()['pending_cut_readback'] = {
            'identity': before_scene['identity'], 'scene_token': token,
            'source_sha256': source_sha256, 'before': before, 'target': target,
            'surface_before': surface_before,
        }
        result['scene_token_before'] = token
    # Always independent native capture/partition AFTER the entire C ABI call,
    # including exceptions, partial click, zero-event rejection and False.
    try:
        try:
            after = _capture_current(api)
        except (_native._CutError, CutQtError) as exc:
            if not action_complete or exc.code not in ('SNAPSHOT_UUID_MISMATCH', 'SCENE_CHANGED_DURING_READ'):
                raise
            # The native action and release are complete. A single fresh export
            # can read the new owner set without repeating any input.
            result['first_post_cut_read_error'] = {'code': exc.code, 'message': str(exc)}
            after = _capture_current(api)
        proof = _native._partition(before,after,target['pattern_uuid'],target['curve'])
        current_scene = scene()
        if current_scene['identity'] != before_scene['identity'] or current_scene['patterns'] != after['rows']:
            raise CutQtError('POST_CUT_SCENE_CHANGED', 'Native partition readback no longer matches the current project and PointInfo rows')
        result.update(readback=proof, mutation_observed=(before['board_fingerprint'] != after['board_fingerprint']
                                                        or current_scene['scene_token'] != token),
                      new_pattern_refs=_result_pattern_refs(after, proof),
                      scene_token_after=current_scene['scene_token'], scene_identity_after=current_scene.get('identity'),
                      after_native_fingerprint=after['scene_fingerprint'])
    except Exception as exc:
        result['error']={'code':'POST_CUT_READBACK_FAILED','type':type(exc).__name__,'message':str(exc)}; return result
    if action_complete and proof['native_partition_observed'] is True:
        if surface_before is not None:
            try:
                from .dispatch import _module

                exported, groups = _surface_capture(proof['resulting_pattern_uuids'], current_scene['scene_token'])
                source, source_groups = surface_before
                result['surface_preservation']['after_capture'] = {
                    'capture_id': exported.get('capture_id'), 'snapshot_id': exported.get('snapshot_id'),
                    'scene_token': exported.get('scene_token'), 'groups': groups,
                    'obj_asset': exported.get('export', {}).get('obj_asset'),
                }
                checked = _module('cut_surface_readback').verify_cut_surface(
                    source, exported, source_groups, groups,
                    native_boundary_partition_verified=proof['checks']['boundary_partition_verified'],
                )
                result['surface_preservation']['readback'] = checked
                result['surface_preservation']['3d'] = checked['verified'] is True
            except Exception as exc:
                result['surface_preservation']['error'] = {'code': getattr(exc, 'code', 'CUT_SURFACE_CAPTURE_FAILED'),
                                                           'message': str(exc)}
            if not result['surface_preservation']['3d']:
                result.update(status='PARTIAL', outcome='native_partition_verified_surface_unverified',
                              error={'code':'CUT_SURFACE_READBACK_REJECTED','message':'Native partition succeeded and the exported surface check did not pass'})
                return result
        outcome = 'native_partition_and_exported_surface_verified' if surface else 'native_2d_partition_verified'
        result.update(status='SUCCEEDED',verified=True,fulfilled=True,requires_recovery=False,outcome=outcome)
    elif action_complete and result['mutation_observed'] is False:
        result.update(status='NO_OP', requires_recovery=False, outcome='no_native_partition')
    elif action_complete:
        result.update(status='PARTIAL', outcome='native_partition_readback_unverified')
    elif safezero and result['mutation_observed'] is False:
        result.update(status='REJECTED',effects_started=False,requires_recovery=False,outcome='zero_cut_preflight_rejected')
    if result['status'] in ('SUCCEEDED', 'NO_OP', 'REJECTED'):
        _state().pop('pending_cut_readback', None)
    return result


def _result_pattern_refs(after, proof):
    refs = []
    for identity in proof['resulting_pattern_uuids']:
        row, paper, fingerprint = _native._owner(after, identity)
        refs.append({'pattern_uuid': identity, 'topology_fingerprint': fingerprint,
                     'current_pattern_index': row['index'],
                     'outline_line_uuids': [line['ID'] for line in paper['ShapeInfo']['LineList']]})
    return refs


def install_native_reader(reader=None, *, name="", evidence=""):
    """Optional trusted native JSON reader; returns data, never a verified flag.

    Without this hook, use the existing cut._capture public ExportPatternJSON
    route. The hook cannot replace fresh public runtime/PI rows or change their
    UUID domain. No project import/replacement is permitted.
    """
    state = _state()
    if state["busy"]:
        raise RuntimeError("Cannot replace a reader during an operation")
    if reader is not None:
        if not callable(reader):
            raise TypeError("Native reader must be callable")
        _description(name, evidence)
    state.update(reader=None if reader is None else {"call": reader, "name": name, "evidence": evidence}, tickets={})


def _capture_current(api):
    reader = _state()["reader"]
    if reader is None:
        captured = _native._capture(api)
        return _normalize_capture(captured["native"], captured["rows"])
    rows = _native.pattern_rows()
    native = copy.deepcopy(reader["call"]())
    # Canonical JSON validates finite/serializable values and binds complete data.
    if len(json.dumps(native, allow_nan=False, ensure_ascii=False).encode("utf-8")) > 32 * 1024 * 1024:
        raise CutQtError("NATIVE_SNAPSHOT_TOO_LARGE", "Native data exceeds the existing 32MiB read bound")
    papers = native.get("PatternList") if type(native) is dict else None
    if (type(papers) is not list or len(papers) > 4096 or any(type(p) is not dict for p in papers)
            or len(rows) > 4096):
        raise CutQtError("INVALID_NATIVE_SNAPSHOT", "Complete bounded native PatternList required")
    ids = [p.get("ID") for p in papers]
    if (any(type(x) is not str or not x for x in ids) or len(set(ids)) != len(ids)
            or set(ids) != {r["pattern_uuid"] for r in rows} or rows != _native.pattern_rows()):
        raise CutQtError("SNAPSHOT_UUID_MISMATCH", "Native UUIDs must biject with the same fresh runtime/PI rows")
    return _normalize_capture(native, rows)


def _normalize_capture(native, rows):
    # The SDK UUID remains literal. Native JSON's other IDs are declaration
    # aliases and may change on re-export even with unchanged geometry.
    ordered = {**native, "PatternList": sorted(native["PatternList"], key=lambda paper: paper["ID"])}
    normalized, aliases = curves._identity_graph(ordered)
    return {**_capture_geometry(normalized, rows), "aliases": aliases}


def _capture_geometry(native, rows):
    papers = {p["ID"]: p for p in native["PatternList"]}
    fingerprints = {key: "sha256:" + digest(_native._geometry(paper, include_ids=False))
                    for key, paper in papers.items()}
    board = {key: {"outline": paper["ShapeInfo"], "closed": paper.get("IsClosed"),
                   "internal": paper.get("InternalLineList", [])} for key, paper in papers.items()}
    return {"native": native, "rows": rows, "papers": papers, "fingerprints": fingerprints,
            "scene_fingerprint": digest(fingerprints),
            "board_fingerprint": digest({"unit": native.get("Unit"), "papers": board})}


def _text(value, name):
    if type(value) is not str or not value or value != value.strip() or len(value) > 512:
        raise CutQtError("INVALID_PARAMETERS", name + " must be a bounded nonempty current reference")
    return value


def _token(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CutQtError("INVALID_PARAMETERS", "A fresh scene token is required")
    return value


def _fingerprint(value):
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise CutQtError("INVALID_PARAMETERS", "Use the topology fingerprint from cut_qt.inspect")
    return value


def _params(params, required, optional=()):
    if type(params) is not dict or not set(required) <= params.keys() or params.keys() - set(required) - set(optional):
        raise CutQtError("INVALID_PARAMETERS", "Unexpected or missing closed Cut parameter")


def _target(capture, owner, shape_id, line_id):
    row, paper, fingerprint = _native._owner(capture, owner)
    aliases = capture.get("aliases", {})
    shape_id, line_id = aliases.get(shape_id, shape_id), aliases.get(line_id, line_id)
    shapes = paper.get("InternalLineList")
    if type(shapes) is not list or any(type(q) is not dict for q in shapes):
        raise CutQtError("INVALID_NATIVE_SNAPSHOT", "Complete native internal-shape list required")
    targets = [q for q in shapes if q.get("ID") == shape_id]
    if len(targets) != 1:
        raise CutQtError("STALE_SHAPE_REFERENCE", "Shape UUID must resolve uniquely under this current owner")
    shape = targets[0]
    if type(shape.get("ShapeType")) is not int or shape["ShapeType"] != 1 or shape.get("IsClosed") is not False:
        raise CutQtError("CUT_SHAPE_UNSUPPORTED", "Only one genuine open ShapeType1 whole line is admitted", "UNSUPPORTED")
    if (len([q for q in shapes if q.get("ShapeType") == 1]) != 1
            or any(type(q.get("ShapeType")) is not int or q["ShapeType"] not in (1, 2)
                   or any(q.get(k) for k in ("IsHole", "IsBinding", "bIsHole", "bIsBinding")) for q in shapes)):
        raise CutQtError("OTHER_INTERNALS_UNVERIFIED", "Additional internal shapes/holes/bindings need an independent preservation contract", "UNSUPPORTED")
    curves = _native._curves(shape)
    if len(curves) != 1 or curves[0]["line_uuid"] != line_id:
        raise CutQtError("STALE_LINE_REFERENCE", "Whole shape must contain exactly the requested single current line")
    curve = curves[0]
    binding = _native._runtime_binding(row, curve["points_mm"])
    boundary = _native._boundary_chain(_native._curves(paper["ShapeInfo"]))
    _native._source_chains(boundary, curve["points_mm"])
    return {"pattern_uuid": owner, "shape_uuid": shape_id, "line_uuid": line_id,
            "topology_fingerprint": fingerprint, "current_pattern_index": row["index"],
            "runtime_binding": binding, "curve": curve,
            "scene_geometry_fingerprint": capture["scene_fingerprint"],
            "selection_board_fingerprint": capture["board_fingerprint"]}


def _unchanged(before, after, target):
    if before["scene_fingerprint"] != after["scene_fingerprint"]:
        raise CutQtError("SCENE_CHANGED_BEFORE_CUT", "Native nonbaseline geometry changed during selection")
    current = _target(after, target["pattern_uuid"], target["shape_uuid"], target["line_uuid"])
    if current != target:
        raise CutQtError("STALE_TOPOLOGY", "Current native/PI target binding changed")


def _inspection():
    response = _scene.inspect_2d({})
    if type(response) is not dict or response.get("status") != "SUCCEEDED" or response.get("effects_started") is not False or response.get("requires_recovery") is not False:
        raise CutQtError("CURRENT_SCENE_SELECTION_UNAVAILABLE", "A complete fresh scene_adapter inspection is required", "UNSUPPORTED")
    value = response.get("result")
    if type(value) is not dict or value.get("verified") is not True:
        raise CutQtError("INVALID_SELECTION_READBACK", "Complete current native adapter receipt required")
    selected = value.get("selection_after", {})
    tools = value.get("tools_after", {})
    if (type(selected.get("selected_items_count")) is not int or selected["selected_items_count"] != 1
            or tools.get("verified") is not True or type(tools.get("current_mode")) is not int or tools["current_mode"] != 1):
        raise CutQtError("SINGLE_LINE_SELECTION_NOT_ESTABLISHED", "Edit Pattern plus one current public Qt selection required; owner selection alone is insufficient", "UNSUPPORTED")
    after = value["after"]
    return {"view_ref": _text(after.get("view_ref"), "view_ref"),
            "view_fingerprint": _token(after.get("view_fingerprint")),
            "selection_fingerprint": _token(selected.get("selection_fingerprint")),
            "scene_token": _token(value.get("scene_token"))}


def _verify(provider, plan, receipt, inspection):
    # Only the installed core verifier can issue this semantic identity record.
    # No operation accepts it, or a verified flag, from the MCP caller.
    observed = provider["verify"](copy.deepcopy(plan), copy.deepcopy(receipt), copy.deepcopy(inspection))
    expected = {k: plan[k] for k in ("pattern_uuid", "shape_uuid", "line_uuid", "topology_fingerprint")}
    expected.update(inspection)
    expected["selection_nonce"] = plan["selection_nonce"]
    if type(observed) is not dict or set(observed) != set(expected) or observed != expected:
        raise CutQtError("SEMANTIC_LINE_SELECTION_UNPROVEN", "Installed verifier did not re-establish this exact current native line/view/selection binding", "UNSUPPORTED")
    return observed


def _prune():
    tickets = _state()["tickets"]
    for key in list(tickets):
        if time.monotonic() > tickets[key]["expires"]:
            tickets.pop(key)


def capabilities(params):
    _params(params, ())
    state = _state()
    surface_configured = bool(state['selector'] and state['selector'].get('surface_readback'))
    return {"status": "SUCCEEDED", "verified": True, "effects_started": False,
            "selector_installed": state["selector"] is not None,
            "selector_name": state["selector"]["name"] if state["selector"] else None,
            "selector_profile": state["selector"].get("kind", "read_stable") if state["selector"] else None,
            "blocked_until_recovery": state["blocked"],
            "native_reader": state["reader"]["name"] if state["reader"] else "existing_public_native_capture",
            "generic_semantic_selection_live_verified": False,
            "exported_surface_readback_configured": surface_configured,
            "surface_preservation_supported": surface_configured, "cut_and_sew_supported": False,
            "live_validation": "anchored_straight_with_existing_seam_and_single_cubic_verified",
            "sdk_cut_calls": 0, "Qt_Cut_trigger_calls": 0}


def inspect(params):
    _params(params, {"pattern_uuid"})
    identity = _text(params["pattern_uuid"], "pattern_uuid")
    before = scene()
    capture = _capture_current(_native._api())
    _, paper, fingerprint = _native._owner(capture, identity)
    export_ids = {stable: raw for raw, stable in capture["aliases"].items()}
    refs = []
    for shape in paper.get("InternalLineList", []):
        if shape.get("ShapeType") == 2:
            continue
        ref = {"shape_uuid": shape.get("ID"), "line_uuids": [q.get("ID") for q in shape.get("LineList", [])],
               "shape_type": shape.get("ShapeType"), "closed": shape.get("IsClosed"), "target_geometry_ready": False}
        ref["export_shape_id"] = export_ids.get(ref["shape_uuid"], ref["shape_uuid"])
        ref["export_line_ids"] = [export_ids.get(key, key) for key in ref["line_uuids"]]
        try:
            if len(ref["line_uuids"]) != 1:
                raise CutQtError("MULTILINE_CUT_UNSUPPORTED", "One complete internal line required", "UNSUPPORTED")
            plan = _target(capture, identity, shape["ID"], ref["line_uuids"][0])
            ref.update(target_geometry_ready=True, runtime_binding=plan["runtime_binding"])
        except (_native._CutError, CutQtError) as exc:
            ref["error"] = {"code": exc.code, "message": str(exc)}
        refs.append(ref)
    after = scene()
    if before["scene_token"] != after["scene_token"]:
        raise CutQtError("SCENE_CHANGED_DURING_READ", "Read current scene again")
    return {"status": "SUCCEEDED", "verified": True, "effects_started": False,
            "pattern_uuid": identity, "topology_fingerprint": fingerprint, "shape_refs": refs,
            "scene_token": after["scene_token"], "semantic_selection_ready": False,
            "sdk_cut_calls": 0, "Qt_Cut_trigger_calls": 0, "surface_preservation_supported": False}


def prepare(params):
    _params(params, {"pattern_uuid", "shape_uuid", "line_uuid", "topology_fingerprint", "scene_token"})
    state = _state()
    provider = state["selector"]
    if provider is None:
        raise CutQtError("NO_SEMANTIC_LINE_SELECTOR", "Install a core-owned current native-UUID to exact selected-line provider; a generic picker/owner hit is insufficient", "UNSUPPORTED")
    token = _token(params["scene_token"])
    if scene()["scene_token"] != token:
        raise CutQtError("STALE_SCENE", "Read fresh owner/shape/line refs before preparing selection")
    api = _native._api()
    before = _capture_current(api)
    target = _target(before, *[_text(params[k], k) for k in ("pattern_uuid", "shape_uuid", "line_uuid")])
    if target["topology_fingerprint"] != _fingerprint(params["topology_fingerprint"]):
        raise CutQtError("STALE_TOPOLOGY", "Current geometry/PI does not match the requested target")
    _prune()
    if len(state["tickets"]) >= MAX_TICKETS:
        raise CutQtError("TOO_MANY_PENDING_SELECTIONS", "Pending selection tickets are bounded; expire or consume them")
    plan = copy.deepcopy(target)
    plan.update(scene_token=token, selection_nonce=uuid.uuid4().hex,
                selector_epoch=provider["epoch"], scope="one_current_whole_native_internal_line")
    if provider.get('kind')=='atomic':
        return _prepare_atomic(provider,plan,target,before,api)
    receipt = None
    try:
        state["effect_boundary_entered"] = True
        receipt = provider["select"](copy.deepcopy(plan))
        if len(json.dumps(receipt, allow_nan=False, ensure_ascii=False).encode("utf-8")) > 262144:
            raise CutQtError("SELECTION_RECEIPT_TOO_LARGE", "Trusted selector should return bounded proof/path-SHA references")
        after = _capture_current(api)
        _unchanged(before, after, target)
        current = _inspection()
        semantic = _verify(provider, plan, receipt, current)
        final = _capture_current(api)
        _unchanged(before, final, target)
        if scene()["scene_token"] != current["scene_token"]:
            raise CutQtError("SCENE_CHANGED_DURING_SELECTION", "Selection no longer belongs to this current scene")
    except Exception as exc:
        # The callback boundary may already have changed UI/selection. No Cut.
        return _failure(getattr(exc, "code", "SELECTOR_FAILED"), str(exc), status="UNKNOWN", effects=True,
                        extra={"selection_receipt": receipt, "Qt_Cut_trigger_calls": 0})
    ref = PREFIX + uuid.uuid4().hex
    state["tickets"][ref] = {"plan": plan, "target": target, "capture": {"scene_fingerprint": before["scene_fingerprint"]}, "receipt": copy.deepcopy(receipt),
                              "inspection": current, "semantic": semantic, "expires": time.monotonic() + TTL_SECONDS}
    return {"status": "SUCCEEDED", "verified": True, "fulfilled": False, "effects_started": True,
            "selection_ref": ref, "scene_token": current["scene_token"], "expires_in_seconds": TTL_SECONDS,
            "selected_native_line": {k: target[k] for k in ("pattern_uuid", "shape_uuid", "line_uuid", "topology_fingerprint")},
            "selector": {"name": provider["name"], "evidence": provider["evidence"]},
            "selection_receipt": receipt, "Qt_Cut_trigger_calls": 0, "sdk_cut_calls": 0,
            "verification_scope": "Installed semantic selector plus independent current native/PI and adapter view/selection checks; no Cut yet"}


def execute(params):
    _params(params, {"selection_ref", "scene_token"}, {"require_surface_preservation"})
    surface = params.get("require_surface_preservation", True)
    if type(surface) is not bool:
        raise CutQtError("INVALID_BOOLEAN", "require_surface_preservation must be an actual boolean")
    provider = _state()['selector']
    if surface and not (provider and provider.get('surface_readback') is True):
        raise CutQtError("SURFACE_PRESERVATION_UNSUPPORTED", "The installed provider has no exported surface readback", "UNSUPPORTED")
    ref, token = _text(params["selection_ref"], "selection_ref"), _token(params["scene_token"])
    _prune()
    ticket = _state()["tickets"].pop(ref, None)
    if ticket is None:
        raise CutQtError("STALE_SELECTION_REFERENCE", "Use a current core-generated single-use prepare reference")
    if provider is None or provider["epoch"] != ticket["plan"]["selector_epoch"]:
        raise CutQtError("STALE_SELECTOR", "Trusted selector installation changed")
    if token != ticket["inspection"]["scene_token"] or scene()["scene_token"] != token:
        raise CutQtError("STALE_SCENE", "The prepared selection belongs to a different current scene")
    api = _native._api()
    before = _capture_current(api)
    target = ticket["target"]
    _unchanged(ticket["capture"], before, target)
    if provider.get('kind')=='atomic':
        return _execute_atomic(provider,ticket,before,api,token,surface)
    current = _inspection()
    if current != ticket["inspection"]:
        raise CutQtError("STALE_LINE_SELECTION", "View, mode or selected Qt item changed after prepare")
    _verify(provider, ticket["plan"], ticket["receipt"], current)
    _unchanged(before, _capture_current(api), target)
    if scene()["scene_token"] != token:
        raise CutQtError("STALE_SCENE", "Scene changed immediately before selected Cut")
    action = None
    action_exception = None
    _state()["effect_boundary_entered"] = True
    _state()["cut_boundary_entered"] = True
    try:
        action = _scene.cut_selected(current)
    except Exception as exc:
        action_exception = {"type": type(exc).__name__, "message": str(exc)}
    action_result = action.get("result") if type(action) is dict else None
    action_result = action_result if type(action_result) is dict else {}
    calls = action_result.get("QAction_trigger_calls")
    known_calls = calls if type(calls) is int and calls in (0, 1) else None
    safe_zero = (type(action) is dict and action.get("status") in ("REJECTED", "UNSUPPORTED")
                 and action.get("effects_started") is False and action.get("requires_recovery") is False
                 and action_result.get("verified") is False and action_exception is None
                 and ("QAction_trigger_calls" not in action_result or known_calls == 0))
    if safe_zero:
        known_calls = 0
    action_consistent = (type(action) is dict and type(action.get("effects_started")) is bool
                         and type(action.get("requires_recovery")) is bool and known_calls is not None
                         and action["effects_started"] is bool(known_calls))
    # Always read after the action boundary, including False/rejection/exception.
    result = {"status": "UNKNOWN", "verified": False, "fulfilled": False, "effects_started": True,
              "requires_recovery": True, "do_not_retry": True, "sdk_cut_calls": 0,
              "Qt_Cut_trigger_calls": known_calls,
              "adapter_response": action, "adapter_exception": action_exception,
              "target": target, "surface_preservation": {"required": False, "3d": False, "rest": False, "uv": False},
              "cut_and_sew_verified": False, "live_validation": "pending"}
    try:
        after = _capture_current(api)
        proof = _native._partition(before, after, target["pattern_uuid"], target["curve"])
        result.update(readback=proof, mutation_observed=before["scene_fingerprint"] != after["scene_fingerprint"],
                      new_pattern_refs=_result_pattern_refs(after, proof),
                      scene_token_after=scene()["scene_token"])
    except Exception as exc:
        result["error"] = {"code": "POST_CUT_READBACK_FAILED", "type": type(exc).__name__, "message": str(exc)}
        return result
    success = (action_consistent and type(action) is dict and action.get("status") == "SUCCEEDED" and action.get("effects_started") is True
               and action.get("requires_recovery") is False and action_result.get("verified") is True
               and action_result.get("selected_cut_action_returned") is True and type(calls) is int and calls == 1
               and proof["native_partition_observed"] is True and action_exception is None)
    if success:
        result.update(status="SUCCEEDED", verified=True, fulfilled=True, requires_recovery=False,
                      outcome="native_2d_partition_verified")
    elif safe_zero and not result["mutation_observed"]:
        result.update(status="REJECTED", effects_started=False, requires_recovery=False,
                      outcome="zero_cut_preflight_rejected")
    else:
        result.update(status="UNKNOWN" if not action_consistent or action_exception or type(action) is not dict or action.get("requires_recovery") else "PARTIAL",
                      outcome="cut_or_partition_unverified")
    return result


def _failure(code, message, *, status="REJECTED", effects=False, cut_calls=0, extra=None):
    return {"status": status, "verified": False, "fulfilled": False, "effects_started": effects,
            "requires_recovery": effects, "do_not_retry": effects, "sdk_cut_calls": 0,
            "Qt_Cut_trigger_calls": cut_calls,
            "error": {"code": code, "message": message}, **(extra or {})}


def _entry(fn):
    def invoke(params):
        state = _state()
        if state["busy"]:
            return _failure("CUT_PROVIDER_BUSY", "An operation is already active")
        if state["blocked"] and fn in (prepare, execute):
            return _failure("CUT_PROVIDER_RECOVERY_REQUIRED", "Previous uncertain selection/Cut requires core recovery; no automatic retry")
        state["busy"] = True
        state["effect_boundary_entered"] = False
        state["cut_boundary_entered"] = False
        try:
            result = fn(params)
            if result.get("requires_recovery") is True:
                state["blocked"] = True
                state["tickets"].clear()
            return result
        except Exception as exc:
            effects = state["effect_boundary_entered"]
            if effects:
                state["blocked"] = True
                state["tickets"].clear()
            raw_status = getattr(exc, "status", "UNSUPPORTED")
            status = raw_status.upper() if type(raw_status) is str and raw_status.upper() in ("UNSUPPORTED", "REJECTED") else "UNSUPPORTED"
            return _failure(getattr(exc, "code", "CUT_QT_UNAVAILABLE"), str(exc),
                            status="UNKNOWN" if effects else status, effects=effects,
                            cut_calls=None if state["cut_boundary_entered"] else 0)
        finally:
            state["busy"] = False
    return invoke


OPERATIONS = {
    "cut_qt.capabilities": _entry(capabilities),
    "cut_qt.inspect": _entry(inspect),
    "cut_qt.prepare": _entry(prepare),
    "cut_qt.execute": _entry(execute),
}
