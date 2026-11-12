"""Read-only calibration from CMT pose and measured viewport correspondences."""
from __future__ import annotations

import hashlib
import io
import math
import zipfile

from .cloth import _camera, _cross, _dot, _number, _text, _vector


_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
_MAX_MEMBER_BYTES = 64 * 1024
_CMT_BASIS_AXIS_L2_LIMIT = 4e-6
_POSE_KEYS = ("right", "up", "forward", "camera_center_world_mm")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _dimensions(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(dimension) is not int or dimension <= 0 for dimension in value)):
        raise ValueError("Two positive integer viewport dimensions are required")
    return list(value)


def _validated_pose(pose):
    if not isinstance(pose, dict):
        raise ValueError("Camera pose must be a dictionary")
    axes_and_center = _camera({**pose, "focal_px": 1, "center_px": [0, 0], "viewport_size_px": [1, 1]})[:4]
    return dict(zip(_POSE_KEYS, axes_and_center))


def camera_pose_from_matrix(matrix4x4):
    """Convert an exported CMT matrix into a pose with bounded basis correction."""
    if not isinstance(matrix4x4, (list, tuple)) or len(matrix4x4) != 4:
        raise ValueError("CMT matrix must have four rows")
    rows = [_vector(row, 4, "CMT matrix row") for row in matrix4x4]
    if rows[3] != [0.0, 0.0, 0.0, 1.0]:
        raise ValueError("CMT homogeneous row differs")
    raw_right, raw_up, raw_forward, center = [list(column) for column in zip(*rows[:3])]
    raw_axes = (raw_right, raw_up, raw_forward)
    lengths = [math.hypot(*axis) for axis in raw_axes]
    limit = _CMT_BASIS_AXIS_L2_LIMIT
    if any(abs(length - 1) > limit for length in lengths):
        raise ValueError("CMT basis scale exceeds the correction limit")
    determinant = _dot(_cross(raw_right, raw_up), raw_forward)
    if determinant <= 0:
        raise ValueError("CMT basis must be right-handed")

    right = [value / lengths[0] for value in raw_right]
    up_along_right = _dot(raw_up, right)
    up = [value - up_along_right * direction for value, direction in zip(raw_up, right)]
    up_length = math.hypot(*up)
    if up_length == 0:
        raise ValueError("CMT up direction is degenerate")
    up = [value / up_length for value in up]
    forward = _cross(right, up)
    axes = (right, up, forward)
    corrections = [math.dist(original, corrected) for original, corrected in zip(raw_axes, axes)]
    maximum = max(corrections)
    if maximum > limit:
        raise ValueError("CMT basis direction exceeds the correction limit")

    pose = _validated_pose(dict(zip(_POSE_KEYS, (*axes, center))))
    pose["basis_correction"] = {
        "method": "normalize_right_orthogonalize_up_cross_forward",
        "applied": maximum > 0,
        "axis_l2_delta": dict(zip(_POSE_KEYS[:3], corrections)),
        "max_axis_l2_delta": maximum,
        "axis_l2_limit": limit,
        "raw_basis_determinant": determinant,
        "cmt_basis_decimal_places": None,
    }
    return pose


def camera_pose_from_cmt(data):
    """Decode the row-major camera-to-world matrix used by exported Type 0 CMT."""
    if type(data) is not bytes or not 0 < len(data) <= _MAX_MEMBER_BYTES:
        raise ValueError("Bounded CMT bytes are required")
    header = data.decode("ascii").strip().split("|^^|")
    if len(header) != 4 or header[:2] != ["Version: 1", "Type: 0"] or header[3]:
        raise ValueError("Unsupported CMT format")
    fields = header[2].split()
    if len(fields) != 16:
        raise ValueError("CMT matrix must have 16 components")
    matrix = [float(field) for field in fields]
    pose = camera_pose_from_matrix([matrix[index:index + 4] for index in range(0, 16, 4)])
    # Preserve the serialized precision when the basis uses a uniform decimal
    # format. The correction bound also covers drift present before serialization.
    parts = [fields[row * 4 + column].partition(".") for row in range(3) for column in range(3)]
    if all(dot and whole.lstrip("+-").isdigit() and fraction.isdigit() for whole, dot, fraction in parts):
        precision = {len(fraction) for _, _, fraction in parts}
        if len(precision) == 1:
            pose["basis_correction"]["cmt_basis_decimal_places"] = precision.pop()
    return {**pose, "cmt_sha256": _sha256(data)}


