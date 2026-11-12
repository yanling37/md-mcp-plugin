"""Fit local viewport projection using actual selected-outline pixels and OBJ edges."""
from collections import Counter
import math

from .curve_projection import decode_png
from .world_projection import calibrate_projection, project_world


def _yellow_offset(image, center, tangent, normal):
    width, height, channels = (image[key] for key in ('width', 'height', 'channels'))
    data = image['pixels']
    x, y = center
    support = []
    for row in range(max(0, math.floor(y - 4)), min(height, math.ceil(y + 4))):
        for column in range(max(0, math.floor(x - 4)), min(width, math.ceil(x + 4))):
            at = (row * width + column) * channels
            red, green, blue = data[at:at + 3]
            if red < 224 or green < 210 or blue > 90 or channels == 4 and data[at + 3] < 240:
                continue
            dx, dy = column - x, row - y
            across = dx * normal[0] + dy * normal[1]
            along = dx * tangent[0] + dy * tangent[1]
            if abs(along) <= .82 and abs(across) <= 3:
                support.append((across, 1 - blue / 255))
    if not support or max(value for value, _ in support) - min(value for value, _ in support) > 2:
        return None
    return math.fsum(value * weight for value, weight in support) / math.fsum(weight for _, weight in support)


def calibrate_selected_outline(pose, group, png, focal_seed_px):
    """Fit intrinsics from two disjoint sets of measured boundary-edge normals."""
    if type(focal_seed_px) not in (int, float) or not 1 <= focal_seed_px <= 100_000:
        raise ValueError('A positive finite physical-pixel focal seed is required')
    image = decode_png(png)
    width, height = image['width'], image['height']
    positions = group['positions_mm']
    faces = group['faces']
    if not 3 <= len(positions) <= 100_000 or not 1 <= len(faces) <= 200_000:
        raise ValueError('Selected outline mesh exceeds its size budget')
    edges = Counter(tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1]))
    boundary = sorted(edge for edge, count in edges.items() if count == 1)
    if len(boundary) > 8192:
        raise ValueError('Selected outline edge count exceeds its fit budget')
    seed = {**pose, 'focal_px': focal_seed_px, 'center_px': [(width - 1) / 2, (height - 1) / 2]}
    projected = {index: project_world(seed, positions[index]) for index in {i for edge in boundary for i in edge}}
    partitions = ([], [])
    observed_pixels = set()
    for a, b in boundary:
        u, v = projected[a], projected[b]
        dx, dy = v[0] - u[0], v[1] - u[1]
        length = math.hypot(dx, dy)
        if length < 1.5:
            continue
        tangent = dx / length, dy / length
        normal = -tangent[1], tangent[0]
        point = [(x + y) / 2 for x, y in zip(positions[a], positions[b])]
        center = project_world(seed, point)
        offset = _yellow_offset(image, center, tangent, normal)
        if offset is None:
            continue
        pixel = [value + offset * component for value, component in zip(center, normal)]
        bucket = tuple(round(value) for value in pixel)
        if bucket in observed_pixels or not 0 <= pixel[0] < width or not 0 <= pixel[1] < height:
            continue
        observed_pixels.add(bucket)
        partition = (math.floor(center[0] / 6) + math.floor(center[1] / 6)) % 2
        partitions[partition].append({'id': f'{a}-{b}', 'world_xyz_mm': point,
                                      'pixel_xy': pixel, 'normal_xy': list(normal)})
    if min(map(len, partitions)) < 12:
        raise ValueError('The selected outline did not provide enough distinct fit and holdout edges')
    result = calibrate_projection(pose, [width, height], *partitions, max_error_px=.7)
    result.update(boundary_edge_count=len(boundary), measured_edge_count=len(observed_pixels),
                  focal_seed_px=focal_seed_px, verification_scope='local selected boundary normal residuals')
    return result
