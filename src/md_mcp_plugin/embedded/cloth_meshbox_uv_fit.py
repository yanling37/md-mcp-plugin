"""Offline per-piece UV registration for a single MeshBox PNG and thin OBJ."""

from collections import Counter
from dataclasses import dataclass
import hashlib
import math

from .cloth_meshbox_planner import MAX_PROJECTED_VERTICES, plan_vertex_box
from .curve_projection import decode_png
from .mesh import parse_obj


_SPLIT = (.26, .74)
_RADIUS = {'wire': 2.0, 'dark': 2.5, 'yellow': 2.75}
_MIN_SIGNAL = {'wire': 8, 'dark': 25, 'yellow': 55}
_PROFILE_ERROR = {'wire': .75, 'dark': 1.0, 'yellow': 1.25}
_MAX_WITNESSES = 90
_MAX_GRID_REFERENCES = 500_000


class MeshboxFitError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Piece:
    uv: dict
    wire: tuple
    outline: tuple


@dataclass(frozen=True)
class _Line:
    edge: tuple
    uv: tuple
    normal: tuple
    offset: float
    uncertainty: float
    tangent: tuple
    tangent_span: tuple
    centers: tuple
    pixels: frozenset


@dataclass(frozen=True)
class MeshboxFrameFit:
    png_sha256: str
    obj_sha256: str
    topology_fingerprint: str
    viewport_size_logical_px: tuple
    device_pixel_ratio: float
    projected_by_piece: dict
    diagnostics_by_piece: dict

    def plan_vertex(self, pattern_uuid, vertex_index, *, start_drag_distance, min_clearance_logical_px=0.0):
        """Use zero-based exported OBJ indices as planner labels."""
        return plan_vertex_box(
            self.projected_by_piece, pattern_uuid, vertex_index,
            viewport_size_logical_px=self.viewport_size_logical_px,
            device_pixel_ratio=self.device_pixel_ratio, start_drag_distance=start_drag_distance,
            min_clearance_logical_px=min_clearance_logical_px,
        )


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _at(a, b, fraction):
    return tuple(x + fraction * (y - x) for x, y in zip(a, b))


def _pair(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(type(x) not in (float, int) or not -1e7 <= x <= 1e7 for x in value)):
        raise MeshboxFitError('INVALID_COORDINATES', 'Expected a bounded finite coordinate pair')
    return tuple(map(float, value))