def camera_pose_from_archive(data):
    """Read a ZCMR camera pose and bind its CPT bytes as opaque provenance."""
    if type(data) is not bytes or not 0 < len(data) <= _MAX_ARCHIVE_BYTES:
        raise ValueError("Bounded ZCMR archive bytes are required")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not 2 <= len(entries) <= 16:
                raise ValueError("ZCMR member count differs")
            pair = []
            for extension in (".cmt", ".cpt"):
                matching = [entry for entry in entries if entry.filename.lower().endswith(extension)]
                if len(matching) != 1 or not 0 < matching[0].file_size <= _MAX_MEMBER_BYTES:
                    raise ValueError(f"ZCMR must contain one bounded {extension} member")
                pair.append(matching[0])
            first, second = [entry.filename.rsplit(".", 1)[0] for entry in pair]
            if first != second:
                raise ValueError("ZCMR pose and parameter member names differ")
            cmt, cpt = (archive.read(entry) for entry in pair)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
        raise ValueError("Cannot read ZCMR camera members") from exc
    return {
        **camera_pose_from_cmt(cmt), "cpt_sha256": _sha256(cpt), "archive_sha256": _sha256(data),
        "cpt_pixel_intrinsics_decoded": False,
    }


def _normalized_coordinates(pose, world_xyz_mm):
    xyz = _vector(world_xyz_mm, 3, "world_xyz_mm")
    offset = [value - origin for value, origin in zip(xyz, pose["camera_center_world_mm"])]
    depth = _number(-_dot(offset, pose["forward"]), "camera depth")
    if depth <= 0:
        raise ValueError("Point lies at or behind the camera")
    coordinates = [_dot(offset, pose["right"]) / depth, -_dot(offset, pose["up"]) / depth]
    return _vector(coordinates, 2, "normalized camera coordinates"), depth


def _intrinsics(camera):
    if "focal_x_px" in camera or "focal_y_px" in camera:
        focal = _vector([camera.get("focal_x_px"), camera.get("focal_y_px")], 2, "axis focals")
    else:
        focal = [_number(camera.get("focal_px"), "focal_px")] * 2
    if min(focal) <= 0:
        raise ValueError("Focal lengths must be positive")
    return focal, _vector(camera.get("center_px"), 2, "center_px")


def project_world(camera, world_xyz_mm):
    """Project one world point into the calibration's viewport pixel coordinates."""
    normalized, _ = _normalized_coordinates(_validated_pose(camera), world_xyz_mm)
    focal, principal = _intrinsics(camera)
    return _vector([c + f * value for c, f, value in zip(principal, focal, normalized)], 2, "projected pixel")


def _sample_rows(samples, pose, dimensions):
    if not isinstance(samples, (list, tuple)) or not 3 <= len(samples) <= 4096:
        raise ValueError("Each partition needs 3 to 4096 measured samples")
    rows, observed, depths, normals = [], [], [], []
    identities, supports, worlds, pixels = set(), set(), set(), set()
    for sample in samples:
        identifier = _text(sample["id"], "sample id")
        support = _text(sample.get("support_id", identifier), "sample support id")
        world = _vector(sample["world_xyz_mm"], 3, "sample world_xyz_mm")
        pixel = _vector(sample["pixel_xy"], 2, "sample pixel_xy")
        if identifier in identities or tuple(world) in worlds or tuple(pixel) in pixels:
            raise ValueError("Repeated correspondence in calibration partition")
        if any(not 0 <= value < size for value, size in zip(pixel, dimensions)):
            raise ValueError("Measured pixel lies outside the bound viewport")
        (x, y), depth = _normalized_coordinates(pose, world)
        normal = sample.get("normal_xy")
        if normal is None:
            rows.extend(([x, 0, 1, 0], [0, y, 0, 1]))
            observed.extend(pixel)
        else:
            nx, ny = _vector(normal, 2, "measurement normal_xy")
            if abs(math.hypot(nx, ny) - 1.0) > 1e-6:
                raise ValueError("Measurement normal must be a unit vector")
            rows.append([nx * x, ny * y, nx, ny])
            observed.append(nx * pixel[0] + ny * pixel[1])
        normals.append(normal is not None)
        depths.append(depth)
        identities.add(identifier)
        supports.add(support)
        worlds.add(tuple(world))
        pixels.add(tuple(pixel))
    return rows, observed, depths, normals, (identities, supports, worlds, pixels)


def _least_squares(rows, observed):
    """Small column-scaled QR with a second orthogonalization pass."""
    columns = list(zip(*rows))
    scales = [math.hypot(*column) for column in columns]
    if len(rows) < len(columns) or min(scales) == 0:
        raise ValueError("Calibration partition does not constrain the camera intrinsics")
    basis = []
    triangular = [[0.0] * len(columns) for _ in columns]
    for j, (column, scale) in enumerate(zip(columns, scales)):
        residual = [value / scale for value in column]
        for _ in range(2):
            for i, direction in enumerate(basis):
                coefficient = math.fsum(a * b for a, b in zip(residual, direction))
                triangular[i][j] += coefficient
                residual = [a - coefficient * b for a, b in zip(residual, direction)]
        length = math.hypot(*residual)
        if length < 1e-6:
            raise ValueError("Calibration partition does not constrain the camera intrinsics")
        triangular[j][j] = length
        basis.append([value / length for value in residual])
    result = [math.fsum(a * b for a, b in zip(direction, observed)) for direction in basis]
    for i in reversed(range(len(result))):
        result[i] = (result[i] - math.fsum(triangular[i][j] * result[j] for j in range(i + 1, len(result)))) / triangular[i][i]
    return [_number(value / scale, "fitted parameter") for value, scale in zip(result, scales)]


