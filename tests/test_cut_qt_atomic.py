"""Atomic Cut contract tests: synthetic callback/data, no DLL/MD/network."""
import copy
import ctypes
from hashlib import sha256
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from md_mcp_plugin.embedded import cut_qt as cut
from md_mcp_plugin.embedded import cut_qt_atomic as atom
from md_mcp_plugin.embedded import dispatch, project
from md_mcp_plugin.errors import PluginError

spec=importlib.util.spec_from_file_location('_cutqt_fixture_atomic',ROOT/'tests/test_cut_qt.py')
fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
REF=fixture.NATIVE_REF; FP=fixture.VIEW_FP
BINDING={'native_view_ref':REF,'view_fingerprint':FP,'point':[100,120]}


def raw(operation='select_and_cut'):
    before={'native_view_ref':REF,'view_fingerprint':FP,'mapping_fingerprint':FP,'mapping':{'viewport_size':[800,600]}}
    p={'background_safe':True,'cursor_query_known':True,'cursor_position_is_observational':True,'cursor_position':[1,2]}
    value={'schema':atom.SCHEMA,'return_code':0,'operation':operation,'status':'SUCCEEDED','verified':True,
           'effects_started':operation!='inspect','requires_recovery':False,'single_native_batch':True,
           'before':before,'after':copy.deepcopy(before),'physical_before':p,'physical_after':copy.deepcopy(p),
           'mode_before':1,'mode_after':1,'physical_scope_verified':True,'view_scope_verified':True,
           'local_technical_cleanup_verified':True,'cursor_position_observational':True,
           'SDK_calls':0,'SDK_reads_between_click_and_cut':0,'Python_callbacks_in_batch':0,'event_pump_calls':0,
           'event_calls':0 if operation=='inspect' else 2,'events':[],'stages':[],
           'QAction_trigger_calls':0 if operation=='inspect' else 1,'Cut_action_returned':operation!='inspect',
           'press_outstanding':False,'scene_ungrab_calls':0}
    value.update({k:False for k in ('semantic_line_selection_verified','native_partition_verified','native_mm_mapping_verified',
                                  'geometry_preserved_verified','hard_callback_timeout','native_faults_contained','automatic_retry_allowed')})
    value['controller_calls']={k:0 for k in atom.ZERO_COUNTERS}
    value['controller_calls']['QAction_trigger_calls']=value['QAction_trigger_calls']
    if operation!='inspect':
        for stage,buttons in [('line_press',1),('line_release',0)]:
            value['events'].append({'stage':stage,'viewport_logical':BINDING['point'],'button':1,'buttons':buttons,
                                    'modifiers':0,'sent':True,'returned':True,'send_return':True,'accepted':False})
        value['stages']=['line_press','line_release','actionCutPattern2D']
        value.update({k:True for k in ('dispatch_completed','line_pair_cleanup_verified','press_sent','release_sent',
                                      'release_returned','paired_release_verified','all_events_nonspontaneous')})
    return value


