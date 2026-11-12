"""Core-only Scene P/R→fixed Cut C ABI; no public pixel route.

Installation pins bytes but never loads a DLL. A fresh semantic plan must be
established by cut_qt's trusted integration before select_and_cut is invoked.
Dispatch verification here does not prove line identity or native partition.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
from pathlib import Path
import re
import sys

from . import canvas as _base

STATE_KEY = '_md_mcp_plugin_cut_qt_atomic_state'
SCHEMA = 'md-qt-cut-qt-atomic/1'
CAPACITY = 1048576
ZERO_COUNTERS = ('OS_input_calls','OS_cursor_setter_calls','activation_calls','focus_setter_calls',
                 'window_restore_calls','global_capture_setter_calls','global_modifier_setter_calls',
                 'event_pump_calls','model_memory_access_calls','project_open_import_save_calls','simulation_calls')


class AtomicError(ValueError):
    pass


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {'configuration':None,'library':None,'call':None,'process':None,'blocked':False,'busy':False}
        setattr(sys, STATE_KEY, state)
    return state


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hex(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise AtomicError('Exact SHA256/fingerprint required')
    return value


def _point(value):
    if type(value) is not list or len(value)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10000000 for v in value):
        raise AtomicError('Bounded logical viewport point required')
    return list(value)


def install_native_bridge(path, sha256):
    """Trusted bootstrap only. No MCP install operation, load or hot swap."""
    _hex(sha256); path=Path(path)
    if not path.is_absolute() or path.name!='md_qt_cut_qt_atomic_bridge.dll' or path.is_symlink() or not path.is_file() or _sha(path)!=sha256:
        raise AtomicError('One existing absolute byte-pinned atomic Cut DLL required')
    config={'path':str(path.resolve()),'sha256':sha256}; state=_state()
    if state['blocked'] or state['library'] is not None and config!=state['configuration']:
        raise AtomicError('No hot swap or clearing an uncertain dispatch')
    state['configuration']=config
    return {'configured':True,'loaded':state['library'] is not None,'sha256':sha256,'live_validation':'pending'}


def _load(environment):
    state=_state(); config=state['configuration']
    if config is None or _sha(config['path'])!=config['sha256']:
        raise AtomicError('Atomic Cut bridge not configured or bytes changed')
    if state['library'] is None:
        library=ctypes.CDLL(config['path'],winmode=0x900)
        version=library.mdqt_cut_qt_atomic_abi_version
        version.argtypes,version.restype=[],ctypes.c_uint32
        if version()!=1: raise AtomicError('Atomic Cut ABI1 required')
        call=library.mdqt_cut_qt_atomic_json
        call.argtypes=[ctypes.c_uint32,ctypes.c_uint64,ctypes.c_char_p,ctypes.c_uint32,
                       ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32)]
        call.restype=ctypes.c_int
        state.update(library=library,call=call,process=dict(environment['process']))
    if state['process']!=environment['process']: raise AtomicError('Process lifetime changed')
    return state['call']


def _integer(value, allowed):
    return type(value) is int and value in allowed


def validate_report(raw, code, request):
    """Pure receipt replay. Does not trust accepted/grabber as line hit."""
    if (type(raw) is not dict or raw.get('schema')!=SCHEMA or not _integer(raw.get('return_code'),(code,))
            or raw.get('operation')!=request['operation']): raise AtomicError('Invalid atomic receipt schema/code/operation')
    for k in ('semantic_line_selection_verified','native_partition_verified','native_mm_mapping_verified','geometry_preserved_verified',
              'hard_callback_timeout','native_faults_contained','automatic_retry_allowed'):
        if raw.get(k) is not False: raise AtomicError('Unproved semantic/safety claim: '+k)
    if raw.get('single_native_batch') is not True: raise AtomicError('Single native batch required')
    for k in ('SDK_calls','SDK_reads_between_click_and_cut','Python_callbacks_in_batch','event_pump_calls'):
        if not _integer(raw.get(k),(0,)): raise AtomicError('Nonzero or untyped batch counter: '+k)
    counts=raw.get('controller_calls')
    if type(counts) is not dict or set(counts)!=set(ZERO_COUNTERS)|{'QAction_trigger_calls'}:
        raise AtomicError('Closed controller counter set required')
    if any(not _integer(counts[k],(0,)) for k in ZERO_COUNTERS): raise AtomicError('Forbidden side effect counter')
    n,cut=raw.get('event_calls'),raw.get('QAction_trigger_calls')
    if not _integer(n,range(4)) or not _integer(cut,(0,1)) or counts['QAction_trigger_calls']!=cut:
        raise AtomicError('Bounded typed actual counters required')
    events=raw.get('events')
    if type(events) is not list or len(events)!=n or any(type(e) is not dict for e in events): raise AtomicError('Actual event rows/count mismatch')
    effects=bool(n or cut)
    if raw.get('effects_started') is not effects or type(raw.get('requires_recovery')) is not bool: raise AtomicError('Effects/recovery flags mismatch')
    if raw.get('status')=='REJECTED':
        if effects or code!=-5 or raw.get('verified') is not False or raw['requires_recovery'] is not False:
            raise AtomicError('Not a proven zero-event rejection')
        return False
    if raw.get('status')=='PARTIAL':
        if not effects or code!=-10 or raw.get('verified') is not False or raw['requires_recovery'] is not True:
            raise AtomicError('Partial dispatch must retain recovery')
        return False
    if code!=0 or raw.get('status')!='SUCCEEDED' or raw.get('verified') is not True or raw['requires_recovery'] is not False:
        raise AtomicError('Unknown native completion')
    before,after=raw.get('before'),raw.get('after')
    if type(before) is not dict or type(after) is not dict or raw.get('mode_before')!=1 or type(raw.get('mode_before')) is not int:
        raise AtomicError('Mode1/current view receipt required')
    for k in ('physical_scope_verified','view_scope_verified','local_technical_cleanup_verified','cursor_position_observational'):
        if raw.get(k) is not True: raise AtomicError('Scope/cleanup unproved: '+k)
    for k in ('physical_before','physical_after'):
        p=raw.get(k)
        if type(p) is not dict or p.get('background_safe') is not True or p.get('cursor_query_known') is not True or p.get('cursor_position_is_observational') is not True:
            raise AtomicError('Background physical observation missing')
    if request['operation']=='inspect':
        if effects or raw.get('stages')!=[] or raw.get('Cut_action_returned') is not False or before!=after:
            raise AtomicError('Read-only inspection changed scope')
        return True
    if (before.get('native_view_ref')!=request['native_view_ref'] or after.get('native_view_ref')!=request['native_view_ref']
            or before.get('view_fingerprint')!=request['view_fingerprint']): raise AtomicError('Stale native view receipt')
    if n!=2 or cut!=1 or raw.get('stages')!=['line_press','line_release','actionCutPattern2D']:
        raise AtomicError('Fixed P/R→Cut sequence mismatch')
    for k in ('Cut_action_returned','dispatch_completed','line_pair_cleanup_verified','press_sent','release_sent',
              'release_returned','paired_release_verified','all_events_nonspontaneous'):
        if raw.get(k) is not True: raise AtomicError('Incomplete batch/release: '+k)
    if raw.get('press_outstanding') is not False or not _integer(raw.get('scene_ungrab_calls'),(0,1)) or not _integer(raw.get('mode_after'),(0,1)):
        raise AtomicError('Unreleased or unknown post-tool scope')
    for row,stage,buttons in zip(events,('line_press','line_release'),(1,0)):
        if (row.get('stage')!=stage or row.get('viewport_logical')!=request['point'] or row.get('sent') is not True or row.get('returned') is not True
                or not _integer(row.get('button'),(1,)) or not _integer(row.get('buttons'),(buttons,)) or not _integer(row.get('modifiers'),(0,))
                or type(row.get('send_return')) is not bool or type(row.get('accepted')) is not bool):
            raise AtomicError('Fixed event coordinate/button/return mismatch')
    return True


def closed_cut_before_observation_timeout(raw):
    """Validate the delivered pair and Cut return from a timed-out post-check."""
    if type(raw) is not dict or raw.get('status') != 'PARTIAL' or raw.get('return_code') != -10:
        return False
    try:
        validate_report(raw, -10, {'operation': 'select_and_cut'})
        if ((raw.get('error') or {}).get('code') != 'COOPERATIVE_BUDGET_EXCEEDED'
                or raw['event_calls'] != 2 or raw['QAction_trigger_calls'] != 1
                or raw.get('stages') != ['line_press', 'line_release', 'actionCutPattern2D']
                or not _integer(raw.get('mode_before'), (1,))
                or not _integer(raw.get('scene_ungrab_calls'), (0, 1))
                or raw.get('press_outstanding') is not False):
            return False
        closed = ('press_sent', 'release_sent', 'release_returned', 'paired_release_verified',
                  'line_pair_cleanup_verified', 'Cut_action_returned', 'all_events_nonspontaneous')
        if any(raw.get(key) is not True for key in closed):
            return False
        point = _point(raw['events'][0].get('viewport_logical'))
        return all(
            row.get('stage') == stage and _point(row.get('viewport_logical')) == point
            and row.get('sent') is True and row.get('returned') is True
            and _integer(row.get('button'), (1,)) and _integer(row.get('buttons'), (buttons,))
            and _integer(row.get('modifiers'), (0,))
            for row, stage, buttons in zip(raw['events'], ('line_press', 'line_release'), (1, 0))
        )
    except (AtomicError, AttributeError, KeyError, TypeError):
        return False


def _retire_recovered_bridge():
    """Internal commit after verified scene reload and fresh closed input."""
    state = _state()
    if state['busy'] or state['blocked'] is not True or state['library'] is None:
        raise AtomicError('Expected the inactive, held bridge after verified Cut recovery')
    state.setdefault('retired_libraries', []).append(state['library'])
    state.update(configuration=None, library=None, call=None, process=None, blocked=False)


def _invoke(request):
    state=_state(); write=request['operation']=='select_and_cut'; invoked=False
    if state['busy'] or state['blocked'] or any(getattr(sys,k,{}).get('blocked') is True for k in (
            '_md_mcp_plugin_scene_adapter_state','_md_mcp_plugin_scene_adapter_r3_state','_md_mcp_plugin_scene_adapter_r4_state',
            '_md_mcp_plugin_scene_adapter_r5_state','_md_mcp_plugin_scene_adapter_r6_state')):
        return {'status':'REJECTED','effects_started':False,'requires_recovery':False,'result':{'verified':False},'error':{'code':'CORE_OR_BRIDGE_RECOVERY_REQUIRED'}}
    state['busy']=True
    try:
        environment=_base._environment(); call=_load(environment)
        data=json.dumps(request,allow_nan=False,separators=(',',':')).encode()
        out=ctypes.create_string_buffer(CAPACITY); written,needed=ctypes.c_uint32(),ctypes.c_uint32()
        invoked=True
        code=call(environment['process']['pid'],environment['process']['creation_filetime'],data,len(data),out,len(out),ctypes.byref(written),ctypes.byref(needed))
        if written.value==0 and code in (-1,-2,-3,-4):
            return {'status':'REJECTED','effects_started':False,'requires_recovery':False,'result':{'verified':False},'error':{'code':'ABI_PREFLIGHT_ZERO_DISPATCH'}}
        if not 0<written.value<len(out) or needed.value!=written.value+1: raise AtomicError('Missing receipt; never resize/replay')
        raw=_base._json(out.raw[:written.value].decode('utf-8'))
        validate_report(raw,code,request)
        if raw['requires_recovery']: state['blocked']=True
        return {'status':raw['status'],'effects_started':raw['effects_started'],'requires_recovery':raw['requires_recovery'],'result':raw}
    except Exception as exc:
        unknown=write and invoked
        if unknown: state['blocked']=True
        return {'status':'UNKNOWN' if unknown else 'UNSUPPORTED','effects_started':unknown,'requires_recovery':unknown,
                'result':{'verified':False},'error':{'code':'ATOMIC_CUT_UNAVAILABLE','message':str(exc)}}
    finally: state['busy']=False


def inspect_current(*, budget_ms=500):
    if type(budget_ms) is not int or not 50<=budget_ms<=1500: raise AtomicError('Bounded integer budget required')
    return _invoke({'operation':'inspect','budget_ms':budget_ms})


def select_and_cut(binding, *, budget_ms=500):
    """Private core call after fresh semantic qualification; no MCP operation."""
    if type(binding) is not dict or set(binding)!={'native_view_ref','view_fingerprint','point'}:
        raise AtomicError('Closed internally generated batch binding required')
    if type(binding['native_view_ref']) is not str or re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',binding['native_view_ref']) is None:
        raise AtomicError('Fresh atomic bridge-owned view reference required')
    if type(budget_ms) is not int or not 50<=budget_ms<=1500: raise AtomicError('Bounded integer budget required')
    return _invoke({'operation':'select_and_cut','budget_ms':budget_ms,'native_view_ref':binding['native_view_ref'],
                    'view_fingerprint':_hex(binding['view_fingerprint']),'point':_point(binding['point'])})
