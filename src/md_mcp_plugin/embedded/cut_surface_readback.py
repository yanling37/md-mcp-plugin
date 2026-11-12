"""Bounded exported-surface Cut readback. No SDK, file IO or native owner claim.

Positive results certify the stated finite samples, area/connectivity and exact
non-target OBJ groups. They do not certify continuous Hausdorff distance, target
UV/vertex-normal fields, solver Rest, or a group-label to native-UUID binding.
"""
import heapq
import math

from . import mesh
from .cut_interface_connectivity import verify_cut_interface


class SurfaceReadbackError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _reject(code, message):
    raise SurfaceReadbackError(code, message)


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _add(a, b, factor=1.):
    return tuple(x+factor*y for x, y in zip(a, b))


def _closest(p, a, b, c):
    """Float64 closest point on one nondegenerate triangle (all seven regions)."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(p, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0 and d2 <= 0:
        return a
    bp = _sub(p, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1*d4-d3*d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return _add(a, ab, d1/(d1-d3))
    cp = _sub(p, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5*d2-d1*d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return _add(a, ac, d2/(d2-d6))
    va = d3*d6-d5*d4
    if va <= 0 and d4-d3 >= 0 and d5-d6 >= 0:
        return _add(b, _sub(c, b), (d4-d3)/(d4-d3+d5-d6))
    denominator = va+vb+vc
    return _add(_add(a, ab, vb/denominator), ac, vc/denominator)


def _box_distance2(point, low, high):
    return sum(max(lo-v, 0., v-hi)**2 for v, lo, hi in zip(point, low, high))


class _Budget:
    def __init__(self, triangle_tests):
        self.limit, self.tests, self.nodes = triangle_tests, 0, 0
        self.exhausted = False

    def visit(self, triangle=False):
        if (triangle and self.tests >= self.limit) or (not triangle and self.nodes >= self.limit*8):
            self.exhausted = True
            _reject('SURFACE_WORK_BUDGET', 'Nearest-triangle work exceeded its shared hard budget')
        self.tests += int(triangle)
        self.nodes += int(not triangle)


class _BVH:
    def __init__(self, triangles, budget):
        self.triangles, self.budget, self.nodes = triangles, budget, []
        self._build(list(range(len(triangles))))

    def _build(self, indices):
        low = tuple(min(self.triangles[i]['low'][k] for i in indices) for k in range(3))
        high = tuple(max(self.triangles[i]['high'][k] for i in indices) for k in range(3))
        node = len(self.nodes)
        self.nodes.append(None)
        if len(indices) <= 8:
            self.nodes[node] = (low, high, None, None, indices)
        else:
            axis = max(range(3), key=lambda k: high[k]-low[k])
            indices.sort(key=lambda i: self.triangles[i]['center'][axis])
            middle = len(indices)//2
            left, right = self._build(indices[:middle]), self._build(indices[middle:])
            self.nodes[node] = (low, high, left, right, None)
        return node

    def nearest(self, point, normal=None):
        low, high = self.nodes[0][:2]
        pending = [(_box_distance2(point, low, high), 0)]
        best, chosen, closest, alignment = math.inf, None, None, -math.inf
        while pending:
            bound, index = heapq.heappop(pending)
            if bound > best+1e-24:
                continue
            self.budget.visit()
            low, high, left, right, indices = self.nodes[index]
            if indices is None:
                for child in (left, right):
                    lo, hi = self.nodes[child][:2]
                    heapq.heappush(pending, (_box_distance2(point, lo, hi), child))
                continue
            for i in indices:
                self.budget.visit(True)
                triangle = self.triangles[i]
                q = _closest(point, *triangle['points'])
                distance2 = _dot(_sub(point, q), _sub(point, q))
                dot = _dot(normal, triangle['normal']) if normal is not None else 1.
                if distance2 < best-1e-24 or (abs(distance2-best) <= 1e-24 and dot > alignment):
                    best, chosen, closest, alignment = distance2, triangle, q, dot
        return best, chosen, closest, alignment


def _groups(value, label):
    if (type(value) not in (tuple, list) or not 1 <= len(value) <= 32
            or any(type(v) is not str or not v or len(v) > 256 for v in value)
            or len(set(value)) != len(value)):
        _reject('INVALID_TARGET_GROUPS', label+' must contain unique exact OBJ group labels')
    return set(value)


def _snapshot(value):
    # Check bounded containers before canonical hashing copies/serializes them.
    if type(value) is not dict:
        _reject('INVALID_MESH_SNAPSHOT', 'A mesh snapshot object is required')
    for key in ('positions_mm', 'uv', 'normals', 'faces', 'face_labels', 'face_uv_indices', 'face_normal_indices'):
        if type(value.get(key)) is not list or len(value[key]) > mesh.MAX_ELEMENTS:
            _reject('INVALID_MESH_SNAPSHOT', 'Mesh arrays exceed the exported snapshot element cap')
    if any(type(face) is not list or len(face) > 256 for face in value['faces']) or sum(map(len, value['faces'])) > mesh.MAX_FACE_CORNERS:
        _reject('INVALID_MESH_SNAPSHOT', 'Face-corner inventory exceeds the exported snapshot cap')
    try:
        mesh._validate_snapshot(value)
    except (ValueError, ReferenceError, TypeError) as exc:
        _reject('INVALID_MESH_SNAPSHOT', str(exc))
    if (value.get('scope') != 'scene_garment_OBJ_export' or value.get('space') != 'world'
            or value.get('unit') != 'mm' or value.get('axis_order') != ['X', 'Y', 'Z']):
        _reject('UNSUPPORTED_SURFACE_SCOPE', 'Whole-scene world-mm XYZ exported snapshots are required')
    try:
        coordinates = value['positions_mm']
        faces, labels = value['faces'], value['face_labels']
        valid = all(len(p) == 3 and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e9
                                        for v in p) for p in coordinates)
        valid = valid and 0 < len(coordinates) <= 500000 and 0 < len(faces) <= 500000
        valid = valid and len(labels) == len(faces)
        for key, dimension in (('uv', 2), ('normals', 3)):
            valid = valid and len(value[key]) <= 500000 and all(len(p) == dimension and all(
                type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e9 for v in p) for p in value[key])
        valid = valid and all(type(i) is int and 0 <= i < len(coordinates) for face in faces for i in face)
        valid = valid and all(3 <= len(face) <= 256 and len(set(face)) == len(face) for face in faces)
        for array, corners in (('uv', 'face_uv_indices'), ('normals', 'face_normal_indices')):
            valid = valid and len(value[corners]) == len(faces) and all(len(row) == len(face) and all(
                i is None or type(i) is int and 0 <= i < len(value[array]) for i in row)
                for face, row in zip(faces, value[corners]))
        valid = valid and all(type(row['groups']) is list and all(type(g) is str and 0 < len(g) <= 256 for g in row['groups'])
                              and all(type(row[k]) is str for k in ('object', 'material', 'smoothing')) for row in labels)
        valid = valid and type(value['material_libraries']) is list and len(value['material_libraries']) <= 64 and all(
            type(name) is str and len(name) <= 65536 for name in value['material_libraries'])
        inventory = mesh.group_inventory(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        _reject('INVALID_MESH_SNAPSHOT', 'Bounded finite world-mm 3D positions required')
    if inventory['ungrouped_face_count'] or inventory['nonexclusive_face_count']:
        _reject('AMBIGUOUS_OBJ_GROUPS', 'Every scene face must have exactly one group label')
    return set(inventory['groups'])


def _triangles(snapshot, groups):
    triangles = []
    for index, (face, label) in enumerate(zip(snapshot['faces'], snapshot['face_labels'])):
        group = label['groups'][0]
        if group not in groups:
            continue
        if len(face) != 3:
            _reject('NONTRIANGULAR_TARGET', 'Target OBJ groups must already contain actual exported triangles')
        points = tuple(tuple(snapshot['positions_mm'][i]) for i in face)
        a, b, c = points
        u, v = _sub(b, a), _sub(c, a)
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        magnitude = math.hypot(*cross)
        edge2 = max(_dot(u, u), _dot(v, v), _dot(_sub(c, b), _sub(c, b)))
        if not magnitude > 1e-15 or magnitude/edge2 <= 1e-12:
            _reject('DEGENERATE_TARGET_TRIANGLE', 'Zero-area or numerically singular target triangle')
        triangles.append(dict(points=points, normal=tuple(x/magnitude for x in cross), area=magnitude/2,
            group=group, export_face_index=index,
            low=tuple(min(p[k] for p in points) for k in range(3)),
            high=tuple(max(p[k] for p in points) for k in range(3)),
            center=tuple(sum(p[k] for p in points)/3 for k in range(3))))
    return triangles


def _connectivity(triangles):
    parents, edges = list(range(len(triangles))), {}
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    manifold = True
    for i, triangle in enumerate(triangles):
        a, b, c = triangle['points']
        for edge in ((a, b), (b, c), (c, a)):
            key = tuple(sorted(edge))
            linked = edges.setdefault(key, [])
            linked.append(i)
            if len(linked) > 2:
                manifold = False
            if len(linked) > 1:
                parents[find(i)] = find(linked[0])
    return dict(edge_components=len({find(i) for i in range(len(triangles))}),
                manifold_edges=manifold, edge_welding='exact world XYZ endpoint pairs; not native topology')


def _direction(source, destination, tolerance, budget):
    tree = _BVH(destination, budget)
    worst, normal_witness, count, distance_ok = None, None, 0, True
    for triangle in source:
        a, b, c = triangle['points']
        samples = [('vertex', p, None) for p in (a, b, c)]
        samples += [('edge_midpoint', tuple((x+y)/2 for x, y in zip(p, q)), None)
                    for p, q in ((a, b), (b, c), (c, a))]
        samples.append(('centroid', triangle['center'], triangle['normal']))
        for kind, point, normal in samples:
            distance2, nearest, q, dot = tree.nearest(point, normal)
            distance = math.sqrt(max(0., distance2))
            count += 1
            witness = dict(sample_kind=kind, source_group=triangle['group'],
                source_export_face_index=triangle['export_face_index'], point_mm=list(point),
                nearest_group=nearest['group'], nearest_export_face_index=nearest['export_face_index'],
                nearest_point_mm=list(q), distance_mm=distance)
            if worst is None or distance > worst['distance_mm']:
                worst = witness
            # Guard float rounding conservatively; no exact-rational claim.
            rounding = 64*math.ulp(max(1., *(abs(v) for v in point), *(abs(v) for v in q)))
            distance_ok = distance_ok and distance+rounding <= tolerance
            if normal is not None and dot < .5 and normal_witness is None:
                normal_witness = dict(witness, normal_dot=dot)
    return dict(all_samples_evaluated=True, sample_count=count, sampled_distance_verified=distance_ok,
                centroid_orientation_verified=normal_witness is None, max_sample_witness=worst,
                orientation_failure_witness=normal_witness)


def verify_cut_surface(before, after, before_groups, after_groups, *, tolerance_mm=.002,
                       relative_area_tolerance=1e-5, max_triangles=25000,
                       max_samples=200000, max_triangle_tests=2000000,
                       native_boundary_partition_verified=False):
    """Compare target group unions bidirectionally; exact-check every other group.

