"""Bounded two-scale integer-pixel planning; no native owner or input authority.

Matrices are COLUMN vectors: [[dx/dpixelX, dy/dpixelX],
                             [dx/dpixelY, dy/dpixelY]].
Only a returned candidate's error is certified. Pruned search rejection does
not prove the absence of every possible integer solution.
"""
from fractions import Fraction
import math

_CANDIDATES = 24
_MAX_STEP = 32


class LatticeError(ValueError):
    def __init__(self, code, message, diagnostics=None):
        self.code, self.diagnostics = code, diagnostics or {}
        super().__init__(message)


def _fail(code, message):
    raise LatticeError(code, message)


def _vector(value, label):
    if (type(value) not in (list, tuple) or len(value) != 2
            or any(type(v) not in (int, float) for v in value)):
        _fail('INVALID_LATTICE_INPUT', label + ' must contain two finite numbers')
    try:
        result = [float(v) for v in value]
    except (OverflowError, ValueError):
        _fail('INVALID_LATTICE_INPUT', label + ' exceeds the finite numeric domain')
    if not all(math.isfinite(v) for v in result):
        _fail('INVALID_LATTICE_INPUT', label + ' must contain two finite numbers')
    return result


def _matrix(value, label):
    if type(value) not in (list, tuple) or len(value) != 2:
        _fail('INVALID_LATTICE_INPUT', label + ' must have two column vectors')
    columns = [_vector(v, label) for v in value]
    x, y = columns
    lengths = [math.hypot(*v) for v in columns]
    determinant = x[0]*y[1] - x[1]*y[0]
    product = lengths[0]*lengths[1]
    if (not all(math.isfinite(v) for v in [*lengths, determinant, product])
            or min(lengths) <= 1e-12 or product == 0
            or max(lengths)/min(lengths) > 1.05
            or abs(x[0]*y[0] + x[1]*y[1])/product > .02
            or abs(determinant)/product < .99):
        _fail('ILL_CONDITIONED_LATTICE', label + ' must be a stable near-orthogonal similarity')
    inverse = [[y[1]/determinant, -y[0]/determinant],
               [-x[1]/determinant, x[0]/determinant]]
    return columns, lengths, inverse


def _multiply(rows, vector):
    try:
        result = [math.fsum(a*b for a, b in zip(row, vector)) for row in rows]
    except (OverflowError, ValueError):
        _fail('LATTICE_STEP_BUDGET', 'Displacement exceeds the finite search domain')
    if not all(math.isfinite(v) for v in result):
        _fail('LATTICE_STEP_BUDGET', 'Displacement exceeds the finite search domain')
    return result


