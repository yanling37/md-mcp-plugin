"""Bounded stdlib PNG/marker/similarity math. No native APIs or input calls."""
import itertools
import math
import struct
import zlib


class ProjectionError(ValueError):
    def __init__(self, code, message, diagnostics=None):
        self.code, self.diagnostics = code, diagnostics or {}
        super().__init__(message)


def decode_png(data):
    """Decode only noninterlaced 8-bit RGB/RGBA, including PNG filters 0..4."""
    if type(data) is not bytes or not 8 <= len(data) <= 16 * 1024 * 1024 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Bounded PNG bytes required')
    offset, header, compressed, ended, phase, chunks = 8, None, bytearray(), False, 0, 0
    while offset < len(data):
        if offset + 12 > len(data) or chunks >= 256:
            raise ValueError('Truncated/oversized PNG chunk list')
        size, = struct.unpack_from('>I', data, offset); tag = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data) or not all(65 <= c <= 90 or 97 <= c <= 122 for c in tag):
            raise ValueError('Invalid PNG chunk')
        body = data[offset + 8:end - 4]; crc, = struct.unpack_from('>I', data, end - 4)
        if zlib.crc32(tag + body) & 0xffffffff != crc:
            raise ValueError('PNG CRC differs')
        if header is None and tag != b'IHDR':
            raise ValueError('IHDR must be first')
        if tag == b'IHDR':
            if header is not None or size != 13:
                raise ValueError('Invalid/duplicate PNG header')
            w, h, depth, kind, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            if not 0 < w <= 4096 or not 0 < h <= 4096 or w * h > 4000000 or (depth, kind) not in ((8, 2), (8, 6)) or (compression, filtering, interlace) != (0, 0, 0):
                raise ValueError('Only bounded noninterlaced RGB8/RGBA8 supported')
            header = w, h, 3 if kind == 2 else 4
        elif tag == b'IDAT':
            if phase == 2:
                raise ValueError('IDAT chunks must be consecutive')
            phase = 1; compressed.extend(body)
        elif tag == b'IEND':
            if size or not compressed or end != len(data):
                raise ValueError('Invalid PNG end')
            ended = True; break
        elif tag[:1].isupper() and tag != b'PLTE':
            raise ValueError('Unsupported critical PNG chunk')
        elif phase == 1:
            phase = 2
        offset = end; chunks += 1
    if not ended or header is None:
        raise ValueError('Incomplete PNG')
    w, h, channels = header; stride = w * channels; expected = h * (stride + 1)
    stream = zlib.decompressobj(); raw = stream.decompress(bytes(compressed), expected + 1)
    if len(raw) != expected or not stream.eof or stream.unused_data or stream.unconsumed_tail:
        raise ValueError('Invalid/excess PNG scanline data')
    pixels = bytearray(); previous = bytearray(stride)
    for y in range(h):
        start = y * (stride + 1); mode = raw[start]; row = bytearray(raw[start + 1:start + 1 + stride])
        if mode > 4:
            raise ValueError('Unsupported PNG filter')
        if mode == 1:
            for x in range(channels, stride):
                row[x] = (row[x] + row[x - channels]) & 255
        elif mode == 2:
            for x, above in enumerate(previous):
                row[x] = (row[x] + above) & 255
        elif mode == 3:
            for x in range(channels):
                row[x] = (row[x] + previous[x] // 2) & 255
            for x in range(channels, stride):
                row[x] = (row[x] + (row[x - channels] + previous[x]) // 2) & 255
        elif mode == 4:
            for x in range(channels):
                row[x] = (row[x] + previous[x]) & 255
            for x in range(channels, stride):
                a, b, c = row[x - channels], previous[x], previous[x - channels]
                da, db, dc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                predictor = a if da <= db and da <= dc else b if db <= dc else c
                row[x] = (row[x] + predictor) & 255
        pixels.extend(row); previous = row
    return {'width': w, 'height': h, 'channels': channels, 'pixels': bytes(pixels)}


def _markers(image, color, *, marker_scale=1.0):
    w, h, c, data = (image[k] for k in ('width', 'height', 'channels', 'pixels'))
    mask = set()
    for i in range(w * h):
        j = i * c; r, g, b = data[j:j + 3]
        if c == 4 and data[j + 3] < 240: continue
        if (r >= 220 and g >= 200 and b <= 110) if color == 'yellow' else (r <= 145 and 70 <= g <= 185 and b >= 215 and b - r >= 90 and b - g >= 45):
            mask.add(i)
    if len(mask) > 200000:
        raise ValueError('Marker color occupancy exceeds bounded scope')
    seeds = set()
    threshold = 7 if color == 'yellow' else 5
    for i in mask:
        x, y = i % w, i // w
        if 0 < x < w - 1 and 0 < y < h - 1 and sum(i + dy * w + dx in mask for dy in (-1, 0, 1) for dx in (-1, 0, 1)) >= threshold:
            seeds.add(i)
    result = []
    while seeds:
        first = seeds.pop(); stack, component = [first], [first]
        while stack:
            i = stack.pop(); x, y = i % w, i // w
            for dy, dx in itertools.product((-1, 0, 1), repeat=2):
                j = i + dy * w + dx
                if 0 <= x + dx < w and 0 <= y + dy < h and j in seeds:
                    seeds.remove(j); stack.append(j); component.append(j)
        xs, ys = [i % w for i in component], [i // w for i in component]
        width, height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        filled = (len(component) >= 9 * marker_scale ** 2 and min(width, height) / max(width, height) >= .5) if color == 'yellow' else len(component) >= 3 * marker_scale ** 2
        if width <= 9 * marker_scale and height <= 9 * marker_scale and filled:
            result.append({'xy': [sum(xs) / len(xs), sum(ys) / len(ys)], 'dense_core_count': len(component),
                           'bbox': [min(xs), min(ys), max(xs), max(ys)]})
    if len(result) > 32:
        raise ValueError('Too many dense marker candidates')
    return sorted(result, key=lambda row: row['xy'])


def _points(rows, label, minimum):
    if type(rows) is not list or not minimum <= len(rows) <= 32:
        raise ValueError(label + ' count out of bounds')
    result = []; ids = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'id', 'xy_mm'} or type(row['id']) is not str or not row['id'] or len(row['id']) > 256 or row['id'] in ids:
            raise ValueError('Unique exact IDs and xy_mm required')
        xy = row['xy_mm']
        if type(xy) is not list or len(xy) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 10000000 for v in xy):
            raise ValueError('Finite bounded native XY required')
        ids.add(row['id']); result.append([float(v) for v in xy])
    return result


def _apply(model, xy):
    a, b, tx, ty, mirror = model; x, y = xy[0], xy[1] * mirror
    return [a * x - b * y + tx, b * x + a * y + ty]


def _fit(source, target, mirror):
    n = len(source); sx, sy = [sum(p[i] for p in source) / n for i in (0, 1)]; sy *= mirror
    px, py = [sum(p[i] for p in target) / n for i in (0, 1)]
    q = [(x - sx, y * mirror - sy) for x, y in source]; t = [(x - px, y - py) for x, y in target]
    denom = sum(x * x + y * y for x, y in q)
    if denom <= 1e-16: raise ValueError('Degenerate native anchors')
    a = sum(x * u + y * v for (x, y), (u, v) in zip(q, t)) / denom
    b = sum(x * v - y * u for (x, y), (u, v) in zip(q, t)) / denom
    return [a, b, px - a * sx + b * sy, py - b * sx - a * sy, mirror]


def _near(point, candidates, radius):
    return [i for i, marker in enumerate(candidates) if math.dist(point, marker['xy']) <= radius]


def _solve_projection(png_bytes, anchors, handles, target_handle_id, *, device_pixel_ratio=1.0,
                      residual_px=1.5, marker_radius_px=2.0, _image=None):
    """Unique similarity with optional reflection, plus independent blue target.

    DPR must come from the same capture metadata. Default 1 is an explicit
    assumption, never inferred from PNG DPI. This is pixel calibration only.
    """
    source = _points(anchors, 'anchors', 3); control = _points(handles, 'handles', 1)
    if type(device_pixel_ratio) not in (int, float) or not math.isfinite(device_pixel_ratio) or not 0 < device_pixel_ratio <= 8:
        raise ValueError('Actual capture DPR in (0,8] required')
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 3 for v in (residual_px, marker_radius_px)):
        raise ValueError('Pixel qualification thresholds must be in (0,3]')
    target_indices = [i for i, row in enumerate(handles) if row['id'] == target_handle_id]
    if len(target_indices) != 1: raise ValueError('One current target handle ID required')
    target_index = target_indices[0]
    i, j = max(itertools.combinations(range(len(source)), 2), key=lambda ij: math.dist(source[ij[0]], source[ij[1]]))
    diameter = math.dist(source[i], source[j])
    if diameter <= 1e-8 or max(abs((p[0] - source[i][0]) * (source[j][1] - source[i][1]) - (p[1] - source[i][1]) * (source[j][0] - source[i][0])) for p in source) <= diameter * diameter * 1e-8:
        raise ValueError('At least three noncollinear native anchors required')
    image = decode_png(png_bytes) if _image is None else _image
    yellow, blue = _markers(image, 'yellow'), _markers(image, 'blue')
    diagnostics = {'yellow_candidates': yellow, 'blue_candidates': blue, 'image_size_px': [image['width'], image['height']]}
    def reject(reason, **extra):
        raise ProjectionError(reason, 'No unique current anchor/blue-target projection; zero input', {**diagnostics, **extra})
    if len(yellow) < len(source) or not blue: return reject('VISIBLE_MARKERS_INCOMPLETE')
    solutions = {}
    for left, right in itertools.permutations(range(len(yellow)), 2):
        for mirror in (1, -1):
            model = _fit([source[i], source[j]], [yellow[left]['xy'], yellow[right]['xy']], mirror)
            neighbours = [_near(_apply(model, p), yellow, residual_px * 2) for p in source]
            if any(len(row) != 1 for row in neighbours): continue
            assignment = tuple(row[0] for row in neighbours)
            if len(set(assignment)) != len(source): continue
            model = _fit(source, [yellow[k]['xy'] for k in assignment], mirror)
            errors = [math.dist(_apply(model, p), yellow[k]['xy']) for p, k in zip(source, assignment)]
            if max(errors) > residual_px: continue
            visible = []
            for index, xy in enumerate(control):
                predicted = _apply(model, xy); matched = _near(predicted, blue, marker_radius_px)
                if len(matched) == 1:
                    visible.append((index, matched[0], predicted))
            hit = [row for row in visible if row[0] == target_index]
            if len(hit) != 1 or sum(row[1] == hit[0][1] for row in visible) != 1: continue
            solutions[(assignment, mirror)] = (model, errors, visible, hit[0])
    if len(solutions) != 1: return reject('NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION', solution_count=len(solutions))
    (assignment, mirror), (model, errors, visible, target) = next(iter(solutions.items()))
    a, b, tx, ty, _ = model; actual = blue[target[1]]['xy']; dpr = float(device_pixel_ratio)
    pairs = [{'id': row['id'], 'native_xy_mm': row['xy_mm'], 'marker_xy_px': yellow[k]['xy'], 'residual_px': e} for row, k, e in zip(anchors, assignment, errors)]
    return {'verified': True, 'unique': True, 'input_calls': 0, 'native_dispatch_authorized': False,
            'linear': [[a / dpr, -b * mirror / dpr], [b / dpr, a * mirror / dpr]], 'translation': [tx / dpr, ty / dpr],
            'target_xy_px': [v / dpr for v in actual], 'anchor_pairs': pairs,
            'anchor_max_error_px': max(errors) / dpr, 'target_marker_error_px': math.dist(target[2], actual) / dpr,
            'matrix_native_mm_to_png_px': [[a, -b * mirror, tx], [b, a * mirror, ty]],
            'scale_px_per_mm': math.hypot(a, b), 'rotation_degrees': math.degrees(math.atan2(b, a)), 'y_reflected': mirror == -1,
            'maximum_anchor_residual_px': max(errors), 'rms_anchor_residual_px': math.sqrt(sum(e * e for e in errors) / len(errors)),
            'anchor_bijection': pairs,
            'visible_handle_bijection': [{'id': handles[index]['id'], 'predicted_xy_px': prediction, 'marker_xy_px': blue[k]['xy']} for index, k, prediction in visible],
            'target_handle_id': target_handle_id, 'target_predicted_xy_px': target[2], 'target_blue_marker_xy_px': actual,
            'target_marker_prediction_error_px': math.dist(target[2], actual), 'current_logical_xy': [v / dpr for v in actual],
            'device_pixel_ratio_used': dpr, 'absolute_0p002_mm_accuracy_verified': False, **diagnostics}