def _inventory(mesh, group_by_uuid, excluded_groups):
    if (not isinstance(group_by_uuid, dict) or not group_by_uuid or not 1 <= len(group_by_uuid) <= 64
            or any(not isinstance(s, str) or not s.strip() or len(s) > 512
                   for s in (*group_by_uuid, *group_by_uuid.values(), *excluded_groups))):
        raise MeshboxFitError('INVALID_OWNER_MAPPING', 'Supply exact SDK UUIDs and OBJ groups')
    labels = set(group_by_uuid.values())
    excluded = set(excluded_groups)
    classified = labels | excluded
    if len(labels) != len(group_by_uuid) or labels & excluded:
        raise MeshboxFitError('INVALID_OWNER_MAPPING', 'OBJ groups must have one explicit classification')
    uv_by_group = {label: {} for label in labels}
    edges_by_group = {label: Counter() for label in labels}
    owner_by_vertex = {}
    seen = set()
    for face, corners, attrs in zip(mesh['faces'], mesh['face_uv_indices'], mesh['face_labels']):
        groups = attrs['groups']
        if len(groups) != 1 or groups[0] not in classified:
            raise MeshboxFitError('UNCLASSIFIED_OBJ_GROUP', f'Unclassified or ambiguous OBJ face groups {groups!r}')
        label = groups[0]
        seen.add(label)
        for vertex in face:
            if owner_by_vertex.setdefault(vertex, label) != label:
                raise MeshboxFitError('WELDED_OWNERS', 'A vertex is shared by different OBJ groups')
        if label in excluded:
            continue
        if len(face) != 3 or None in corners:
            raise MeshboxFitError('MISSING_TRIANGLE_UV', 'Selectable cloth must have triangulated corner UVs')
        uv = uv_by_group[label]
        for vertex, uv_index in zip(face, corners):
            coordinate = _pair(mesh['uv'][uv_index])
            if uv.setdefault(vertex, coordinate) != coordinate:
                raise MeshboxFitError('AMBIGUOUS_VERTEX_UV', f'{label} has multiple UV positions for OBJ vertex {vertex}')
        edges_by_group[label].update(tuple(sorted((a, b))) for a, b in zip(face, face[1:] + face[:1]))
    if seen != classified or len(owner_by_vertex) != mesh['vertex_count']:
        raise MeshboxFitError('INCOMPLETE_OBJ_INVENTORY', 'Every group and vertex must be accounted for by faces')
    if sum(map(len, uv_by_group.values())) > MAX_PROJECTED_VERTICES:
        raise MeshboxFitError('VERTEX_BUDGET', 'Selectable inventory exceeds the planner budget')
    pieces = {}
    for uuid, label in group_by_uuid.items():
        edges = edges_by_group[label]
        outline = tuple(sorted(edge for edge, count in edges.items() if count == 1))
        if any(count > 2 for count in edges.values()) or not outline:
            raise MeshboxFitError('AMBIGUOUS_PIECE_EDGES', f'{label} has unsupported boundary topology')
        degree = Counter(i for edge in outline for i in edge)
        if set(degree.values()) != {2}:
            raise MeshboxFitError('AMBIGUOUS_PIECE_EDGES', f'{label} has an open or nonmanifold boundary')
        adjacent = {i: [] for i in uv_by_group[label]}
        for a, b in edges:
            adjacent[a].append(b)
            adjacent[b].append(a)
        pending = [next(iter(adjacent))]
        reached = set(pending)
        while pending:
            for neighbor in adjacent[pending.pop()]:
                if neighbor not in reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
        if len(reached) != len(adjacent):
            raise MeshboxFitError('DISCONNECTED_PIECE_UV', f'{label} does not have a single connected UV mesh')
        pieces[uuid] = _Piece(uv_by_group[label], tuple(sorted(e for e, count in edges.items() if count == 2)), outline)
    return pieces


def _inverse_gram(rows):
    """Invert a small Gram matrix with partial pivoting on normalized columns."""
    columns = tuple(zip(*rows))
    n = len(columns)
    aug = [[_dot(col, other) / len(rows) for other in columns] + [float(i == j) for j in range(n)]
           for i, col in enumerate(columns)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(aug[j][i]))
        aug[i], aug[pivot] = aug[pivot], aug[i]
        divisor = aug[i][i]
        if abs(divisor) < 1e-7:
            raise MeshboxFitError('UNDERCONSTRAINED_AFFINE', 'Witnesses do not constrain a stable affine map')
        aug[i] = [x / divisor for x in aug[i]]
        for j, row in enumerate(aug):
            if j != i:
                aug[j] = [x - row[i] * y for x, y in zip(row, aug[i])]
    return tuple(tuple(x / len(rows) for x in row[n:]) for row in aug)


@dataclass(frozen=True)
class _UVBasis:
    center: tuple
    halfspan: tuple

    @classmethod
    def from_uv(cls, uv):
        u, v = zip(*uv.values())
        limits = [(min(axis), max(axis)) for axis in (u, v)]
        halfspan = tuple((hi - lo) / 2 for lo, hi in limits)
        if min(halfspan) < 1e-9:
            raise MeshboxFitError('DEGENERATE_UV', 'Cloth UVs do not span two dimensions')
        return cls(tuple((lo + hi) / 2 for lo, hi in limits), halfspan)

    def at(self, uv):
        return (*((x - c) / s for x, c, s in zip(uv, self.center, self.halfspan)), 1.0)


