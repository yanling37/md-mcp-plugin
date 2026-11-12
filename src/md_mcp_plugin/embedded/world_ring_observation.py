"""Image-only observations of colored ellipse contours in a captured 3D PNG.

All coordinates are physical PNG pixel-index centers, with (0, 0) at the
top-left sample and Y down. An ellipse angle describes its image coordinates.
This module has no camera, project, input or native hit-test dependencies.
"""

from __future__ import annotations

import hashlib
import math
import random
import re

from .curve_projection import decode_png


SCHEMA = 'md-world-ring-observation-v1'
COLORS = ('red', 'green', 'blue')
SECTORS = 72
MAX_COLOR_PIXELS = 20_000
MAX_FIT_PIXELS = 1_200
FIT_TRIALS = 192


def _bounds(points):
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points)]


def _region(roi, width, height):
    if roi is None:
        return (0, 0, width, height)
    if type(roi) not in (tuple, list) or len(roi) != 4 or any(type(x) is not int for x in roi):
        raise ValueError('roi_px requires four integer [left, top, right, bottom] bounds')
    left, top, right, bottom = roi
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError('roi_px must be an in-image, right/bottom-exclusive region')
    return tuple(roi)


def _color_points(image, roi, floor, dominance):
    width, channels, data = (image[key] for key in ('width', 'channels', 'pixels'))
    left, top, right, bottom = roi
    points = [[], [], []]
    counts = [0, 0, 0]
    for y in range(top, bottom):
        for x in range(left, right):
            start = (y * width + x) * channels
            if channels == 4 and data[start + 3] < 240:
                continue
            r, g, b = data[start:start + 3]
            if r >= floor and r - max(g, b) >= dominance:
                channel = 0
            elif g >= floor and g - max(r, b) >= dominance:
                channel = 1
            elif b >= floor and b - max(r, g) >= dominance:
                channel = 2
            else:
                continue
            counts[channel] += 1
            if counts[channel] <= MAX_COLOR_PIXELS:
                points[channel].append((x, y))
    return zip(COLORS, counts, points, strict=True)


def _components(points):
    pending = set(points)
    records = []
    while pending:
        seed = pending.pop()
        component = [seed]
        for x, y in component:
            for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
                adjacent = (x + dx, y + dy)
                if adjacent in pending:
                    pending.remove(adjacent)
                    component.append(adjacent)
        records.append({'pixel_count': len(component), 'pixel_bounds_xyxy_inclusive': _bounds(component)})
    records.sort(key=lambda row: (-row['pixel_count'], row['pixel_bounds_xyxy_inclusive']))
    return len(records), records[:12]


def _solve(rows):
    """Small pivoted solve for a normalized five-coefficient conic."""
    matrix = [list(row) for row in rows]
    for column in range(5):
        pivot = max(range(column, 5), key=lambda index: abs(matrix[index][column]))
        if abs(matrix[pivot][column]) < 1e-10:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        row = matrix[column]
        divisor = row[column]
        for j in range(column, 6):
            row[j] /= divisor
        for i in range(5):
            if i == column:
                continue
            factor = matrix[i][column]
            for j in range(column, 6):
                matrix[i][j] -= factor * row[j]
    return [row[5] for row in matrix]


def _fit(points, normalization, roi):
    ox, oy, scale = normalization
    terms = []
    for x, y in points:
        u, v = (x - ox) / scale, (y - oy) / scale
        terms.append((u*u, u*v, v*v, u, v))
    if len(terms) == 5:
        coefficients = _solve([row + (1.0,) for row in terms])
    else:
        normal = [[0.] * 6 for _ in range(5)]
        for row in terms:
            for i, value in enumerate(row):
                for j in range(i, 5):
                    normal[i][j] += value * row[j]
                normal[i][5] += value
        for i in range(5):
            for j in range(i):
                normal[i][j] = normal[j][i]
        coefficients = _solve(normal)
    if coefficients is None:
        return None
    a, b, c, d, e = coefficients
    determinant = 4 * a * c - b*b
    if determinant <= 1e-12:
        return None
    x = (b * e - 2 * c * d) / determinant
    y = (b * d - 2 * a * e) / determinant
    level = 1 + a * x*x + b * x*y + c * y*y
    if abs(level) <= 1e-12:
        return None
    a, b, c = a / level, b / level, c / level
    difference = math.hypot(a - c, b)
    low, high = (a + c - difference) / 2, (a + c + difference) / 2
    if not (0 < low <= high):
        return None
    major, minor = scale / math.sqrt(low), scale / math.sqrt(high)
    cx, cy = ox + scale * x, oy + scale * y
    angle = (math.atan2(b, a - c) / 2 + math.pi / 2) % math.pi
    left, top, right, bottom = roi
    if (not (left <= cx < right and top <= cy < bottom)
            or not 3 <= minor <= major <= math.hypot(right - left, bottom - top)
            or major / minor > 24):
        return None
    return cx, cy, major, minor, angle


