"""Verify derived 2D polyline caches and perimeter sewing references geometrically."""
import copy
import math


def _xy(point):
    value = point['Position']
    result = [float(value['x']), float(value['y'])]
    if not all(math.isfinite(x) for x in result):
        raise ValueError('Nonfinite native point')
    return result


def _sample(line, t):
    points = line['PointList']
    xy = [_xy(p) for p in points]
    if len(points) == 2 and all(p['PointType'] == 'Straight' for p in points):
        return [(1 - t) * a + t * b for a, b in zip(*xy)]
    if len(points) != 4 or [p['PointType'] for p in points] != ['Straight', 'Bezier Curve', 'Bezier Curve', 'Straight']:
        raise ValueError('Only native straight lines and cubic Beziers have a verified sampling model')
    # de Casteljau evaluation avoids a cancellation-prone expanded polynomial.
    for _ in range(3):
        xy = [[(1 - t) * a + t * b for a, b in zip(u, v)] for u, v in zip(xy, xy[1:])]
    return xy[0]


def _cache(pattern, tolerance):
    outer = pattern['ShapeInfo']['LineList']
    matches = []
    for child_index, child in enumerate(pattern.get('InternalLineList', [])):
        if type(child.get('ShapeType')) is not int or child['ShapeType'] != 2 or len(child.get('LineList', [])) != len(outer):
            continue
        maximum = 0.0
        good = True
        for line, cached in zip(outer, child['LineList']):
            points = cached['PointList']
            if not 2 <= len(points) <= 4096 or points[0]['PointType'] != 'Straight' or points[-1]['PointType'] != 'Straight':
                good = False
                break
            if any(p['PointType'] != 'Polyline' for p in points[1:-1]):
                good = False
                break
            if _xy(points[0]) != _xy(line['PointList'][0]) or _xy(points[-1]) != _xy(line['PointList'][-1]):
                good = False
                break
            for i, point in enumerate(points):
                error = math.dist(_xy(point), _sample(line, i / (len(points) - 1)))
                maximum = max(maximum, error)
                if error > tolerance:
                    good = False
                    break
            if not good:
                break
        if good:
            matches.append((child_index, child['LineList'], maximum))
    if len(matches) != 1:
        raise ValueError('A unique complete uniform-sample Type2 cache is required')
    return matches[0]


def _station(lines, line_index, ratio):
    if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
        raise ValueError('Invalid normalized perimeter reference')
    lengths = [[math.dist(_xy(a), _xy(b)) for a, b in zip(line['PointList'], line['PointList'][1:])] for line in lines]
    totals = [sum(row) for row in lengths]
    total = sum(totals)
    distance = ratio * total - sum(totals[:line_index])
    if total <= 0 or distance < -0.002 or distance > totals[line_index] + 0.002:
        raise ValueError('Perimeter station is outside the named sewn line')
    distance = max(0, min(distance, totals[line_index]))
    points = lines[line_index]['PointList']
    for i, length in enumerate(lengths[line_index]):
        if distance <= length or i == len(points) - 2:
            t = distance / length if length else 0
            return [(1 - t) * a + t * b for a, b in zip(_xy(points[i]), _xy(points[i + 1]))]
        distance -= length
    raise ValueError('Missing perimeter segment')


def normalize(before, after, point_path, *, tolerance_mm):
    """Return a comparison-only copy. Never alter MD or ignore whole cache blocks.

    Every changed cache point must independently match the same-index uniform
    sample on its complete current cubic before AND after. Sewing ratio changes
    additionally require the same explicit named line and physical endpoints.
    """
    result = copy.deepcopy(after)
    proof = {'verified': False, 'normalized_paths': [], 'cache_max_error_mm': None,
             'sewing_endpoint_max_error_mm': 0.0, 'solver_rest_verified': False}
    try:
        point_path = tuple(point_path)
        if len(point_path) != 7 or point_path[:1] != ('PatternList',) or point_path[2] != 'ShapeInfo':
            raise ValueError('Derived normalization currently requires an outer cubic handle')
        owner_index, edited_line = point_path[1], point_path[4]
        old_owner, new_owner = before['PatternList'][owner_index], after['PatternList'][owner_index]
        if old_owner['ID'] != new_owner['ID']:
            raise ValueError('Owner changed')
        cache_tolerance = min(0.0005, tolerance_mm / 4)
        old_index, old_cache, old_error = _cache(old_owner, cache_tolerance)
        new_index, new_cache, new_error = _cache(new_owner, cache_tolerance)
        if old_index != new_index:
            raise ValueError('Derived cache structure changed')
        proof['cache_max_error_mm'] = max(old_error, new_error)
        for li, (old_line, new_line) in enumerate(zip(old_cache, new_cache)):
            if len(old_line['PointList']) != len(new_line['PointList']):
                raise ValueError('Derived sample topology changed')
            for qi, (a, b) in enumerate(zip(old_line['PointList'], new_line['PointList'])):
                if a['Position'] == b['Position']:
                    continue
                if li != edited_line or not 0 < qi < len(old_line['PointList']) - 1:
                    raise ValueError('A non-target cache line or endpoint changed')
                path = ('PatternList', owner_index, 'InternalLineList', old_index, 'LineList', li, 'PointList', qi, 'Position')
                result['PatternList'][owner_index]['InternalLineList'][old_index]['LineList'][li]['PointList'][qi]['Position'] = copy.deepcopy(a['Position'])
                proof['normalized_paths'].append(list(path))
        old_groups, new_groups = before.get('SeamLinePairGroupList', []), after.get('SeamLinePairGroupList', [])
        if len(old_groups) != len(new_groups):
            raise ValueError('Sewing group count changed')
        for gi, (old_group, new_group) in enumerate(zip(old_groups, new_groups)):
            if len(old_group.get('PairList', [])) != len(new_group.get('PairList', [])):
                raise ValueError('Sewing pair count changed')
            for pi, (a, b) in enumerate(zip(old_group.get('PairList', []), new_group.get('PairList', []))):
                for side in ('First', 'Second'):
                    first, last = a[side], b[side]
                    if first.get('LengthParam') == last.get('LengthParam'):
                        continue
                    if first['ShapeID'] != old_owner['ID'] or last['ShapeID'] != first['ShapeID'] or last['LineID'] != first['LineID']:
                        raise ValueError('Changed sewing reference is not on this owner and named line')
                    indices = [i for i, line in enumerate(old_owner['ShapeInfo']['LineList']) if line['ID'] == first['LineID']]
                    if len(indices) != 1:
                        raise ValueError('Sewn line does not resolve uniquely')
                    li = indices[0]
                    for key in ('fStart', 'fEnd'):
                        p = _station(old_cache, li, first['LengthParam'][key])
                        q = _station(new_cache, li, last['LengthParam'][key])
                        error = math.dist(p, q)
                        proof['sewing_endpoint_max_error_mm'] = max(error, proof['sewing_endpoint_max_error_mm'])
                        if error > tolerance_mm:
                            raise ValueError('Physical sewing endpoint moved beyond tolerance')
                        result['SeamLinePairGroupList'][gi]['PairList'][pi][side]['LengthParam'][key] = first['LengthParam'][key]
                        proof['normalized_paths'].append(['SeamLinePairGroupList', gi, 'PairList', pi, side, 'LengthParam', key])
        proof['verified'] = True
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError) as exc:
        proof['reason'] = str(exc)
        return copy.deepcopy(after), proof
    return result, proof