@dataclass(frozen=True)
class _Bootstrap:
    basis: _UVBasis
    coefficients: tuple
    influence: tuple
    landmark_error: float
    model_error: tuple

    def at(self, uv):
        row = self.basis.at(uv)
        return tuple(_dot(axis, row) for axis in self.coefficients)

    def support(self, uv, direction):
        row = self.basis.at(uv)
        weight = sum(abs(_dot(row, col)) for col in self.influence)
        normal = tuple(map(abs, direction))
        error = weight * self.landmark_error * sum(normal) + (1 + weight) * _dot(normal, self.model_error)
        return error, weight


def _bootstrap(piece, basis, points, size, landmark_error, model_error):
    boundary = {i for edge in piece.outline for i in edge}
    if (not isinstance(points, dict) or not 3 <= len(points) <= 24 or not points.keys() <= boundary
            or any(type(i) is not int for i in points)):
        raise MeshboxFitError('INVALID_SEED', 'Use 3..24 distinct vertices on the stated piece')
    xy = [_pair(point) for point in points.values()]
    if any(not (0 < x < size[0] and 0 < y < size[1]) for x, y in xy):
        raise MeshboxFitError('INVALID_SEED', 'Current-frame seed landmarks must be inside the raw PNG')
    rows = [basis.at(piece.uv[index]) for index in points]
    inverse = _inverse_gram(rows)
    influence = tuple(tuple(_dot(row, coefficient) for row in rows) for coefficient in inverse)
    coefficients = tuple(tuple(_dot(row, axis) for row in influence) for axis in zip(*xy))
    bootstrap = _Bootstrap(basis, coefficients, tuple(zip(*influence)), landmark_error, model_error)
    if any(max(map(abs, _sub(bootstrap.at(piece.uv[index]), point))) > 2 * landmark_error
           for index, point in zip(points, xy)):
        raise MeshboxFitError('INCONSISTENT_SEED', 'Current-frame landmarks disagree with their UV affine map')
    return bootstrap, xy


class _EdgeGrid:
    def __init__(self, pieces, projected):
        self.cells = {}
        references = 0
        for owner, piece in pieces.items():
            for kind, edges in (('wire', piece.wire), ('outline', piece.outline)):
                for edge in edges:
                    a, b = (projected[owner][v] for v in edge)
                    for cell in self._cells(a, b, 0):
                        references += 1
                        if references > _MAX_GRID_REFERENCES:
                            raise MeshboxFitError('SEED_PROJECTION_BUDGET', 'Seed geometry exceeds the image search budget')
                        self.cells.setdefault((kind, cell), []).append((owner, edge, a, b))

    @staticmethod
    def _cells(a, b, radius):
        (x0, x1), (y0, y1) = (sorted(pair) for pair in zip(a, b))
        return ((i, j) for i in range(math.floor((x0 - radius) / 12), math.floor((x1 + radius) / 12) + 1)
                for j in range(math.floor((y0 - radius) / 12), math.floor((y1 + radius) / 12) + 1))

    def clear(self, kind, owner, edge, point, radius):
        for cell in self._cells(point, point, radius):
            for other_owner, other_edge, a, b in self.cells.get((kind, cell), ()):
                if (other_owner, other_edge) == (owner, edge):
                    continue
                direction = _sub(b, a)
                squared = _dot(direction, direction)
                fraction = min(1, max(0, _dot(_sub(point, a), direction) / squared)) if squared else 0
                if math.dist(point, _at(a, b, fraction)) <= radius:
                    return False
        return True


def _signal(image, i, j, style):
    channels = image['channels']
    start = (j * image['width'] + i) * channels
    r, g, b = image['pixels'][start:start + 3]
    if channels == 4 and image['pixels'][start + 3] != 255:
        return 0
    if style == 'wire':
        return max(0, g - max(r, b))
    if style == 'yellow':
        return max(0, min(r, g) - b)
    return max(0, 150 - max(r, g, b))