def _metrics(partition, parameters):
    rows, observed, depths, normals, _ = partition
    residuals = iter([math.fsum(a * b for a, b in zip(row, parameters)) - value for row, value in zip(rows, observed)])
    errors = [abs(next(residuals)) if normal else math.hypot(next(residuals), next(residuals)) for normal in normals]
    return {
        "count": len(errors), "normal_only_count": sum(normals), "max_error_px": max(errors),
        "rms_error_px": math.sqrt(math.fsum(error ** 2 for error in errors) / len(errors)),
        "depth_range_mm": [min(depths), max(depths)],
    }


def calibrate_projection(pose, viewport_size_px, fit_samples, holdout_samples, *, max_error_px):
    """Fit axis focals and principal point from independent, frame-bound measurements.

    A sample has id, world_xyz_mm and pixel_xy. Supply normal_xy for a wire ridge
    that measures only the component along its unit normal. Samples sharing an
    edge or marker must carry the same support_id. Pixel coordinates, viewport
    and error tolerance use one raster or one logical coordinate system.
    """
    basis = _validated_pose(pose)
    dimensions = _dimensions(viewport_size_px)
    limit = _number(max_error_px, "max_error_px")
    if limit <= 0:
        raise ValueError("Positive reprojection error limit is required")
    fit = _sample_rows(fit_samples, basis, dimensions)
    holdout = _sample_rows(holdout_samples, basis, dimensions)
    if any(a & b for a, b in zip(fit[-1], holdout[-1])):
        raise ValueError("Fit and holdout must have independent samples and supports")
    parameters = _least_squares(fit[0], fit[1])
    # Verify that the held-out measurements can detect changes in every axis
    # parameter. Their coordinates never determine the returned intrinsics.
    _least_squares(holdout[0], [0.0] * len(holdout[0]))
    fx, fy, cx, cy = parameters
    if min(fx, fy) <= 0:
        raise ValueError("Measured correspondences imply a nonpositive focal length")
    fit_metrics, holdout_metrics = _metrics(fit, parameters), _metrics(holdout, parameters)
    verified = max(fit_metrics["max_error_px"], holdout_metrics["max_error_px"]) <= limit
    common = {**basis, "viewport_size_px": dimensions}
    shared, shared_cx, shared_cy = _least_squares([[x + y, nx, ny] for x, y, nx, ny in fit[0]], fit[1])
    shared_parameters = [shared, shared, shared_cx, shared_cy]
    shared_fit, shared_holdout = _metrics(fit, shared_parameters), _metrics(holdout, shared_parameters)
    shared_verified = shared > 0 and max(shared_fit["max_error_px"], shared_holdout["max_error_px"]) <= limit
    return {
        "verified": verified, "max_error_px": limit, "fit": fit_metrics, "holdout": holdout_metrics,
        "camera": {**common, "center_px": [cx, cy], "focal_x_px": fx, "focal_y_px": fy},
        "shared_focal_fit": shared_fit, "shared_focal_holdout": shared_holdout,
        "cloth_camera": (
            {**common, "center_px": [shared_cx, shared_cy], "focal_px": shared}
            if verified and shared_verified else None
        ),
    }


def project_ring_samples(camera, pivot_world_mm, axis_world, radius_mm, start_radial_world):
    """Predict ring points for supplied world geometry. Independent image evidence is required for a hit."""
    pivot = _vector(pivot_world_mm, 3, "pivot_world_mm")
    axis = _vector(axis_world, 3, "axis_world")
    radial = _vector(start_radial_world, 3, "start_radial_world")
    axis_length, radial_length = math.hypot(*axis), math.hypot(*radial)
    radius = _number(radius_mm, "radius_mm")
    if not math.isfinite(axis_length) or not math.isfinite(radial_length) or min(axis_length, radial_length, radius) <= 0:
        raise ValueError("Nonzero ring axis, radial direction and positive radius are required")
    axis = [value / axis_length for value in axis]
    radial = [value / radial_length for value in radial]
    if abs(_dot(axis, radial)) > 1e-6:
        raise ValueError("Ring start direction must be perpendicular to its axis")
    tangent = _cross(axis, radial)

    def sample(degrees):
        angle = math.radians(degrees)
        point = [
            origin + radius * (math.cos(angle) * x + math.sin(angle) * y)
            for origin, x, y in zip(pivot, radial, tangent)
        ]
        return project_world(camera, point)

    return {
        "projected_cardinals_px": [sample(degrees) for degrees in (0, 90, 180, 270)],
        "predicted_check_sample": {"angle_degrees": 45.0, "viewport_px": sample(45)},
        "observed_ring_verified": False,
    }