class RawContracts(unittest.TestCase):
    def test_fixed_batch_proves_dispatch_only(self):
        request={'operation':'select_and_cut',**BINDING}
        self.assertTrue(atom.validate_report(raw(),0,request))
        for key,value in [('SDK_reads_between_click_and_cut',1),('semantic_line_selection_verified',True),
                          ('press_outstanding',True),('paired_release_verified',False),('QAction_trigger_calls',True)]:
            r=raw(); r[key]=value
            with self.assertRaises(atom.AtomicError): atom.validate_report(r,0,request)

    def test_stale_mode_point_and_extra_event_reject(self):
        request={'operation':'select_and_cut',**BINDING}
        for mutate in (lambda r:r.update(mode_before=2),lambda r:r['events'][0].update(viewport_logical=[101,120]),
                       lambda r:r['before'].update(native_view_ref='other'),lambda r:r['events'].append(r['events'][0])):
            r=raw(); mutate(r)
            with self.assertRaises(atom.AtomicError): atom.validate_report(r,0,request)

    def test_partial_requires_recovery_and_inspection_is_zero_input(self):
        r=raw(); r.update(status='PARTIAL',verified=False,return_code=-10,requires_recovery=True)
        self.assertFalse(atom.validate_report(r,-10,{'operation':'select_and_cut',**BINDING}))
        r['requires_recovery']=False
        with self.assertRaises(atom.AtomicError): atom.validate_report(r,-10,{'operation':'select_and_cut',**BINDING})
        self.assertTrue(atom.validate_report(raw('inspect'),0,{'operation':'inspect'}))

    def test_invoked_missing_receipt_never_replays_and_blocks(self):
        previous=getattr(sys,atom.STATE_KEY,None)
        setattr(sys,atom.STATE_KEY,{'configuration':{},'library':None,'call':None,'process':None,'blocked':False,'busy':False})
        self.addCleanup(lambda: delattr(sys,atom.STATE_KEY) if previous is None else setattr(sys,atom.STATE_KEY,previous))
        calls=[]
        def fn(*args): calls.append(1); return -8
        with patch.object(atom._base,'_environment',return_value={'process':{'pid':1,'creation_filetime':2}}),patch.object(atom,'_load',return_value=fn):
            r=atom.select_and_cut(BINDING)
        self.assertEqual(len(calls),1); self.assertEqual(r['status'],'UNKNOWN'); self.assertTrue(atom._state()['blocked'])