def _profile(image, point, normal, style):
    radius = _RADIUS[style]
    if not (radius + 1 < point[0] < image['width'] - radius - 1
            and radius + 1 < point[1] < image['height'] - radius - 1):
        return None
    pixels, scores = set(), []
    steps = round(radius * 4)
    for step in range(-steps, steps + 1):
        x, y = (p + step * d / 4 - .5 for p, d in zip(point, normal))
        i, j = math.floor(x), math.floor(y)
        fx, fy = x - i, y - j
        points = ((i, j), (i + 1, j), (i, j + 1), (i + 1, j + 1))
        weights = ((1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy)
        scores.append(_dot([_signal(image, *p, style) for p in points], weights))
        pixels.update(points)
    peak = max(scores)
    if peak < _MIN_SIGNAL[style]:
        return None
    level = peak * .55
    indices = [i for i, score in enumerate(scores) if score >= level]
    left, right = indices[0], indices[-1]
    if left == 0 or right == len(scores) - 1 or len(indices) != right - left + 1:
        return None
    lo = left - 1 + (level - scores[left - 1]) / (scores[left] - scores[left - 1])
    hi = right + (scores[right] - level) / (scores[right] - scores[right + 1])
    shift = (lo + hi) / 8 - radius
    if abs(shift) > radius - .65 or (hi - lo) / 4 > (3.6 if style == 'yellow' else 2.6):
        return None
    center = tuple(p + shift * d for p, d in zip(point, normal))
    return center, pixels, shift


def _landmarks_visible(image, points, style, error):
    for x, y in points:
        columns = range(max(0, math.ceil(x - .5 - error)), min(image['width'], math.floor(x - .5 + error) + 1))
        rows = range(max(0, math.ceil(y - .5 - error)), min(image['height'], math.floor(y - .5 + error) + 1))
        if not any(_signal(image, i, j, style) >= _MIN_SIGNAL[style] for i in columns for j in rows):
            raise MeshboxFitError('LANDMARK_NOT_VISIBLE', f'No {style} contour near landmark ({x}, {y})')


def _observe(image, grid, owner, piece, projected, style, edge, seed_xy, bootstrap):
    a, b = (projected[i] for i in edge)
    length = math.dist(a, b)
    if length < 6.5:
        return None
    tangent = tuple(x / length for x in _sub(b, a))
    normal = (-tangent[1], tangent[0])
    uv = _at(*(piece.uv[i] for i in edge), .5)
    # True midpoint must lie inside the two sampled tangent coordinates under
    # the independent landmark error budget. This check precedes the wire fit.
    if bootstrap.support(uv, tangent)[0] >= (_SPLIT[1] - .5) * length:
        return None
    samples = [_at(a, b, t) for t in _SPLIT]
    kind = 'wire' if style == 'wire' else 'outline'
    for point in samples:
        if (any(math.dist(point, xy) < _RADIUS[style] + 2 for xy in seed_xy)
                or not grid.clear(kind, owner, edge, point, _RADIUS[style])):
            return None
    observations = [_profile(image, point, normal, style) for point in samples]
    if any(observation is None for observation in observations):
        return None
    (p0, pixels0, d0), (p1, pixels1, d1) = observations
    if abs(d1 - d0) > 1.25:
        return None
    chord = _sub(p1, p0)
    chord_length = math.hypot(*chord)
    measured_normal = (-chord[1] / chord_length, chord[0] / chord_length)
    return _Line(edge, uv, measured_normal, _dot(measured_normal, p0), _PROFILE_ERROR[style],
                 tangent, tuple(_dot(tangent, point) for point in samples), (p0, p1), frozenset(pixels0 | pixels1))


def _spread(lines, basis):
    """Round-robin over UV regions so dense interior tessellation cannot dominate."""
    cells = {}
    for line in lines:
        cell = tuple(min(5, max(0, int((u + 1) * 3))) for u in basis.at(line.uv)[:2])
        cells.setdefault(cell, []).append(line)
    return [rows[i] for i in range(max(map(len, cells.values()), default=0)) for rows in cells.values() if i < len(rows)]


def _split_witnesses(wire, outline, basis):
    fit, held_wire, held_outline = [], [], []
    outline_pixels, fit_pixels, validation_pixels = set(), set(), set()
    # Reserve contour observations first. Wire and contour image supports are disjoint.
    for line in _spread(outline, basis):
        held_outline.append(line)
        outline_pixels.update(line.pixels)
        if len(held_outline) == _MAX_WITNESSES:
            break
    for line in _spread(wire, basis):
        if not line.pixels.isdisjoint(outline_pixels):
            continue
        if len(fit) <= len(held_wire) and line.pixels.isdisjoint(validation_pixels):
            fit.append(line)
            fit_pixels.update(line.pixels)
        elif len(held_wire) < _MAX_WITNESSES and line.pixels.isdisjoint(fit_pixels):
            held_wire.append(line)
            validation_pixels.update(line.pixels)
        if len(fit) == _MAX_WITNESSES:
            break
    return fit, held_wire, held_outline


def _row(basis, uv, normal):
    u, v, offset = basis.at(uv)
    x, y = normal
    return x * u, x * v, x * offset, y * u, y * v, y * offset


@dataclass(frozen=True)
class _Affine:
    basis: _UVBasis
    coefficients: tuple
    column_influence: tuple
    witness_errors: tuple
    center: tuple
    model_error: tuple

    @classmethod
    def fit(cls, lines, basis, size, model_error):
        rows = [_row(basis, line.uv, line.normal) for line in lines]
        inverse = _inverse_gram(rows)
        center = tuple(x / 2 for x in size)
        measured = [line.offset - _dot(line.normal, center) for line in lines]
        columns = tuple(tuple(_dot(row, coefficient) for row in rows) for coefficient in inverse)
        coefficients = tuple(_dot(column, measured) for column in columns)
        errors = tuple(line.uncertainty + _dot(tuple(map(abs, line.normal)), model_error) for line in lines)
        return cls(basis, coefficients, tuple(zip(*columns)), errors, center, model_error)

    def predict(self, uv, normal):
        row = _row(self.basis, uv, normal)
        weights = [abs(_dot(row, influence)) for influence in self.column_influence]
        uncertainty = _dot(weights, self.witness_errors) + _dot(tuple(map(abs, normal)), self.model_error)
        return _dot(row, self.coefficients) + _dot(normal, self.center), uncertainty, sum(weights)


def _coverage(lines, basis):
    return tuple((max(axis) - min(axis)) / 2 for axis in zip(*(basis.at(line.uv)[:2] for line in lines)))


def _diagnose(lines, affine, role):
    residuals, report = [], []
    for line in lines:
        location, uncertainty, _ = affine.predict(line.uv, line.normal)
        tangent, _, _ = affine.predict(line.uv, line.tangent)
        residual = abs(location - line.offset)
        if not line.tangent_span[0] < tangent < line.tangent_span[1]:
            raise MeshboxFitError('LINE_SUPPORT_TOO_SHORT', f'{role} edge {line.edge} projects outside its sampled profiles')
        if (residual > line.uncertainty + uncertainty
                or residual > line.uncertainty + _dot(tuple(map(abs, line.normal)), affine.model_error)):
            raise MeshboxFitError('LINE_RESIDUAL', f'{role} edge {line.edge} disagrees by {residual:.3f} px')
        residuals.append(residual)
        report.append({'edge': line.edge, 'observed_centers_px': line.centers, 'normal': line.normal,
                       'measurement_bound_px': line.uncertainty, 'residual_px': residual})
    return {'count': len(lines), 'uv_span_fraction': _coverage(lines, affine.basis),
            'max_normal_residual_px': max(residuals),
            'rms_normal_residual_px': math.sqrt(sum(r * r for r in residuals) / len(residuals)), 'witnesses': report}


def _combined_bound(center, wire_error, landmark_center, landmark_error):
    # The true coordinate must lie in both intervals. Keep the wire-fit center
    # and enclose the intersection in the symmetric interval the planner uses.
    low = max(center - wire_error, landmark_center - landmark_error)
    high = min(center + wire_error, landmark_center + landmark_error)
    if not low <= center <= high:
        raise MeshboxFitError('LANDMARK_DISAGREEMENT', 'Wire-fit center is outside the independent landmark interval')
    return max(center - low, high - center)


def fit_meshbox_frame(png_bytes, obj_bytes, *, png_sha256, obj_sha256, group_by_uuid, excluded_groups,
                      seeds_by_uuid, viewport_size_logical_px, device_pixel_ratio,
                      landmark_error_px=1.0, model_error_px=(.25, .25)):
    """Fit separate affines from the supplied current PNG and verified OBJ bytes.

    Each seed is {'points': {global_obj_index: (x,y)}, 'outline': 'dark'|'yellow'}.
    Landmarks locate search strips, bound their tangent coordinate, and constrain
    the final error intervals. Wire observations determine the reported centers.
    All coordinates are physical pixels relative to the PNG's upper-left edge.
    The caller binds both hashes to one scene and supplies all selectable groups.
    Bounds assume the landmark and raster errors and the per-axis model deviation
    (including UV quantization) stay within their stated budgets.
    """
    if (hashlib.sha256(png_bytes).hexdigest() != png_sha256
            or hashlib.sha256(obj_bytes).hexdigest() != obj_sha256):
        raise MeshboxFitError('ASSET_HASH_MISMATCH', 'PNG or OBJ bytes differ from the evidence receipt')
    image = decode_png(png_bytes)
    size = image['width'], image['height']
    logical = tuple(viewport_size_logical_px)
    dpr = device_pixel_ratio
    if (len(logical) != 2 or any(type(x) is not int or not 2 <= x <= 16384 for x in logical)
            or type(dpr) not in (int, float) or not .5 <= dpr <= 8
            or any(abs(p - q * dpr) > .50001 for p, q in zip(size, logical))):
        raise MeshboxFitError('FRAME_SCALE_MISMATCH', 'PNG extent, logical viewport and DPR must agree')
    model_error = _pair(model_error_px)
    if any(not 0 <= value <= 2 for value in model_error):
        raise MeshboxFitError('INVALID_MODEL_ERROR', 'Per-axis model error must be 0..2 physical pixels')
    if type(landmark_error_px) not in (int, float) or not .5 <= landmark_error_px <= 2:
        raise MeshboxFitError('INVALID_LANDMARK_ERROR', 'Landmark error must be .5..2 physical pixels per axis')
    mesh = parse_obj(obj_bytes)
    pieces = _inventory(mesh, group_by_uuid, tuple(excluded_groups))
    if not isinstance(seeds_by_uuid, dict) or seeds_by_uuid.keys() != pieces.keys():
        raise MeshboxFitError('MISSING_PIECE_SEED', 'Supply current-frame landmarks for each selectable owner')
    bases, bootstraps, seeds, coarse = {}, {}, {}, {}
    for owner, piece in pieces.items():
        spec = seeds_by_uuid[owner]
        if not isinstance(spec, dict) or set(spec) != {'outline', 'points'} or spec['outline'] not in ('dark', 'yellow'):
            raise MeshboxFitError('INVALID_SEED', 'Each piece requires an outline style and current PNG landmarks')
        bases[owner] = _UVBasis.from_uv(piece.uv)
        bootstraps[owner], seeds[owner] = _bootstrap(piece, bases[owner], spec['points'], size, landmark_error_px, model_error)
        coarse[owner] = {i: bootstraps[owner].at(uv) for i, uv in piece.uv.items()}
        if any(not (1 < x < size[0] - 1 and 1 < y < size[1] - 1) for x, y in coarse[owner].values()):
            raise MeshboxFitError('PIECE_CLIPPED', f'{owner} has a seeded vertex outside the visible frame')
        _landmarks_visible(image, seeds[owner], spec['outline'], landmark_error_px)
    grid = _EdgeGrid(pieces, coarse)
    all_rows, diagnostics = {}, {}
    for owner, piece in pieces.items():
        basis = bases[owner]
        style = seeds_by_uuid[owner]['outline']
        wire = [_observe(image, grid, owner, piece, coarse[owner], 'wire', edge, seeds[owner], bootstraps[owner])
                for edge in piece.wire]
        outline = [_observe(image, grid, owner, piece, coarse[owner], style, edge, seeds[owner], bootstraps[owner])
                   for edge in piece.outline]
        fit, held_wire, held_outline = _split_witnesses([line for line in wire if line], [line for line in outline if line], basis)
        counts = len(fit), len(held_wire), len(held_outline)
        if any(len(lines) < count or min(_coverage(lines, basis)) < .48
               for lines, count in zip((fit, held_wire, held_outline), (12, 8, 8))):
            raise MeshboxFitError('INSUFFICIENT_WITNESSES', f'{owner} has insufficient spread or counts (fit/wire/outline {counts})')
        affine = _Affine.fit(fit, basis, size, model_error)
        try:
            checks = {name: _diagnose(lines, affine, name) for name, lines in
                      (('fit_wire', fit), ('held_out_wire', held_wire), ('held_out_outline', held_outline))}
        except MeshboxFitError as exc:
            raise MeshboxFitError(exc.code, f'{owner}: {exc}') from exc
        rows, leverage, wire_leverage = [], 0.0, 0.0
        bounds = [math.inf, math.inf, -math.inf, -math.inf]
        for index, uv in sorted(piece.uv.items()):
            x, dx, lx = affine.predict(uv, (1, 0))
            y, dy, ly = affine.predict(uv, (0, 1))
            landmark = bootstraps[owner]
            bx, blx = landmark.support(uv, (1, 0))
            by, bly = landmark.support(uv, (0, 1))
            prior_x, prior_y = coarse[owner][index]
            dx = _combined_bound(x, dx, prior_x, bx)
            dy = _combined_bound(y, dy, prior_y, by)
            bounds = [min(bounds[0], x - dx), min(bounds[1], y - dy), max(bounds[2], x + dx), max(bounds[3], y + dy)]
            leverage = max(leverage, min(lx, blx), min(ly, bly))
            wire_leverage = max(wire_leverage, lx, ly)
            rows.append({'vertex_index': index, 'pixel_xy': [x, y], 'uncertainty_px': [dx, dy]})
        if leverage > 4:
            raise MeshboxFitError('UNSUPPORTED_EXTRAPOLATION', f'{owner} extends beyond reliable witness support')
        if not (0 < bounds[0] < bounds[2] < size[0] and 0 < bounds[1] < bounds[3] < size[1]):
            raise MeshboxFitError('PIECE_CLIPPED', f'{owner} has a projected vertex interval crossing the frame edge')
        origin = tuple(affine.predict((0, 0), n)[0] for n in ((1, 0), (0, 1)))
        uv_map = [tuple(affine.coefficients[start + i] / basis.halfspan[i] for i in range(2)) + (axis_offset,)
                  for start, axis_offset in zip((0, 3), origin)]
        diagnostics[owner] = dict(checks, vertex_count=len(rows), group_name=group_by_uuid[owner], landmark_error_px=landmark_error_px,
                                  uv_to_physical_px=uv_map, model_error_px=model_error,
                                  max_tighter_influence_l1=leverage, max_wire_influence_l1=wire_leverage,
                                  bounds_physical_px=bounds,
                                  max_vertex_uncertainty_px=[max(row['uncertainty_px'][axis] for row in rows) for axis in (0, 1)])
        all_rows[owner] = rows
    return MeshboxFrameFit(png_sha256, obj_sha256, mesh['topology_fingerprint'], logical, dpr, all_rows, diagnostics)
