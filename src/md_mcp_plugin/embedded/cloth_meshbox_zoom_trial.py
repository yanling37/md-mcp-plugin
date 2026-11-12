"""One background Front/Zoom Cloth observation for a root-owned disposable copy.

Call from the MD GUI-thread transaction that owns the mutation lease. Reading,
MeshBox sampling/reconciliation and checkpoint restoration use the owner's
existing guarded entry points. Importing this module never loads an MD API.
"""

from copy import deepcopy
from hashlib import file_digest
from pathlib import Path


_IDENTITY = ('scene_identity', 'root_window', 'checkpoint_sha256')
_STABLE = ('world_arrays_sha256', 'mode', 'world_checked', 'sdk_selection', 'simulation_stopped')
_VIEW = ('viewport_2d', 'viewport_3d', 'frame_2d_sha256', 'frame_3d_sha256')
_CAMERA = ('cmt_sha256', 'cpt_sha256', 'matrix')


def _state(snapshot):
    if not isinstance(snapshot, dict) or not set((*_IDENTITY, *_STABLE, 'scene_token', 'camera', 'background_verified')) <= snapshot.keys():
        raise ValueError('Read the bound disposable project, background endpoint, camera, mode and mesh')
    if (snapshot['background_verified'] is not True or snapshot['simulation_stopped'] is not True
            or snapshot['mode'] != 'MovePattern' or snapshot['world_checked'] is not True):
        raise ValueError('The root must verify background MovePattern/World and stopped simulation')
    camera = snapshot['camera']
    if any(key not in camera for key in _CAMERA):
        raise ValueError('Current CMT matrix and CMT/CPT hashes are required')
    return snapshot


def recovery_differences(before, after):
    """Compare restored observable state, allowing new opaque view refs after reopen.

    The owner supplies semantic viewport state without volatile native handles
    or opaque references. Both PNG hashes are captured in the original tool,
    selection, visibility and rendering modes.
    """
    _state(before)
    _state(after)
    if any(key not in state for state in (before, after) for key in _VIEW):
        raise ValueError('Both viewport states and rendered frame hashes are required for recovery')
    return ([key for key in (*_IDENTITY, *_STABLE, *_VIEW) if before[key] != after[key]]
            + [f'camera.{key}' for key in _CAMERA if before['camera'][key] != after['camera'][key]])


def _same_trial(original, current):
    return all(original[k] == current[k] for k in (*_IDENTITY, *_STABLE, 'scene_token'))


def reopen_disposable_checkpoint(before, *, prepared_directory, utility_api, import_api, api_types):
    """N237 non-dialog reopen. Call only after the original native ticket is closed.

    The outer trial must first bind the current process, window and checkpoint.
    Reestablish the original view/selection in the root callback and read them
    back separately with recovery_differences.
    """
    checkpoint = Path(before['scene_identity']['project_path'])
    prepared = Path(prepared_directory).resolve(strict=True)
    if (not checkpoint.is_absolute() or checkpoint.suffix.lower() != '.zprj'
            or checkpoint.resolve(strict=True) != checkpoint or checkpoint.is_symlink()
            or not checkpoint.is_relative_to(prepared) or not checkpoint.is_file() or checkpoint.stat().st_nlink != 1):
        raise ValueError('Restore only the bound independent project in the prepared test directory')

    def checksum():
        with checkpoint.open('rb') as stream:
            return file_digest(stream, 'sha256').hexdigest()

    if checksum() != before['checkpoint_sha256']:
        raise ValueError('The saved disposable checkpoint hash changed')
    options = api_types.ImportZPRJOption()
    fields = {'bAppend': False, 'bLoadGarment': True, 'bLoadAvatar': True,
              'translationValueX': 0., 'translationValueY': 0., 'translationValueZ': 0.}
    if not all(hasattr(options, key) for key in fields):
        raise ValueError('The N237 non-dialog import options differ in this build')
    for name, value in fields.items():
        setattr(options, name, value)
    utility_api.NewProject()
    returned = import_api.ImportZprjW(str(checkpoint), options)
    if returned is not True or checksum() != before['checkpoint_sha256']:
        raise ValueError('Checkpoint reopen or preserved source bytes could not be verified')
    return {'checkpoint_reopened': True, 'checkpoint_sha256': before['checkpoint_sha256']}


