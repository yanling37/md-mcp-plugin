"""Screen-space rectangle feasibility for independently projected cloth vertices."""

from dataclasses import dataclass
import math


MAX_PROJECTED_VERTICES = 50000
MAX_BOX_CANDIDATES = 16384
MAX_EXCLUSION_CHECKS = 2000000


@dataclass(frozen=True)
class _Bounds:
    left: float
    top: float
    right: float
    bottom: float

    def separation(self, other):
        return max(self.left - other.right, other.left - self.right,
                   self.top - other.bottom, other.top - self.bottom)

    def containment(self, inner):
        return min(inner.left - self.left, inner.top - self.top,
                   self.right - inner.right, self.bottom - inner.bottom)

    def as_list(self):
        return [self.left, self.top, self.right, self.bottom]


def _number(value, name, *, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be a finite number in [{minimum}, {maximum}]')
    return float(value)


def _pair(value, name):
    if type(value) not in (list, tuple) or len(value) != 2:
        raise ValueError(f'{name} must contain two coordinates')
    return value


def _viewport(size):
    width, height = _pair(size, 'Logical viewport size')
    if any(type(value) is not int or not 2 <= value <= 16384 for value in (width, height)):
        raise ValueError('Logical viewport dimensions must be integers in [2, 16384]')
    return width, height


def _identity(pattern_uuid, vertex_index):
    if type(pattern_uuid) is not str or not pattern_uuid.strip() or len(pattern_uuid) > 512:
        raise ValueError('Each projected piece must have a nonempty bounded identity')
    if type(vertex_index) is not int or not 0 <= vertex_index < (1 << 31):
        raise ValueError('Vertex indices must be nonnegative bounded integers')
    return pattern_uuid, vertex_index


def _vertex_bounds(projected_by_piece, dpr):
    if type(projected_by_piece) is not dict or not projected_by_piece:
        raise ValueError('Supply the independently projected vertices of each selectable piece')
    vertices = {}
    for pattern_uuid, rows in projected_by_piece.items():
        if type(rows) not in (list, tuple) or not rows:
            raise ValueError('Each projected piece must supply a nonempty vertex list')
        if len(vertices) + len(rows) > MAX_PROJECTED_VERTICES:
            raise ValueError('Projected inventory exceeds the vertex budget')
        for row in rows:
            if type(row) is not dict or set(row) != {'vertex_index', 'pixel_xy', 'uncertainty_px'}:
                raise ValueError('Each row requires a vertex index, pixel_xy and uncertainty_px')
            key = _identity(pattern_uuid, row['vertex_index'])
            if key in vertices:
                raise ValueError('A projected vertex identity appears more than once')
            x, y = (_number(value, 'Physical pixel coordinate', minimum=-1e7, maximum=1e7)
                    for value in _pair(row['pixel_xy'], 'pixel_xy'))
            dx, dy = (_number(value, 'Per-axis uncertainty', minimum=0, maximum=1e7)
                      for value in _pair(row['uncertainty_px'], 'uncertainty_px'))
            vertices[key] = _Bounds((x - dx) / dpr, (y - dy) / dpr, (x + dx) / dpr, (y + dy) / dpr)
    return vertices


def _candidate_range(low, high, size, threshold, clearance):
    # Strict margin on both sides, including when an edge is exactly integral.
    low_edge = math.ceil(low - clearance) - 1
    high_edge = math.floor(high + clearance) + 1
    span = max(threshold + 1, high_edge - low_edge)
    return range(max(0, high_edge - span), min(low_edge, size - 1 - span) + 1), span


def plan_vertex_box(projected_by_piece, target_pattern_uuid, target_vertex_index, *, viewport_size_logical_px,
                    device_pixel_ratio, start_drag_distance, min_clearance_logical_px=0.0):
    """Find an integer Qt drag rectangle with strictly separated uncertainty boxes.

    The input uses physical coordinates measured from the viewport's upper-left
    edge. Convert zero-based image sample centers by adding 0.5 before calling.
    Per-axis uncertainty includes projection, registration, and raster error.
    The caller supplies all selectable vertices, including offscreen vertices
    whose uncertainty may cross the box. No visibility assumption is applied.
    """
    width, height = _viewport(viewport_size_logical_px)
    dpr = _number(device_pixel_ratio, 'Device pixel ratio', minimum=.5, maximum=8)
    clearance = _number(min_clearance_logical_px, 'Minimum logical clearance', minimum=0, maximum=16384)
    if type(start_drag_distance) is not int or not 0 <= start_drag_distance <= 65535:
        raise ValueError('Use the actual nonnegative Qt startDragDistance in logical pixels')
    target_id = _identity(target_pattern_uuid, target_vertex_index)
    vertices = _vertex_bounds(projected_by_piece, dpr)
    if target_id not in vertices:
        raise ValueError('The target must resolve uniquely in the projected inventory')
    target = vertices.pop(target_id)
    xs, box_width = _candidate_range(target.left, target.right, width, start_drag_distance, clearance)
    ys, box_height = _candidate_range(target.top, target.bottom, height, start_drag_distance, clearance)
    report = {'schema': 'md-meshbox-projected-rectangle/1', 'candidate_available': False,
              'dispatch_authorized': False, 'native_selection_verified': False,
              'projection_and_inventory_independently_verified': False,
              'target_pattern_uuid': target_pattern_uuid, 'target_vertex_index': target_vertex_index,
              'target_uncertainty_bounds_logical_px': target.as_list(),
              'other_projected_vertex_count': len(vertices), 'device_pixel_ratio': dpr,
              'viewport_size_logical_px': [width, height], 'start_drag_distance': start_drag_distance,
              'minimum_clearance_logical_px': clearance}
    if not xs or not ys:
        return dict(report, reason='TARGET_BOX_OUTSIDE_VIEWPORT')
    if len(xs) * len(ys) > MAX_BOX_CANDIDATES:
        return dict(report, reason='CANDIDATE_BUDGET_EXCEEDED')

    envelope = _Bounds(xs[0], ys[0], xs[-1] + box_width, ys[-1] + box_height)
    max_inside = min((box_width - (target.right - target.left)) / 2,
                     (box_height - (target.bottom - target.top)) / 2)
    competitors = [bounds for bounds in vertices.values() if envelope.separation(bounds) <= max_inside]
    if len(xs) * len(ys) * len(competitors) > MAX_EXCLUSION_CHECKS:
        return dict(report, reason='EXCLUSION_BUDGET_EXCEEDED')

    best = None
    for x in xs:
        for y in ys:
            box = _Bounds(x, y, x + box_width, y + box_height)
            inside = box.containment(target)
            if inside <= clearance:
                continue
            outside = min((box.separation(bounds) for bounds in competitors), default=math.inf)
            score = min(inside, outside)
            if score <= clearance:
                continue
            offset_squared = ((box.left + box.right - target.left - target.right) ** 2
                              + (box.top + box.bottom - target.top - target.bottom) ** 2)
            rank = (score, -offset_squared, -x, -y)
            if best is None or rank > best[0]:
                best = rank, box, inside
    if best is None:
        return dict(report, reason='OTHER_PROJECTED_VERTEX_NOT_EXCLUDED')

    _, box, inside = best
    nearest = min(((box.separation(bounds), identity) for identity, bounds in vertices.items()), default=None)
    other = None if nearest is None else {'pattern_uuid': nearest[1][0], 'vertex_index': nearest[1][1]}
    return dict(report, candidate_available=True, reason='PROJECTED_UNCERTAINTY_BOXES_SEPARATED',
                box_start_logical_px=[box.left, box.top], box_end_logical_px=[box.right, box.bottom],
                target_containment_clearance_logical_px=inside,
                other_exclusion_clearance_logical_px=None if nearest is None else nearest[0],
                closest_other_projected_vertex=other)
