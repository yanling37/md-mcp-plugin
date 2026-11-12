"""Resolve outline corners and ensure the current baseline has no old blue vertex."""
import math

from .cloth_meshbox_uv_fit import _at, _dot, _profile, _sub


def _corner(image, piece, coarse, index, style):
    lines = []
    for a, b in piece.outline:
        if index not in (a, b):
            continue
        endpoint = b if a == index else a
        start, end = coarse[index], coarse[endpoint]
        dx, dy = _sub(end, start)
        length = math.hypot(dx, dy)
        if length < 5:
            return None
        normal = -dy / length, dx / length
        profiles = [_profile(image, _at(start, end, t), normal, style) for t in (.32, .55, .78)]
        centers = [p[0] for p in profiles if p is not None]
        if len(centers) < 2:
            return None
        dx, dy = _sub(centers[-1], centers[0])
        length = math.hypot(dx, dy)
        if length < 1.5:
            return None
        n = -dy / length, dx / length
        offset = _dot(n, centers[0])
        if any(abs(_dot(n, p) - offset) > .5 for p in centers):
            return None
        lines.append((n, offset))
    if len(lines) != 2:
        return None
    ((a, b), q), ((c, d), r) = lines
    determinant = a * d - b * c
    if abs(determinant) < .45:
        return None
    point = (d * q - b * r) / determinant, (a * r - c * q) / determinant
    return point if math.dist(point, coarse[index]) <= 3 else None


def _no_old_blue(image, diagnostics):
    width, height, channels, pixels = (image[k] for k in ('width', 'height', 'channels', 'pixels'))
    for row in diagnostics.values():
        left, top, right, bottom = row['bounds_physical_px']
        for y in range(max(0, math.floor(top - 3)), min(height, math.ceil(bottom + 3))):
            for x in range(max(0, math.floor(left - 3)), min(width, math.ceil(right + 3))):
                at = (y * width + x) * channels
                r, g, b = pixels[at:at + 3]
                if b >= 160 and b - r >= 70 and b - g >= 20 and (channels == 3 or pixels[at + 3] == 255):
                    raise ValueError('The fresh 2D baseline already contains a blue point near the cloth')
