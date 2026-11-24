"""Measured 2D handle writing: current identity, local camera tracking, native readback.

Raster scale only chooses preparation. Two actual native responses calibrate the
current input Jacobian; the final native XY and preservation decide success.
"""
import math
from pathlib import Path

from . import curves, selection
from .curve_projection import track_marker


def solve_projection(*args, **options):
    from .dispatch import _module

    _module('curve_projection')
    return _module('curve_partial_projection').solve_with_fallback(*args, **options)


class StopWrite(ValueError):
    def __init__(self, code, message, recovery=False):
        self.code, self.recovery = code, recovery
        super().__init__(message)


def inverse_jacobian(columns):
    """Invert measured mm/input-pixel columns after independent-axis checks."""
    if (type(columns) not in (tuple,list) or len(columns)!=2
            or any(type(c) not in (tuple,list) or len(c)!=2 for c in columns)
            or any(type(v) not in (int,float) or not math.isfinite(v)for c in columns for v in c)):
        raise StopWrite('NATIVE_INPUT_JACOBIAN_REJECTED','Finite measured two-axis input columns required')
    x, y = columns
    nx, ny = math.hypot(*x), math.hypot(*y)
    determinant = x[0]*y[1]-y[0]*x[1]
    if (min(nx,ny) <= 1e-9 or max(nx,ny)/min(nx,ny) > 1.05
            or abs(sum(a*b for a,b in zip(x,y)))/(nx*ny) > .02
            or abs(determinant)/(nx*ny) < .99):
        raise StopWrite('NATIVE_INPUT_JACOBIAN_REJECTED', 'Actual X/Y input responses are not a stable nonsingular 2D mapping')
    return [[y[1]/determinant,-y[0]/determinant],[-x[1]/determinant,x[0]/determinant]],max(nx,ny)