def _support(model, points, tolerance):
    cx, cy, major, minor, angle = model
    c, s = math.cos(angle), math.sin(angle)
    samples = []
    for x, y in points:
        dx, dy = x - cx, y - cy
        u, v = (dx * c + dy * s) / major, (-dx * s + dy * c) / minor
        gradient = 2 * math.hypot(u / major, v / minor)
        if gradient == 0:
            continue
        residual = abs(u*u + v*v - 1) / gradient
        if residual <= tolerance:
            degrees = math.degrees(math.atan2(v, u)) % 360
            sector = min(SECTORS - 1, int(degrees * SECTORS / 360))
            samples.append((x, y, residual, sector, degrees))
    return samples


def _score(samples):
    return len({row[3] for row in samples}), len(samples), -sum(row[2] for row in samples)


def _consensus(points, roi, tolerance):
    if len(points) < 24:
        return None, []
    x0, y0, x1, y1 = _bounds(points)
    scale = max(x1 - x0, y1 - y0) / 2
    if scale < 3:
        return None, []
    normalization = ((x0 + x1) / 2, (y0 + y1) / 2, scale)
    if len(points) > MAX_FIT_PIXELS:
        pool = [points[i * len(points) // MAX_FIT_PIXELS] for i in range(MAX_FIT_PIXELS)]
    else:
        pool = points
    rng = random.Random(0)
    best, best_score = None, (0, 0, 0)
    for _ in range(FIT_TRIALS):
        model = _fit(rng.sample(pool, 5), normalization, roi)
        if model is not None:
            score = _score(_support(model, pool, tolerance))
            if score > best_score:
                best, best_score = model, score
    if best is None:
        return None, []
    inliers = _support(best, points, tolerance)
    for _ in range(2):
        if len(inliers) < 24:
            break
        refined = _fit([(x, y) for x, y, *_ in inliers], normalization, roi)
        if refined is None:
            break
        current = _support(refined, points, tolerance)
        if _score(current) < _score(inliers):
            break
        best, inliers = refined, current
    return best, inliers


def _support_runs(samples):
    """Lossless inlier pixels as [row, first_column, last_column_exclusive]."""
    runs = []
    for y, x in sorted((row[1], row[0]) for row in samples):
        if runs and runs[-1][0] == y and runs[-1][2] == x:
            runs[-1][2] += 1
        else:
            runs.append([y, x, x + 1])
    return runs


def _pixel(image, x, y):
    channels = image['channels']
    start = (y * image['width'] + x) * channels
    color = list(image['pixels'][start:start + channels])
    return color if channels == 4 else color + [255]


def _uncertainty(model, samples, roi):
    cx, cy, major, minor, _ = model
    errors = sorted(row[2] for row in samples)
    split_fits = []
    for parity in (0, 1):
        points = [(x, y) for x, y, _, sector, _ in samples if sector % 2 == parity]
        if len(points) >= 12:
            fit = _fit(points, (cx, cy, major), roi)
            if fit is not None:
                split_fits.append(fit)
    result = {'rms_contour_residual_px': math.sqrt(sum(x*x for x in errors) / len(errors)),
              'p95_contour_residual_px': errors[math.ceil(.95 * len(errors)) - 1],
              'maximum_contour_residual_px': errors[-1],
              'sample_center_quantization_per_axis_px': .5,
              'alternate_sector_fits_available': len(split_fits),
              'alternate_sector_center_max_shift_px': None,
              'alternate_sector_semiaxes_max_shift_px': None}
    if len(split_fits) == 2:
        result['alternate_sector_center_max_shift_px'] = max(math.dist((cx, cy), fit[:2]) for fit in split_fits)
        result['alternate_sector_semiaxes_max_shift_px'] = max(abs(a - b) for fit in split_fits for a, b in zip((major, minor), fit[2:4], strict=True))
    return result


def _candidate(image, color, model, inliers, all_points, roi):
    if model is None or len(inliers) < 24:
        return None
    sectors = {}
    for sample in inliers:
        sector = sample[3]
        if sector not in sectors or sample[2] < sectors[sector][2]:
            sectors[sector] = sample
    occupied = sorted(sectors)
    gaps = [b - a - 1 for a, b in zip(occupied, occupied[1:] + [occupied[0] + SECTORS], strict=True)]
    coverage = len(occupied) / SECTORS
    largest_gap = max(gaps) * 360 / SECTORS
    cx, cy, major, minor, angle = model
    cos, sin = math.cos(angle), math.sin(angle)
    inside = 0
    for x, y in all_points:
        dx, dy = x - cx, y - cy
        u, v = (dx * cos + dy * sin) / major, (-dx * sin + dy * cos) / minor
        inside += u*u + v*v < .49
    inner_fraction = min(1., inside / (math.pi * major * minor * .49))
    warnings = []
    if coverage < .5 or largest_gap > 125.:
        warnings.append('SPARSE_ANGULAR_SUPPORT')
    if inner_fraction > .3:
        warnings.append('DOMINANT_COLOR_FILLS_INTERIOR')
    if len(inliers) / len(all_points) < .2:
        warnings.append('LOW_COLOR_SUPPORT_FRACTION')
    return {'color_class': color, 'status': 'IMAGE_CONTOUR_CANDIDATE' if not warnings else 'AMBIGUOUS_CONTOUR',
            'warnings': warnings, 'ellipse_center_px': [cx, cy], 'ellipse_semiaxes_px': [major, minor],
            'ellipse_major_axis_image_degrees': math.degrees(angle),
            'support_pixel_count': len(inliers), 'dominant_color_support_fraction': len(inliers) / len(all_points),
            'support_pixel_bounds_xyxy_inclusive': _bounds([(x, y) for x, y, *_ in inliers]),
            'angular_coverage': {'occupied_bins': occupied, 'bin_count': SECTORS, 'occupied_fraction': coverage,
                                 'largest_empty_gap_ellipse_degrees': largest_gap},
            'dominant_color_fraction_in_inner_ellipse': inner_fraction,
            'support_runs_y_x0_x1_exclusive': _support_runs(inliers),
            'measured_pixels': [{'pixel_xy': [x, y], 'rgba': _pixel(image, x, y),
                                 'ellipse_parameter_degrees': degrees, 'contour_residual_px': residual}
                                for x, y, residual, _, degrees in (sectors[key] for key in occupied)],
            'uncertainty': _uncertainty(model, inliers, roi)}


def _verified_png_sha(png_bytes, expected_png_sha256):
    if type(png_bytes) is not bytes or not 8 <= len(png_bytes) <= 16 * 1024 * 1024:
        raise ValueError('Bounded PNG bytes required')
    sha = hashlib.sha256(png_bytes).hexdigest()
    if expected_png_sha256 is not None and (type(expected_png_sha256) is not str
            or re.fullmatch('[0-9a-f]{64}', expected_png_sha256) is None or expected_png_sha256 != sha):
        raise ValueError('PNG SHA256 differs from capture receipt')
    return sha


def _decode_capture(png_bytes, expected_png_sha256):
    sha = _verified_png_sha(png_bytes, expected_png_sha256)
    return decode_png(png_bytes), sha


def _validate_thresholds(channel_floor, color_dominance, contour_tolerance_px):
    for value in (channel_floor, color_dominance):
        if type(value) is not int or not 1 <= value <= 255:
            raise ValueError('Color thresholds must be integers in [1,255]')
    if type(contour_tolerance_px) not in (int, float) or not .5 <= contour_tolerance_px <= 4.:
        raise ValueError('Contour tolerance must be finite and in [.5,4] physical pixels')


def _observe_decoded(image, sha, *, roi_px=None, channel_floor=112, color_dominance=55,
                     contour_tolerance_px=1.5, only_color=None):
    """Observe a PNG already decoded from bytes verified by the caller."""
    if only_color is not None and only_color not in COLORS:
        raise ValueError('Expected a known RGB contour color')
    size = [image['width'], image['height']]
    roi = _region(roi_px, *size)
    observations, candidates = {}, []
    for color, count, points in _color_points(image, roi, channel_floor, color_dominance):
        if only_color is not None and color != only_color:
            continue
        observation = {'dominant_pixel_count': count}
        observations[color] = observation
        if count > MAX_COLOR_PIXELS:
            observation['status'] = 'COLOR_OCCUPANCY_LIMIT'
            continue
        component_count, largest = _components(points)
        model, inliers = _consensus(points, roi, contour_tolerance_px)
        candidate = _candidate(image, color, model, inliers, points, roi)
        observation.update(component_count=component_count, largest_components=largest,
                           status=candidate['status'] if candidate else 'NO_CONTOUR_CANDIDATE')
        if candidate:
            candidates.append(candidate)
    return {'schema': SCHEMA, 'png_sha256': sha, 'image_size_px': size, 'roi_px': list(roi),
            'coordinate_space': 'png_pixel_index_centers_x_right_y_down',
            'thresholds': {'channel_floor': channel_floor, 'color_dominance': color_dominance,
                           'minimum_alpha': 240, 'contour_tolerance_px': contour_tolerance_px},
            'color_observations': observations, 'candidates': candidates,
            'world_axis_verified': False, 'world_pivot_verified': False, 'native_ring_hit_verified': False}


def observe_rings(png_bytes, *, roi_px=None, expected_png_sha256=None, channel_floor=112,
                  color_dominance=55, contour_tolerance_px=1.5):
    """Extract one robust ellipse hypothesis per dominant RGB color in a PNG.

    Use the returned inlier pixel runs for comparisons to independently
    projected locations. Ellipse parameters and color classes have only image
    semantics. A missing or ambiguous candidate is an ordinary outcome.
    """
    sha = _verified_png_sha(png_bytes, expected_png_sha256)
    _validate_thresholds(channel_floor, color_dominance, contour_tolerance_px)
    return _observe_decoded(decode_png(png_bytes), sha, roi_px=roi_px, channel_floor=channel_floor,
                            color_dominance=color_dominance, contour_tolerance_px=contour_tolerance_px)