def plan_two_scales(fine_columns, coarse_columns, delta_mm, tolerance_mm=.002, max_step=32,
                    min_batch_length_px=0, *, prediction_budget_fraction=.5):
    """Find bounded coarse*m + fine*n within a separate predicted-error budget.

    Each of mX,mY,nX,nY is an integer in [-max_step,max_step], max_step <= 32.
At most two scale-vector batches and 4*max_step total absolute pixel units
    are planned. Each nonzero scale vector must also meet min_batch_length_px.
    The default budget is half the requested tolerance. A caller with independently verified
    calibration closure can use 85 percent. Native readback still uses the full original tolerance.
"""
    if type(max_step) is not int or not 1 <= max_step <= _MAX_STEP:
        _fail('LATTICE_STEP_BUDGET', 'max_step must be an integer from 1 to 32')
    if type(min_batch_length_px) not in (int, float) or not 0 <= min_batch_length_px <= math.sqrt(2)*max_step:
        _fail('LATTICE_STEP_BUDGET', 'Minimum nonzero scale-vector length exceeds its finite pixel budget')
    min_batch_length_px = float(min_batch_length_px)
    if not math.isfinite(min_batch_length_px):
        _fail('INVALID_LATTICE_INPUT', 'Minimum scale-vector length must be finite')
    if type(tolerance_mm) not in (int, float):
        _fail('INVALID_LATTICE_INPUT', 'tolerance_mm must be positive and finite')
    try:
        tolerance_mm = float(tolerance_mm)
    except (OverflowError, ValueError):
        _fail('INVALID_LATTICE_INPUT', 'tolerance_mm must be positive and finite')
    if not math.isfinite(tolerance_mm) or tolerance_mm <= 0:
        _fail('INVALID_LATTICE_INPUT', 'tolerance_mm must be positive and finite')
    fine, fine_lengths, inverse = _matrix(fine_columns, 'fine_columns')
    coarse, coarse_lengths, _ = _matrix(coarse_columns, 'coarse_columns')
    delta = _vector(delta_mm, 'delta_mm')
    ratios = [b/a for a, b in zip(fine_lengths, coarse_lengths)]
    if min(ratios) < 1 - 1e-9 or max(ratios)/min(ratios) > 1.05:
        _fail('INCONSISTENT_SCALE_DIRECTIONS', 'Coarse columns must share a common nondecreasing scale')
    for a, b, na, nb in zip(fine, coarse, fine_lengths, coarse_lengths):
        if ((a[0]*b[0] + a[1]*b[1])/(na*nb) <= 0
                or abs(a[0]*b[1] - a[1]*b[0])/(na*nb) > .02):
            _fail('INCONSISTENT_SCALE_DIRECTIONS', 'Corresponding measured axes must keep direction/rotation')

    target = _multiply(inverse, delta)
    coarse_in_fine = [_multiply(inverse, column) for column in coarse]
    if not all(math.isfinite(v) for v in target):
        _fail('LATTICE_STEP_BUDGET', 'Requested displacement exceeds the bounded search domain')
    choices = []
    for axis in (0, 1):
        candidates = []
        for m in range(-max_step, max_step + 1):
            for n in range(-max_step, max_step + 1):
                residual = coarse_in_fine[axis][axis]*m + n - target[axis]
                candidates.append((abs(residual), abs(m)+abs(n), max(abs(m), abs(n)), m, n))
        candidates.sort()
        choices.append(candidates[:_CANDIDATES])

    qfine = [[Fraction(v) for v in c] for c in fine]
    qcoarse = [[Fraction(v) for v in c] for c in coarse]
    qdelta = [Fraction(v) for v in delta]
    if type(prediction_budget_fraction) is not float or prediction_budget_fraction not in (.5, .85):
        _fail('INVALID_LATTICE_INPUT', 'Prediction budget must be half or 85 percent of the original tolerance')
    budget = Fraction(tolerance_mm) * Fraction(str(prediction_budget_fraction))
    bound = budget**2
    accepted = []
    best_error = None
    for x in choices[0]:
        for y in choices[1]:
            m, n = [x[3], y[3]], [x[4], y[4]]
            if any(any(v) and math.hypot(*v) < min_batch_length_px for v in (m, n)):
                continue
            predicted = [sum(qcoarse[i][axis]*m[i] + qfine[i][axis]*n[i]
                             for i in (0, 1)) for axis in (0, 1)]
            residual = [a-b for a, b in zip(predicted, qdelta)]
            error2 = sum(v*v for v in residual)
            best_error = error2 if best_error is None else min(best_error, error2)
            if error2 <= bound:
                travel = sum(abs(v) for v in m+n)
                batches = int(any(m)) + int(any(n))
                accepted.append(((travel, batches, error2, tuple(m), tuple(n)), m, n, predicted, residual, error2))
    if not accepted:
        raise LatticeError('LATTICE_TARGET_UNREACHABLE', 'No retained bounded candidate meets the prediction budget',
                           dict(best_predicted_error_mm=None if best_error is None else math.sqrt(float(best_error)),
                                search_complete=False, candidates_per_axis=len(choices[0]), max_step=max_step,
                                min_batch_length_px=min_batch_length_px))
    _, m, n, predicted, residual, error2 = min(accepted, key=lambda row: row[0])
    return dict(coarse_steps_px=m, fine_steps_px=n, predicted_delta_mm=[float(v) for v in predicted],
                requested_delta_mm=delta, residual_mm=[float(v) for v in residual],
                predicted_error_mm=math.sqrt(float(error2)), predicted_error_squared_mm2_exact=str(error2),
                error_budget_mm=float(budget), tolerance_mm=float(tolerance_mm),
                max_step=max_step, absolute_pixel_units=sum(abs(v) for v in m+n),
                min_batch_length_px=min_batch_length_px,
                scale_batches=int(any(m))+int(any(n)), candidates_per_axis=len(choices[0]),
                candidate_pairs_checked=len(choices[0])*len(choices[1]), search_complete=False,
                lattice_verified=True, native_owner_binding_verified=False,
                native_dispatch_authorized=False, execution_verified=False)
