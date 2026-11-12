"""Pure tolerant correspondence of two exported Cut child boundary chains."""

from collections import defaultdict
from itertools import product
import math


_SCHEMA = "md-cut-exported-interface-readback/1"
_MAX_TRIANGLES = 50_000
_MAX_DISTANCE_TESTS = 2_000_000
_NEIGHBOUR_CELLS = tuple(product((-1, 0, 1), repeat=3))


class _InterfaceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _require(condition, code, message):
    if not condition:
        raise _InterfaceError(code, message)


def _key(a, b):
    return (a, b) if a < b else (b, a)


def _mesh_edges(triangles):
    parents = list(range(len(triangles)))
    edges = {}
    boundary = {}

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, row in enumerate(triangles):
        points = tuple(tuple(p) for p in row["points"])
        _require(len(points) == len(set(points)) == 3 and all(
            len(p) == 3 and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e9 for v in p)
            for p in points), "INVALID_INTERFACE_TRIANGLE", "Distinct finite world XYZ triangle corners required")
        a, b, c = points
        u = [x-y for x, y in zip(b, a)]
        v = [x-y for x, y in zip(c, a)]
        _require(any(u[i]*v[j] != u[j]*v[i] for i, j in ((0, 1), (1, 2), (2, 0))),
                 "INVALID_INTERFACE_TRIANGLE", "Degenerate world triangle cannot establish an interface")
        for first, second in ((a, b), (b, c), (c, a)):
            key = _key(first, second)
            incident = edges.setdefault(key, [])
            _require(len(incident) < 2, "INTERFACE_NONMANIFOLD", "An edge has more than two incident triangles")
            if incident:
                other, x, y = incident[0]
                _require((x, y) == (second, first), "INTERFACE_ORIENTATION_UNPROVEN",
                         "Interior edges must have opposite incident triangle directions")
                parents[find(index)] = find(other)
                del boundary[key]
            else:
                boundary[key] = (first, second)
            incident.append((index, first, second))
    _require(len({find(i) for i in range(len(triangles))}) == 1, "INTERFACE_CHILD_DISCONNECTED",
             "Each child and the original target must be exactly edge-connected on their own")
    return edges, boundary


def _path_ends(edges, *, closed=False):
    neighbours = defaultdict(set)
    for a, b in edges:
        neighbours[a].add(b)
        neighbours[b].add(a)
    ends = sorted(p for p, linked in neighbours.items() if len(linked) == 1)
    _require(neighbours and len(ends) == (0 if closed else 2)
             and all(1 <= len(linked) <= 2 for linked in neighbours.values()), "INTERFACE_CHAIN_UNPROVEN",
             "The tested edge path must be simple and have the expected endpoints")
    pending = [next(iter(neighbours))]
    seen = set(pending)
    while pending:
        for point in neighbours[pending.pop()] - seen:
            seen.add(point)
            pending.append(point)
    _require(len(seen) == len(neighbours), "INTERFACE_CHAIN_UNPROVEN",
             "The tested edges include a disconnected path or cycle")
    return ends


class _PointIndex:
    def __init__(self, points, tolerance, work):
        self.cell_size = 2 * tolerance
        self.tolerance = tolerance
        self.work = work
        self.cells = defaultdict(list)
        for point in sorted(points):
            self.cells[self._cell(point)].append(point)

    def _cell(self, point):
        return tuple(math.floor(coordinate / self.cell_size) for coordinate in point)

    def near(self, point):
        cell = self._cell(point)
        found = None
        for delta in _NEIGHBOUR_CELLS:
            for other in self.cells.get(tuple(a+b for a, b in zip(cell, delta)), ()):
                self.work[0] += 1
                _require(self.work[0] <= _MAX_DISTANCE_TESTS, "INTERFACE_WORK_BUDGET",
                         "World endpoint correspondence exceeded its fixed work budget")
                if math.dist(point, other) <= self.tolerance:
                    _require(found is None, "AMBIGUOUS_INTERFACE_ENDPOINT",
                             "A boundary vertex has more than one counterpart inside the tolerance")
                    found = other
        return found


def _boundary_points(edges):
    return {p for edge in edges for p in edge}