def solve_projection(png_bytes, anchors, handles, target_handle_id, **options):
    """Return Root's logical-pixel linear/translation/target contract or reject."""
    try:
        return _solve_projection(png_bytes, anchors, handles, target_handle_id, **options)
    except ProjectionError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, struct.error, zlib.error) as exc:
        raise ProjectionError('INVALID_PROJECTION_INPUT', str(exc)) from exc


def track_marker(png_bytes, expected_xy_px, radius_px=8, device_pixel_ratio=1, allowed_colors=('blue',)):
    """Local continuity observation only; does not identify a handle or scale.

    XY/radius/output use logical pixels. Root must supply actual capture DPR
    and independently establish same-ID continuity/native geometry invariance.
    No caller-supplied verification flag or global nearest-color search exists.
    """
    try:
        if type(expected_xy_px) not in (list, tuple) or len(expected_xy_px) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in expected_xy_px):
            raise ValueError('Finite expected logical XY required')
        if type(radius_px) not in (int, float) or not math.isfinite(radius_px) or not 1 <= radius_px <= 32:
            raise ValueError('Local radius must be in [1,32] logical pixels')
        if type(device_pixel_ratio) not in (int, float) or not math.isfinite(device_pixel_ratio) or not 0 < device_pixel_ratio <= 8:
            raise ValueError('Actual capture DPR in (0,8] required')
        if type(allowed_colors) not in (tuple, list) or not 1 <= len(allowed_colors) <= 2 or any(type(c) is not str or c not in ('blue', 'yellow') for c in allowed_colors) or len(set(allowed_colors)) != len(allowed_colors):
            raise ValueError('Only explicit blue/yellow marker colors are supported')
        image = decode_png(png_bytes); w, h, channels = (image[k] for k in ('width', 'height', 'channels'))
        dpr = float(device_pixel_ratio); ex, ey = [v * dpr for v in expected_xy_px]; radius = radius_px * dpr
        if not 0 <= ex < w or not 0 <= ey < h:
            raise ValueError('Expected point is outside this capture')
        x0, y0 = max(0, math.floor(ex - radius)), max(0, math.floor(ey - radius))
        x1, y1 = min(w, math.ceil(ex + radius) + 1), min(h, math.ceil(ey + radius) + 1)
        crop = {'width': x1 - x0, 'height': y1 - y0, 'channels': channels,
                'pixels': b''.join(image['pixels'][(y * w + x0) * channels:(y * w + x1) * channels] for y in range(y0, y1))}
        diagnostics = {'expected_xy_px': list(expected_xy_px), 'radius_px': radius_px,
                       'search_bbox_px': [x0 / dpr, y0 / dpr, (x1 - 1) / dpr, (y1 - 1) / dpr], 'allowed_colors': list(allowed_colors)}
        candidates = []
        for color in allowed_colors:
            for marker in _markers(crop, color, marker_scale=dpr):
                bx0, by0, bx1, by1 = marker['bbox']; bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
                center = [(bx0 + bx1) / 2 + x0, (by0 + by1) / 2 + y0]
                fill = marker['dense_core_count'] / (bw * bh)
                if (math.dist(center, [ex, ey]) > radius or min(bw, bh) / max(bw, bh) < .5 or fill < .45
                        or bx0 < 2 or by0 < 2 or bx1 >= crop['width'] - 2 or by1 >= crop['height'] - 2):
                    continue
                candidates.append((color, marker, center, fill))
        diagnostics['candidate_count'] = len(candidates)
        if len(candidates) != 1:
            raise ProjectionError('LOCAL_MARKER_NOT_UNIQUE', 'Expected neighborhood has no unique filled dot; zero input', diagnostics)
        color, marker, center, fill = candidates[0]
        hit = [v / dpr for v in center]; centroid = [(marker['xy'][0] + x0) / dpr, (marker['xy'][1] + y0) / dpr]
        bbox = [(marker['bbox'][0] + x0) / dpr, (marker['bbox'][1] + y0) / dpr,
                (marker['bbox'][2] + x0) / dpr, (marker['bbox'][3] + y0) / dpr]
        trace = {**diagnostics, 'color': color, 'hit_xy_px': hit, 'centroid_xy_px': centroid, 'bbox_px': bbox,
                 'offset_px': [a - b for a, b in zip(hit, expected_xy_px)],
                 'quality': {'dense_core_count': marker['dense_core_count'], 'dense_bbox_fill': fill},
                 'device_pixel_ratio_used': dpr}
        return {'verified': True, 'unique': True, 'xy_px': hit, **trace, 'trace_record': trace,
                'input_calls': 0, 'native_dispatch_authorized': False,
                'handle_identity_verified': False, 'mm_scale_reconstructed': False}
    except ProjectionError:
        raise
    except (ValueError, TypeError, OverflowError, struct.error, zlib.error) as exc:
        raise ProjectionError('INVALID_TRACK_INPUT', str(exc)) from exc