class AtomicProviderContracts(unittest.TestCase):
    def setUp(self):
        self.base=fixture.CutQtContracts('test_no_default_selector_and_no_public_certificate_installation')
        self.base.setUp(); self.addCleanup(self.base.doCleanups)
        p=patch.object(atom,'inspect_current',return_value={'status':'SUCCEEDED','effects_started':False,'requires_recovery':False,'result':raw('inspect')})
        self.inspect=p.start(); self.addCleanup(p.stop)
        p=patch.object(atom,'select_and_cut',side_effect=self.action)
        self.batch=p.start(); self.addCleanup(p.stop)
        self.trace=[]

    def candidate(self,plan,native):
        self.trace.append('candidate')
        return {'binding':copy.deepcopy(BINDING),'evidence':{'fixture':'synthetic known line/frame, not a live hit'}}

    def verify(self,plan,receipt,inspection,native):
        self.trace.append('semantic_verify')
        return {**{k:plan[k] for k in ('pattern_uuid','shape_uuid','line_uuid','topology_fingerprint')},
                **inspection,'selection_nonce':plan['selection_nonce'],'candidate_sha256':cut.digest(receipt)}

    def action(self,binding):
        # This explicitly stands in for ONE native call. No SDK callbacks in it.
        self.trace.extend(['batch_begin','press','release','Cut','batch_end'])
        self.base.sdk.native=copy.deepcopy(self.base.sdk.after); self.base.changed=True
        return {'status':'SUCCEEDED','effects_started':True,'requires_recovery':False,'result':raw()}

    def install(self,verify=None):
        cut.install_atomic_line_selector(self.candidate,verify or self.verify,name='synthetic atomic semantic plan',evidence='Offline callbacks only')

    def test_prepare_is_read_only_then_one_atomic_batch_and_real_partition(self):
        self.install(); ref=self.base.prepare()
        ticket=cut._state()['tickets'][ref]
        self.assertEqual(ticket['plan']['line_uuid'], self.base.invoke('inspect', {'pattern_uuid':'source'})['shape_refs'][0]['line_uuids'][0])
        self.batch.assert_not_called(); self.base.action_mock.assert_not_called(); self.assertFalse(self.base.selects)
        result=self.base.execute(ref)
        self.assertEqual(result['status'],'SUCCEEDED',result)
        self.assertTrue(result['readback']['native_partition_observed'])
        self.assertEqual(self.batch.call_count,1); self.base.action_mock.assert_not_called()
        self.assertEqual(self.trace[-5:],['batch_begin','press','release','Cut','batch_end'])
        self.assertEqual(self.base.sdk.getters,0); self.assertEqual(self.base.sdk.calls,[])
        self.assertEqual(self.base.execute(ref)['error']['code'],'STALE_SELECTION_REFERENCE')

    def test_completed_cut_reads_fresh_snapshot_once_after_uuid_mismatch(self):
        self.install()
        ref = self.base.prepare()
        capture_current = cut._capture_current
        post_cut_reads = 0

        def capture(api):
            nonlocal post_cut_reads
            if self.base.changed:
                post_cut_reads += 1
                if post_cut_reads == 1:
                    raise cut._native._CutError('SNAPSHOT_UUID_MISMATCH', 'Unstable export')
            return capture_current(api)

        with patch.object(cut, '_capture_current', side_effect=capture):
            result = self.base.execute(ref)
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertEqual(result['first_post_cut_read_error']['code'], 'SNAPSHOT_UUID_MISMATCH')
        self.assertEqual(post_cut_reads, 2)
        self.batch.assert_called_once()

    def test_semantics_default_surface_and_stale_frame_zero_cut(self):
        self.install(verify=lambda *a:{'verified':True})
        r=self.base.invoke('prepare',self.base.prepare_params())
        self.assertEqual(r['status'],'UNSUPPORTED'); self.batch.assert_not_called()
        self.install(); ref=self.base.prepare()
        r=self.base.invoke('execute',{'selection_ref':ref,'scene_token':fixture.TOKEN})
        self.assertEqual(r['error']['code'],'SURFACE_PRESERVATION_UNSUPPORTED')
        r=raw('inspect'); r['after']['view_fingerprint']='f'*64
        self.inspect.return_value={'status':'SUCCEEDED','effects_started':False,'requires_recovery':False,'result':r}
        self.assertEqual(self.base.execute(ref)['error']['code'],'STALE_ATOMIC_VIEW'); self.batch.assert_not_called()

    def test_failed_native_call_still_independently_reads_after_and_blocks(self):
        self.install(); ref=self.base.prepare(); old=self.base.reads
        self.batch.side_effect=RuntimeError('synthetic no receipt')
        r=self.base.execute(ref)
        self.assertEqual(r['status'],'UNKNOWN'); self.assertTrue(r['requires_recovery'])
        self.assertIn('readback',r); self.assertGreater(self.base.reads,old)
        self.assertFalse(r['mutation_observed']); self.assertTrue(cut._state()['blocked'])

    def test_native_mutation_at_reverify_stops_before_composite(self):
        self.install(); ref=self.base.prepare()
        original=self.verify
        def changed(plan,receipt,inspection,native):
            result=original(plan,receipt,inspection,native)
            self.base.sdk.native['PatternList'][1]['ShapeInfo']['LineList'][0]['PointList'][0]['Position']['x']+=.1
            return result
        cut._state()['selector']['verify']=changed
        r=self.base.execute(ref)
        self.assertEqual(r['error']['code'],'SCENE_CHANGED_BEFORE_CUT'); self.batch.assert_not_called()

    def test_inspect_refs_survive_export_alias_changes_across_rpcs(self):
        self.install()
        inspected = self.base.invoke('inspect', {'pattern_uuid': 'source'})
        target = inspected['shape_refs'][0]

        def rekey(native, prefix):
            _, aliases = cut.curves._identity_graph(native)
            renamed = {key: f'{prefix}{i}' for i, key in enumerate(aliases)}

            def visit(value):
                if isinstance(value, dict):
                    return {k: visit(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [visit(v) for v in value]
                return renamed.get(value, value) if isinstance(value, str) else value

            return visit(native)

        self.base.sdk.native = rekey(self.base.sdk.native, 'prepare-export-')
        prepared = self.base.invoke('prepare', {
            'pattern_uuid': 'source', 'shape_uuid': target['shape_uuid'], 'line_uuid': target['line_uuids'][0],
            'topology_fingerprint': inspected['topology_fingerprint'], 'scene_token': inspected['scene_token'],
        })
        self.assertEqual(prepared['status'], 'SUCCEEDED', prepared)
        self.base.sdk.native = rekey(self.base.sdk.native, 'execute-export-')
        result = self.base.execute(prepared['selection_ref'])
        self.assertEqual(result['status'], 'SUCCEEDED', result)
        self.assertEqual(self.batch.call_count, 1)

    def test_malformed_atomic_result_still_checks_after_without_retry(self):
        self.install()
        ref = self.base.prepare()

        def malformed(binding):
            self.action(binding)
            return {'status': 'SUCCEEDED', 'result': None}

        self.batch.side_effect = malformed
        result = self.base.execute(ref)
        self.assertEqual(result['status'], 'UNKNOWN', result)
        self.assertTrue(result['readback']['native_partition_observed'])
        self.assertTrue(result['requires_recovery'])
        self.assertTrue(result['do_not_retry'])
        self.assertEqual(self.batch.call_count, 1)

    def test_surface_export_scene_drift_keeps_known_cut_recovery_evidence(self):
        from md_mcp_plugin.embedded import dispatch

        cut.install_atomic_line_selector(self.candidate, self.verify, name='synthetic frame',
                                         evidence='Offline callbacks only', surface_readback=True)
        ref = self.base.prepare()
        surface_capture = cut._surface_capture
        before_asset = {'path': 'C:/test/mesh.obj', 'sha256': 'a' * 64}

        def drift(owners, token):
            if token == fixture.TOKEN:
                return {'scene_token': token, 'export': {'obj_asset': before_asset}}, ['Source']
            self.assertEqual(token, fixture.AFTER_TOKEN)
            self.assertEqual(set(owners), {'source', 'created'})
            with patch.object(cut, 'scene', return_value={'scene_token': 'e' * 64}):
                return surface_capture(owners, token)

        with patch.object(cut, '_surface_capture', side_effect=drift), patch.object(dispatch, '_module') as module:
            result = self.base.invoke('execute', {'selection_ref': ref, 'scene_token': fixture.TOKEN})
        module.return_value._mesh_read.assert_not_called()
        self.assertEqual(result['status'], 'PARTIAL', result)
        self.assertTrue(result['readback']['native_partition_observed'])
        self.assertEqual(result['surface_preservation']['error']['code'], 'SURFACE_SCENE_CHANGED')
        self.assertEqual(result['surface_preservation']['before_capture']['obj_asset'], before_asset)
        self.assertEqual(result['scene_token_after'], fixture.AFTER_TOKEN)
        self.assertIn('after_native_fingerprint', result)
        self.assertTrue(result['adapter_response']['result']['dispatch_completed'])
        self.assertTrue(result['requires_recovery'])
        self.assertEqual(self.batch.call_count, 1)

    def test_contradictory_zero_input_response_cannot_wash_a_cut(self):
        self.install(); ref=self.base.prepare()
        self.batch.side_effect=None
        self.batch.return_value={'status':'REJECTED','effects_started':False,'requires_recovery':False,
                                 'result':{'verified':False,'QAction_trigger_calls':1,'event_calls':2}}
        r=self.base.execute(ref)
        self.assertEqual(r['status'],'UNKNOWN'); self.assertTrue(r['requires_recovery'])
        self.assertEqual(r['Qt_Cut_trigger_calls'],1); self.assertIn('readback',r)


class CutProjectRecoveryContracts(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.source = Path(directory) / 'fixture.zprj'
        self.source.write_bytes(b'saved project')
        digest = sha256(self.source.read_bytes()).hexdigest()
        identity = {'pid': 42, 'creation_filetime': 43, 'project_path': str(self.source)}
        self.current = {'identity': identity, 'patterns': ['source', 'child'], 'scene_token': 'a' * 64}
        self.reopened = {'identity': identity, 'patterns': ['source'], 'scene_token': 'b' * 64}
        self.native = raw()
        self.native.update(status='PARTIAL', return_code=-10, verified=False, requires_recovery=True,
                           error={'code': 'COOPERATIVE_BUDGET_EXCEEDED'})
        self.native.pop('dispatch_completed')
        self.parent = 'c' * 32
        target = {'pattern_uuid': 'source', 'curve': {'points_mm': [[0, 0], [2, 2]]},
                  'scene_geometry_fingerprint': 'pre', 'selection_board_fingerprint': 'board'}
        self.original = {
            'status': 'UNKNOWN',
            'result': {'single_native_click_cut_batch': True, 'target': target, 'scene_token_before': 'd' * 64,
                       'adapter_response': {'status': 'PARTIAL', 'requires_recovery': True, 'effects_started': True,
                                            'result': self.native}},
        }
        self.params = {'blocked_request_id': self.parent, 'expected_source_sha256': digest}
        checkpoint = {'identity': identity, 'scene_token': 'd' * 64, 'source_sha256': digest,
                      'before': {'scene_fingerprint': 'pre', 'board_fingerprint': 'board'}, 'target': target}
        self.cut_state = {'blocked': True, 'pending_cut_readback': checkpoint, 'tickets': {'old': True}}
        self.library = object()
        self.atomic_state = {'blocked': True, 'busy': False, 'library': self.library,
                             'process': {'pid': 42, 'creation_filetime': 43}}
        cut_module = SimpleNamespace(
            _state=lambda: self.cut_state,
            _capture_current=lambda api: {'rows': self.current['patterns']},
            _native=SimpleNamespace(_api=lambda: None, _partition=Mock(return_value={'native_partition_observed': True})),
        )
        canvas = SimpleNamespace(
            _state=lambda: {'configuration': True},
            views=Mock(return_value={'status': 'SUCCEEDED', 'result': {
                'physical': {'background_safe': True}, 'views': [{'scene_item_grabber_absent': True}],
            }}),
        )
        modules = {'cut_qt': cut_module, 'cut_qt_atomic': atom, 'canvas': canvas}
        self.enterContext(patch.object(dispatch, '_module', side_effect=modules.__getitem__))
        self.enterContext(patch.object(dispatch, '_RECEIPTS', {self.parent: self.original}))
        self.enterContext(patch.object(atom, '_state', return_value=self.atomic_state))
        self.enterContext(patch.object(project, 'scene', side_effect=lambda: self.current))
        self.open = self.enterContext(patch.object(project, 'open_project', side_effect=self.reload))

    def reload(self, params):
        self.current = self.reopened
        return {'verified': True, 'source_file_unchanged': True, 'source_sha256': self.params['expected_source_sha256'],
                'scene': self.reopened}

    def test_closed_timeout_reloads_then_retires_bridge_without_changing_original(self):
        original = copy.deepcopy(self.original)
        receipt = project.recover_cut(self.params)
        self.open.assert_called_once_with({'path': str(self.source), 'replace_current': True})
        self.assertEqual(self.original, original)
        self.assertEqual(receipt['original_mutation_status'], 'UNKNOWN')
        self.assertTrue(receipt['cut_reconciliation']['partition']['native_partition_observed'])
        self.assertFalse(self.cut_state['blocked'])
        self.assertFalse(self.atomic_state['blocked'])
        self.assertEqual(self.atomic_state['retired_libraries'], [self.library])

    def test_incomplete_release_never_reloads(self):
        self.native['release_returned'] = False
        with self.assertRaises(PluginError) as failure:
            project.recover_cut(self.params)
        self.assertIs(failure.exception.details['effects_started'], False)
        self.open.assert_not_called()
        self.assertTrue(self.atomic_state['blocked'])

    def test_completed_batch_can_recover_after_the_immediate_native_read_failed(self):
        result = self.original['result']
        result['adapter_response'] = {'status': 'SUCCEEDED', 'requires_recovery': False,
                                      'effects_started': True, 'result': raw()}
        result['error'] = {'code': 'POST_CUT_READBACK_FAILED'}
        self.atomic_state['blocked'] = False
        receipt = project.recover_cut(self.params)
        self.assertTrue(receipt['cut_reconciliation']['partition']['native_partition_observed'])
        self.open.assert_called_once()
        self.assertIs(self.atomic_state['library'], self.library)

    def test_unverified_reload_retains_hold_and_prevents_another_import(self):
        self.open.side_effect = None
        self.open.return_value = {'verified': False, 'scene': self.current}
        with self.assertRaises(PluginError) as failure:
            project.recover_cut(self.params)
        self.assertIs(failure.exception.details['effects_started'], True)
        with self.assertRaises(PluginError):
            project.recover_cut(self.params)
        self.open.assert_called_once()
        self.assertTrue(self.atomic_state['blocked'])


if __name__=='__main__': unittest.main()