Snapshots use embedded.mesh's hashed scene export schema. This pure function
does not re-open/re-hash asset files: the caller must bind capture freshness and
actual OBJ bytes. Work exhaustion is UNSUPPORTED, never a successful sample.
"""
    report = dict(schema='md-cut-exported-surface-readback-v1', verified=False,
        verification_completed=False, verification_scope='bidirectional finite exported-triangle samples, area, exact-edge connectivity, non-target OBJ arrays',
        continuous_hausdorff_verified=False, solver_rest_verified=False,
        native_owner_binding_verified=False, target_uv_preservation_verified=False,
        target_vertex_normals_preservation_verified=False, material_properties_verified=False,
        asset_bytes_rehashed=False, full_cut_preservation_verified=False, effects_started=False)
    budget = None
    try:
        for value, cap, name in ((max_triangles, 50000, 'max_triangles'), (max_samples, 500000, 'max_samples'),
                                  (max_triangle_tests, 5000000, 'max_triangle_tests')):
            if type(value) is not int or not 1 <= value <= cap:
                _reject('SURFACE_WORK_BUDGET', name+' exceeds its fixed hard cap')
        for value in (tolerance_mm, relative_area_tolerance):
            if type(value) not in (int, float) or not 0 < value <= 1. or not math.isfinite(value):
                _reject('INVALID_SURFACE_TOLERANCE', 'Positive finite tolerances required')
        if tolerance_mm > .002 or relative_area_tolerance > .001:
            _reject('INVALID_SURFACE_TOLERANCE', 'Surface/relative-area tolerances exceed .002mm/.001')
        first, second = _groups(before_groups, 'before_groups'), _groups(after_groups, 'after_groups')
        old_labels, new_labels = _snapshot(before), _snapshot(after)
        if before.get('scene_identity') != after.get('scene_identity') or type(before.get('scene_identity')) is not dict:
            _reject('SCENE_IDENTITY_CHANGED', 'Before/after snapshots must have the same explicit process/project identity')
        report['inputs'] = dict(before_snapshot_id=before['snapshot_id'], after_snapshot_id=after['snapshot_id'],
                                before_groups=sorted(first), after_groups=sorted(second))
        if not first <= old_labels or not second <= new_labels:
            _reject('MISSING_TARGET_GROUP', 'Every requested target/child group must actually exist')
        unchanged = old_labels-first == new_labels-second
        other = {}
        if unchanged:
            for name in sorted(old_labels-first):
                keys = ('positions_mm', 'uv', 'normals', 'faces', 'face_uv_indices', 'face_normal_indices', 'face_labels')
                a, b = mesh.extract_group(before, name), mesh.extract_group(after, name)
                other[name] = all(a[k] == b[k] for k in keys)
        old, new = _triangles(before, first), _triangles(after, second)
        if not old or not new or len(old)+len(new) > max_triangles or 7*(len(old)+len(new)) > max_samples:
            _reject('SURFACE_WORK_BUDGET', 'Complete target triangle/sample inventory exceeds the budget')
        old_area, new_area = math.fsum(t['area'] for t in old), math.fsum(t['area'] for t in new)
        relative_area_error = abs(new_area-old_area)/old_area
        old_connectivity, new_connectivity = _connectivity(old), _connectivity(new)
        child_connectivity = {name: _connectivity([t for t in new if t['group'] == name]) for name in sorted(second)}
        budget = _Budget(max_triangle_tests)
        forward, reverse = _direction(old, new, tolerance_mm, budget), _direction(new, old, tolerance_mm, budget)
        checks = dict(non_target_group_set_exact=unchanged,
            non_target_exported_arrays_exact=unchanged and all(other.values()),
            target_area_preserved=relative_area_error <= relative_area_tolerance,
            bidirectional_sampled_distance_verified=forward['sampled_distance_verified'] and reverse['sampled_distance_verified'],
            centroid_orientation_preserved=forward['centroid_orientation_verified'] and reverse['centroid_orientation_verified'],
            target_union_edge_connectivity_preserved=old_connectivity['edge_components'] == new_connectivity['edge_components'],
            target_manifold_edges=old_connectivity['manifold_edges'] and new_connectivity['manifold_edges'],
            child_groups_edge_connected=all(c['edge_components'] == 1 and c['manifold_edges'] for c in child_connectivity.values()))
        exact_connectivity = checks['target_union_edge_connectivity_preserved']
        interface = None
        if (native_boundary_partition_verified is True and not exact_connectivity and len(first) == 1 and len(second) == 2
                and old_connectivity['edge_components'] == 1 and new_connectivity['edge_components'] == 2
                and all(ok for name, ok in checks.items() if name != 'target_union_edge_connectivity_preserved')):
            interface = verify_cut_interface(old, new, sorted(second), tolerance_mm=tolerance_mm)
            checks['target_union_edge_connectivity_preserved'] = interface['verified'] is True
            if interface['verified'] is True:
                report['verification_scope'] = ('bidirectional finite exported-triangle samples, area, unique tolerant Cut interface, '
                                                'non-target OBJ arrays')
        report.update(verified=all(checks.values()), verification_completed=True, checks=checks,
            non_target_groups=other, before_to_after=forward, after_to_before=reverse,
            area_mm2=dict(before=old_area, after=new_area, relative_error=relative_area_error),
            connectivity=dict(before=old_connectivity, after=new_connectivity, children=child_connectivity,
                              exact_edge_components_preserved=exact_connectivity, tolerant_cut_interface=interface),
            triangle_counts=dict(before=len(old), after=len(new)), tolerance_mm=tolerance_mm,
            status='SUCCEEDED' if all(checks.values()) else 'FAILED', error=None)
    except SurfaceReadbackError as exc:
        report.update(status='UNSUPPORTED', error=dict(code=exc.code, message=str(exc)))
    if budget is not None:
        report['work'] = dict(triangle_tests=budget.tests, bvh_node_visits=budget.nodes,
                              max_triangle_tests=budget.limit, budget_exhausted=budget.exhausted,
                              all_work_within_budget=budget.tests <= budget.limit and budget.nodes <= budget.limit*8)
    return report