def set_handle(request, adapter):
    from .native_curve_provider import _resolve, _scope
    trace=[]
    effects=False
    geometry_dispatched=False
    all_closed=True
    initial=current=plan=verification=None
    hit_offset=[0.,0.]

    def reply(status, code=None, message=None, recovery=False, **extra):
        return dict(status=status,effects_started=effects,geometry_effects_started=geometry_dispatched,
            requires_recovery=recovery,closed_input_batch_verified=all_closed and effects and not recovery,
            error=dict(code=code,message=message) if code else None,trace=trace,
            verification=verification,verification_scope='Native 2D target, fixed endpoints/opposite handles/other semantics; no solver Rest or 3D surface certification',**extra)

    def native(name, response):
        nonlocal effects,all_closed,geometry_dispatched
        raw=response.get('result') or {}
        effects=effects or response.get('effects_started',False)
        if name=='bezier_drag': geometry_dispatched=geometry_dispatched or response.get('effects_started',False)
        trace.append(dict(operation=name,status=response['status'],error=response.get('error'),
            event_calls=raw.get('event_calls',0),requires_recovery=response.get('requires_recovery',False),
            paired_release_verified=raw.get('paired_release_verified',False),
            events=raw.get('events',[]),snap_settings=raw.get('snap_settings'),image=raw.get('image')))
        if response['status'] not in ('SUCCEEDED','NO_OP'):
            all_closed=all_closed and not response.get('requires_recovery',False)
            raise StopWrite('CURVE_'+name.upper()+'_FAILED',str(response.get('error')),response.get('requires_recovery',False))
        return raw

    def capture():
        inspected=native('inspect',adapter.inspect_2d({}))
        if inspected['tools_after'].get('current_tool')!='edit_curve_point':
            raise StopWrite('CURVE_MODE_REQUIRED','Select Edit Curve Point before writing')
        raw=native('capture',adapter.capture_2d(_scope({'result':inspected})))
        image=raw['image']
        return raw,Path(image['path']).read_bytes()

    def scope_after(raw):
        view=raw['after']
        return dict(view_ref=view['view_ref'],view_fingerprint=view['view_fingerprint'],scene_token=raw['scene_token_after'])

    def readback(label):
        nonlocal current,verification
        measured=curves._read_current(request['pattern_uuid'])
        _resolve(measured,request)
        verification=curves.verify_edit(initial['native'],measured['native'],plan)
        trace.append(dict(operation='native_readback',stage=label,
            actual_xy_mm=verification['actual_xy_mm'],target_error_mm=verification['target_error_mm'],
            fixed_endpoints_and_other_semantics_preserved=verification['fixed_endpoints_and_other_semantics_preserved']))
        if not verification['fixed_endpoints_and_other_semantics_preserved']:
            raise StopWrite('NON_TARGET_NATIVE_CHANGE','Native preservation verification failed',True)
        current=measured
        return verification

    def refresh_capture(expected):
        native('refresh_pattern_selection',selection.select_pattern({'pattern_uuid':request['pattern_uuid'],'refresh':True}))
        raw,png=capture()
        view_only_readback('after_selection_capture')
        marker=track_marker(png,expected,device_pixel_ratio=raw['image']['device_pixel_ratio'],allowed_colors=('blue','yellow'))
        trace.append(dict(operation='local_marker',image=raw['image'],marker=marker['trace_record']))
        return raw,marker

    def view_only_readback(label):
        baseline=current
        readback(label)
        if (current['scope']!=baseline['scope'] or current['identities']!=baseline['identities']
                or any(current['public'][k]!=baseline['public'][k]for k in ('scene_fingerprint','geometry_fingerprint','topology_fingerprint'))):
            raise StopWrite('VIEW_PREPARATION_CHANGED_NATIVE_DATA','View/selection preparation changed the current native geometry',True)

    def drag(raw,start,pixels,label,coarse=False):
        end=[a+b for a,b in zip(start,pixels)]
        width,height=raw['image']['viewport_size']
        if math.hypot(*pixels)>300 or not (3<=end[0]<width-3 and 3<=end[1]<height-3):
            raise StopWrite('TARGET_OUTSIDE_PRECISION_VIEW','Measured correction is outside the current viewport')
        drag_params = dict(scope_after(raw), start=list(start), end=end)
        if not coarse:
            # Leave room for the move callback and the checked release in each zoomed-in batch.
            drag_params['budget_ms'] = 1500
        native('bezier_drag', adapter.bezier_drag(drag_params))
        return readback(label)

    def finish():
        # Restore the separate prerequisite's Fit Selected view after precision zoom.
        native('refresh_pattern_selection',selection.select_pattern({'pattern_uuid':request['pattern_uuid'],'refresh':True}))
        inspected=native('inspect',adapter.inspect_2d({}))
        native('fit_selected',adapter.fit_selected(_scope({'result':inspected})))
        view_only_readback('after_fit_restoration')
        if not verification['target_reached']:
            raise StopWrite('TARGET_CHANGED_AFTER_VIEW_RESTORATION','Native target no longer meets tolerance after restoring the view',True)
        return reply('SUCCEEDED')

    def two_scale_finish(raw,marker,fine_columns,fine_inverse,origin,resolution,hit_offset):
        from .dispatch import _module
        lattice_solver=_module('curve_lattice')
        planner=lattice_solver.plan_two_scales
        def return_origin(raw,marker,inverse,origin,resolution,label):
            delta=[a-b for a,b in zip(origin,verification['actual_xy_mm'])]
            pixels=[sum(row[i]*delta[i]for i in (0,1))for row in inverse]
            start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
            if math.hypot(*pixels)>1e-7:
                drag(raw,start,pixels,label)
                if math.dist(verification['actual_xy_mm'],origin)>resolution*1.5:
                    raise StopWrite('CALIBRATION_RETURN_REJECTED','Measured calibration return exceeded its input resolution')
                return refresh_capture([a+b for a,b in zip(start,pixels)])
            return raw,marker
        def wheel(raw,marker,outward):
            method=adapter.zoom_out_at if outward else adapter.zoom_in_at
            native('zoom_out_at' if outward else 'zoom_in_at',method(dict(scope_after(raw),point=marker['hit_xy_px'])))
            raw,png=capture()
            view_only_readback('after_lattice_view_change')
            marker=track_marker(png,marker['hit_xy_px'],device_pixel_ratio=raw['image']['device_pixel_ratio'],allowed_colors=('blue','yellow'))
            trace.append(dict(operation='lattice_view_marker',outward=outward,image=raw['image'],marker=marker['trace_record']))
            return raw,marker
        if not hasattr(adapter,'zoom_out_at'):
            raise StopWrite('TWO_SCALE_CAMERA_REQUIRED','This native resolution requires the fixed outward camera operation')
        raw,marker=return_origin(raw,marker,fine_inverse,origin,resolution,'return_fine_calibration_origin')
        fine_return_exact = verification['actual_xy_mm'] == origin
        if verification['target_reached']:return finish()
        def measure_coarse(raw,marker,outward_steps):
            raw,marker=wheel(raw,marker,True)
            origin=verification['actual_xy_mm'][:]
            coarse_columns=[]
            for axis in (0,1):
                start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
                step=24. if start[axis]+24<raw['image']['viewport_size'][axis]-4 else -24.
                pixels=[0.,0.];pixels[axis]=step
                before=verification['actual_xy_mm'][:]
                drag(raw,start,pixels,'coarse_scale_calibration_'+str(axis))
                coarse_columns.append([(a-b)/step for a,b in zip(verification['actual_xy_mm'],before)])
                raw,marker=refresh_capture([a+b for a,b in zip(start,pixels)])
            coarse_inverse,coarse_resolution=inverse_jacobian(coarse_columns)
            trace.append(dict(operation='two_scale_native_jacobians',fine_columns=fine_columns,
                coarse_columns=coarse_columns,outward_steps=outward_steps))
            raw,marker=return_origin(raw,marker,coarse_inverse,origin,coarse_resolution,'return_coarse_calibration_origin')
            return raw,marker,coarse_columns,verification['actual_xy_mm'] == origin

        def plan_current(coarse_columns, coarse_return_exact, cycle):
            delta = [a-b for a,b in zip(plan['target_xy_mm'], verification['actual_xy_mm'])]
            options = dict(tolerance_mm=request['tolerance_mm'], min_batch_length_px=16)
            try:
                return planner(fine_columns, coarse_columns, delta, **options)
            except lattice_solver.LatticeError as exc:
                drags = [row for row in trace if row['operation'] == 'bezier_drag']
                closed = all_closed and all(row['status'] == 'SUCCEEDED' and row['paired_release_verified']
                    and not row['requires_recovery'] for row in drags)
                if (exc.code != 'LATTICE_TARGET_UNREACHABLE' or cycle != 0
                        or not (closed and fine_return_exact and coarse_return_exact)):
                    raise
                return planner(fine_columns, coarse_columns, delta, prediction_budget_fraction=.85, **options)

        outward_steps=1
        raw,marker,coarse_columns,coarse_return_exact=measure_coarse(raw,marker,outward_steps)
        for cycle in range(2):
            if verification['target_reached']:return finish()
            try:
                lattice=plan_current(coarse_columns,coarse_return_exact,cycle)
            except lattice_solver.LatticeError as exc:
                if exc.code!='LATTICE_TARGET_UNREACHABLE' or outward_steps!=1:
                    raise
                outward_steps=2
                raw,marker,coarse_columns,coarse_return_exact=measure_coarse(raw,marker,outward_steps)
                if verification['target_reached']:return finish()
                lattice=plan_current(coarse_columns,coarse_return_exact,cycle)
            previous_error=verification['target_error_mm']
            trace.append(dict(operation='two_scale_integer_plan',cycle=cycle,plan=lattice,outward_steps=outward_steps))
            for outward,key in ((True,'coarse_steps_px'),(False,'fine_steps_px')):
                if not outward:
                    for _ in range(outward_steps):
                        raw,marker=wheel(raw,marker,False)
                pixels=lattice[key]
                if any(pixels):
                    start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
                    drag(raw,start,pixels,'lattice_'+str(cycle)+'_'+key)
                    if verification['target_reached']:return finish()
                    raw,marker=refresh_capture([a+b for a,b in zip(start,pixels)])
            if verification['target_error_mm']>=previous_error-1e-7:
                raise StopWrite('LATTICE_RESIDUAL_NOT_DECREASING','Measured multi-scale correction stopped reducing the target residual')
            for _ in range(outward_steps):
                raw,marker=wheel(raw,marker,True)
        raise StopWrite('LATTICE_TARGET_NOT_REACHED','Two measured multi-scale corrections did not meet the native target')

    try:
        if [p['pattern_uuid'] for p in selection.inspect_selection({})['selected']] != [request['pattern_uuid']]:
            return reply('UNSUPPORTED','PATTERN_SELECTION_REQUIRED','Call md_select_pattern for this UUID first')
        initial=current=curves._read_current(request['pattern_uuid'])
        analysis,ref,target=_resolve(initial,request)
        if target['identity']['shape_kind']!='outer':
            return reply('UNSUPPORTED','OUTER_HANDLE_REQUIRED','Precision raster binding currently requires an outer cubic handle')
        plan=curves.prepare_edit(initial['native'],request['pattern_uuid'],ref,request['target_xy_mm'],
            tolerance_mm=request['tolerance_mm'],scene_scope=initial['scope'])
        raw,png=capture()
        view_only_readback('after_initial_capture')
        anchors=[dict(id=k,xy_mm=v)for k,v in analysis['outer_endpoints'].items()]
        handles=[dict(id=h['handle_uuid'],xy_mm=h['xy_mm'])for c in analysis['public']['curves']
                 if c['shape_kind']=='outer' for h in c['handles']]
        projection=solve_projection(png,anchors,handles,target['native_ids']['handle_uuid'],
                                   device_pixel_ratio=raw['image']['device_pixel_ratio'])
        trace.append(dict(operation='initial_semantic_projection',anchor_max_error_px=projection['anchor_max_error_px'],
            missing_anchor_ids=projection.get('missing_anchor_ids',[]),target_marker_error_px=projection['target_marker_error_px']))
        marker=track_marker(png,projection['target_xy_px'],device_pixel_ratio=raw['image']['device_pixel_ratio'])
        matrix=projection['linear']
        delta=[a-b for a,b in zip(plan['target_xy_mm'],plan['current_xy_mm'])]
        pixels=[sum(row[i]*delta[i]for i in (0,1))for row in matrix]
        # One coarse closed batch avoids moving a large residual at high zoom.
        first_start=[sum(row[i]*target['current_xy_mm'][i]for i in (0,1))+offset
                     for row,offset in zip(matrix,projection['translation'])]
        hit_offset=[a-b for a,b in zip(first_start,marker['hit_xy_px'])]
        if math.hypot(*pixels)>1e-6:
            drag(raw,first_start,pixels,'coarse',coarse=True)
            if verification['target_reached']: return finish()
        else:
            readback('initial')
            if verification['target_reached']: return reply('SUCCEEDED')
        actual_delta=[a-b for a,b in zip(verification['actual_xy_mm'],plan['current_xy_mm'])]
        expected=[s+sum(row[i]*actual_delta[i]for i in (0,1))for s,row in zip(first_start,matrix)]
        raw,marker=refresh_capture(expected)
        scale=math.hypot(matrix[0][0],matrix[1][0])
        # 1.25 is a preparation estimate only. Actual input resolution is measured
        # below and never inferred from wheel dispatch or pixel calibration.
        zoom_count=max(0,min(40,math.ceil(math.log(max(1,2/request['tolerance_mm']/scale),1.25))))
        total_zoom=0
        for calibration_stage in range(2):
            consecutive_unchanged_frames = 0
            for step in range(zoom_count):
                previous_image_sha = raw['image'].get('sha256')
                native('zoom_in_at',adapter.zoom_in_at(dict(scope_after(raw),point=marker['hit_xy_px'])))
                following,png=capture()
                view_only_readback('after_zoom_'+str(total_zoom))
                marker=track_marker(png,marker['hit_xy_px'],device_pixel_ratio=following['image']['device_pixel_ratio'],allowed_colors=('blue','yellow'))
                trace.append(dict(operation='zoom_marker',step=total_zoom,image=following['image'],marker=marker['trace_record']))
                raw=following;total_zoom+=1
                same_frame = previous_image_sha is not None and raw['image'].get('sha256') == previous_image_sha
                consecutive_unchanged_frames = consecutive_unchanged_frames + 1 if same_frame else 0
                if consecutive_unchanged_frames >= 2:
                    trace.append(dict(operation='zoom_frames_stable', zoom_steps=total_zoom))
                    break
            readback('after_zoom')
            calibration_origin=verification['actual_xy_mm'][:]
            columns=[]
            for axis in (0,1):
                start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
                size=raw['image']['viewport_size'][axis]
                step=24. if start[axis]+24<size-4 else -24.
                pixels=[0.,0.];pixels[axis]=step
                before=verification['actual_xy_mm'][:]
                drag(raw,start,pixels,'calibrate_'+str(calibration_stage)+'_'+str(axis))
                response=[(a-b)/step for a,b in zip(verification['actual_xy_mm'],before)]
                if math.hypot(*response)<1e-9:
                    raise StopWrite('NO_NATIVE_CALIBRATION_RESPONSE','Known-complete calibration has no native handle response')
                columns.append(response)
                raw,marker=refresh_capture([a+b for a,b in zip(start,pixels)])
            inverse,resolution=inverse_jacobian(columns)
            trace.append(dict(operation='measured_input_jacobian',columns_mm_per_px=columns,
                native_mm_per_input_pixel=resolution,zoom_steps=total_zoom,
                raster_estimate_is_accuracy_proof=False))
            if resolution<=request['tolerance_mm']/2:break
            if hasattr(adapter,'zoom_out_at'):
                return two_scale_finish(raw,marker,columns,inverse,calibration_origin,resolution,hit_offset)
            gain=(1/(scale*resolution))**(1/max(1,total_zoom))
            if calibration_stage or not 1.01<gain<3:
                raise StopWrite('NATIVE_INPUT_RESOLUTION_TOO_COARSE','Measured zoom did not establish sufficient native input resolution')
            # Return the calibration offset before increasing magnification,
            # keeping the actual goal near the same visible handle.
            delta=[a-b for a,b in zip(calibration_origin,verification['actual_xy_mm'])]
            pixels=[sum(row[i]*delta[i]for i in (0,1))for row in inverse]
            start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
            drag(raw,start,pixels,'return_calibration_origin')
            if math.dist(verification['actual_xy_mm'],calibration_origin)>resolution*1.5:
                raise StopWrite('CALIBRATION_RETURN_REJECTED','Measured calibration return did not stay within its known input resolution')
            raw,marker=refresh_capture([a+b for a,b in zip(start,pixels)])
            zoom_count=math.ceil(math.log(resolution/(request['tolerance_mm']/2),gain))+2
            if not 1<=zoom_count<=64 or total_zoom+zoom_count>96:
                raise StopWrite('PRECISION_ZOOM_BUDGET_EXCEEDED','Measured preparation exceeds the bounded zoom budget')
        if verification['target_reached']:return finish()
        previous_error=verification['target_error_mm']
        for correction in range(3):
            delta=[a-b for a,b in zip(plan['target_xy_mm'],verification['actual_xy_mm'])]
            pixels=[sum(row[i]*delta[i]for i in (0,1))for row in inverse]
            if math.hypot(*pixels)<1e-7:
                raise StopWrite('ZERO_PIXEL_CORRECTION','Nonzero native residual produced no measurable correction')
            start=[a+b for a,b in zip(marker['hit_xy_px'],hit_offset)]
            drag(raw,start,pixels,'correction_'+str(correction))
            if verification['target_reached']: return finish()
            if verification['target_error_mm']>=previous_error-1e-7:
                raise StopWrite('TARGET_RESIDUAL_NOT_DECREASING','Measured target error stopped decreasing')
            previous_error=verification['target_error_mm']
            raw,marker=refresh_capture([a+b for a,b in zip(start,pixels)])
        raise StopWrite('TARGET_TOLERANCE_NOT_REACHED','Three known-complete measured corrections did not reach the native target')
    except Exception as exc:
        recovery=getattr(exc,'recovery',False) or not all_closed
        if initial is not None and plan is not None and all_closed:
            try:
                readback('stopped')
                if effects:
                    native('refresh_pattern_selection',selection.select_pattern({'pattern_uuid':request['pattern_uuid'],'refresh':True}))
                    inspected=native('inspect',adapter.inspect_2d({}))
                    native('fit_selected',adapter.fit_selected(_scope({'result':inspected})))
                    readback('after_stopped_fit_restoration')
            except Exception: recovery=True
        changed=verification is not None and not verification['scene_unchanged']
        if geometry_dispatched and not changed and not recovery:
            return reply('SUCCEEDED',getattr(exc,'code','PRECISION_PROVIDER_ERROR'),str(exc),native_no_effect=True)
        return reply('PARTIAL' if changed or recovery else 'UNSUPPORTED',
                     getattr(exc,'code','PRECISION_PROVIDER_ERROR'),str(exc),recovery=recovery or changed)