def run_front_zoom_trial(utility_api, *, observe, sample_meshbox, restore_checkpoint):
    """Run the observed N237 camera chain and one guarded MeshBox sample.

    observe(full_viewports=True) snapshots the restoration evidence. Intermediate
    observe(full_viewports=False) reads the bound scope and camera. sample_meshbox
    returns the closed acceptance response or a response explicitly requiring
    recovery. The root provides a prequalified, non-dialog restore callback.
    """
    if not all(callable(fn) for fn in (observe, sample_meshbox, restore_checkpoint)):
        raise ValueError('Bind the existing observation, MeshBox and checkpoint callbacks')
    front = getattr(utility_api, 'SetCamViewPoint', None)
    zoom = getattr(utility_api, 'SetZoomView', None)
    signature = ''.join((getattr(zoom, '__doc__', '') or '').lower().split())
    if not callable(front) or not callable(zoom) or 'setzoomview()->none' not in signature:
        raise ValueError('The runtime must provide the observed no-argument SetZoomView() -> None')
    before = deepcopy(_state(observe(full_viewports=True)))
    if any(key not in before for key in _VIEW):
        raise ValueError('Capture both original viewports before entering the camera transaction')
    report = {'schema': 'md-meshbox-front-zoom-trial/1', 'before': before, 'camera_calls': [],
              'view_restored': False, 'requires_recovery': True, 'zoom_effect_observed': False}
    sample_may_be_open = False
    try:
        report['camera_calls'].append('SetCamViewPoint(2)')
        front(2)
        canonical = deepcopy(_state(observe(full_viewports=False)))
        matrix = canonical['camera']['matrix']
        if (not _same_trial(before, canonical) or [r[:3] for r in matrix[:3]] != [[1, 0, 0], [0, 1, 0], [0, 0, 1]]):
            raise ValueError('Front camera basis or bound trial scope differs')
        report['camera_calls'].append('SetZoomView()')
        if zoom() is not None:
            raise ValueError('SetZoomView did not return the observed None value')
        enlarged = deepcopy(_state(observe(full_viewports=False)))
        if (not _same_trial(before, enlarged) or [r[:3] for r in enlarged['camera']['matrix'][:3]] != [r[:3] for r in matrix[:3]]
                or enlarged['camera']['cmt_sha256'] == canonical['camera']['cmt_sha256']):
            raise ValueError('A same-scope Front camera framing change is not observed')
        report.update(front_camera=canonical['camera'], zoom_camera=enlarged['camera'], zoom_effect_observed=True)
        sample_may_be_open = True
        sample = sample_meshbox()
        report['sample'] = sample
        sampled = sample.get('result') or {}
        sample_may_be_open = not (sample.get('requires_recovery') is False and
                                   (sample.get('effects_started') is False or sampled.get('transaction_closed') is True))
        if sample_may_be_open:
            raise ValueError('Reconcile the original MeshBox ticket before restoring its camera checkpoint')
        if (sample.get('status') != 'SUCCEEDED' or sampled.get('selection_readback', {}).get('verified') is not True
                or sampled.get('scene_token') != before['scene_token']):
            raise ValueError('The single paired MeshBox sample did not qualify')
        for key in ('selection_3d_before', 'selection_3d_after'):
            if any(sampled[key]['camera'][k] != enlarged['camera'][k] for k in _CAMERA):
                raise ValueError('The sample did not retain the observed zoom camera')
    except Exception as exc:
        report['trial_error'] = str(exc)
    finally:
        if not sample_may_be_open:
            try:
                current = _state(observe(full_viewports=False))
                if any(current[k] != before[k] for k in (*_IDENTITY, 'scene_token')):
                    raise ValueError('The original disposable checkpoint scope is no longer active')
                report['restore'] = restore_checkpoint(before)
                after = _state(observe(full_viewports=True))
                differences = recovery_differences(before, after)
                report.update(restored=after, recovery_differences=differences, view_restored=not differences,
                              requires_recovery=bool(differences))
            except Exception as exc:
                report['recovery_error'] = str(exc)
    report['verified'] = report['view_restored'] and 'trial_error' not in report
    return report