def _verify(before, after, child_groups, tolerance, report):
    _require(type(tolerance) in (int, float) and 0 < tolerance <= .002,
             "INVALID_INTERFACE_TOLERANCE", "The existing .002 mm tolerance is the maximum")
    _require(type(before) in (tuple, list) and type(after) in (tuple, list)
             and 0 < len(before) and 0 < len(after) and len(before) + len(after) <= _MAX_TRIANGLES,
             "INVALID_INTERFACE_SCOPE", "Bounded original and child triangles required")
    _require(type(child_groups) in (tuple, list) and len(child_groups) == 2
             and all(type(g) is str and g for g in child_groups) and child_groups[0] != child_groups[1],
             "INVALID_INTERFACE_SCOPE", "Two distinct child group labels required")
    first, second = child_groups
    _require(len({row["group"] for row in before}) == 1 and {row["group"] for row in after} == {first, second},
             "INVALID_INTERFACE_SCOPE", "Expected one original target and exactly the two requested child groups")
    _, original_boundary = _mesh_edges(before)
    first_edges, first_boundary = _mesh_edges([row for row in after if row["group"] == first])
    second_edges, second_boundary = _mesh_edges([row for row in after if row["group"] == second])
    shared = first_edges.keys() & second_edges.keys()
    _require(all(key in first_boundary and key in second_boundary for key in shared),
             "INTERFACE_NONMANIFOLD", "The raw child union contains a cross-group non-manifold edge")
    report.update(before_exact_edge_components=1, after_exact_edge_components=1 if shared else 2)
    _require(not shared, "INTERFACE_EXACT_UNION_ALREADY_CONNECTED",
             "Use the exact union check when the child groups already share an exact edge")
    _path_ends(original_boundary, closed=True)

    work = [0]
    points = _PointIndex(_boundary_points(second_boundary), tolerance, work)
    pairs = {p: q for p in sorted(_boundary_points(first_boundary)) if (q := points.near(p)) is not None}
    inverse = {q: p for p, q in pairs.items()}
    _require(len(pairs) == len(inverse), "AMBIGUOUS_INTERFACE_ENDPOINT",
             "The tolerant endpoint correspondence must be unique in both directions")
    first_interface = {}
    second_interface = {}
    for key, (a, b) in first_boundary.items():
        if a not in pairs or b not in pairs:
            continue
        x, y = pairs[a], pairs[b]
        other = _key(x, y)
        _require(other in second_boundary, "INCOMPLETE_INTERFACE_EDGE_MATCH",
                 "Two matched endpoints must declare a reciprocal boundary edge")
        _require(second_boundary[other] == (y, x), "INTERFACE_ORIENTATION_UNPROVEN",
                 "Paired child boundary edges must have opposite triangle directions")
        first_interface[key] = (a, b)
        second_interface[other] = (y, x)
    _require(all(key in second_interface for key in second_boundary if all(p in inverse for p in key))
             and _boundary_points(first_interface) == pairs.keys()
             and _boundary_points(second_interface) == inverse.keys(), "INCOMPLETE_INTERFACE_EDGE_MATCH",
             "Every matched boundary vertex and edge must belong to the same reciprocal interface")
    first_ends = _path_ends(first_interface)
    second_ends = _path_ends(second_interface)
    _require({pairs[p] for p in first_ends} == set(second_ends)
             and first_ends == _path_ends(first_boundary.keys() - first_interface.keys())
             and second_ends == _path_ends(second_boundary.keys() - second_interface.keys()),
             "INTERFACE_ENDPOINT_CLOSURE_UNPROVEN", "Both child exterior paths must close at the interface endpoints")

    source_points = _PointIndex(_boundary_points(original_boundary), tolerance, work)
    outer_endpoints = []
    for p in first_ends:
        q = pairs[p]
        source = source_points.near(p)
        _require(source is not None and source_points.near(q) == source,
                 "INTERFACE_ENDPOINT_CLOSURE_UNPROVEN", "Both copies of each interface endpoint must meet the same original boundary vertex")
        outer_endpoints.append(source)
    _require(len(set(outer_endpoints)) == 2, "INTERFACE_ENDPOINT_CLOSURE_UNPROVEN",
             "The interface must connect two distinct original boundary vertices")

    report.update(verified=True, reason="UNIQUE_TOLERANT_CUT_INTERFACE",
                  paired_edge_count=len(first_interface), paired_vertex_count=len(pairs),
                  maximum_paired_endpoint_distance_mm=max(math.dist(p, q) for p, q in pairs.items()),
                  child_interface_lengths_mm={first: math.fsum(math.dist(*edge) for edge in first_interface),
                                              second: math.fsum(math.dist(*edge) for edge in second_interface)},
                  original_boundary_endpoints_mm=[list(p) for p in outer_endpoints],
                  unique_reciprocal_edge_pairs=True, opposite_edge_directions=True,
                  simple_connected_open_chain=True, original_boundary_endpoint_closure=True,
                  endpoint_distance_tests=work[0])


def verify_cut_interface(before_triangles, after_triangles, child_groups, *, tolerance_mm=.002):
    """Prove one Cut interface from oriented world-mm exported triangles only."""
    report = {"schema": _SCHEMA, "verified": False, "tolerance_mm": tolerance_mm,
              "coordinate_welding_applied": False, "native_topology_verified": False}
    try:
        _verify(before_triangles, after_triangles, child_groups, tolerance_mm, report)
    except _InterfaceError as exc:
        report.update(reason=exc.code, error=str(exc))
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        report.update(reason="INVALID_INTERFACE_SCOPE", error=str(exc))
    return report
