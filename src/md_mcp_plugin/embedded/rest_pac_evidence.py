"""Read saved Rest evidence from the PAC layout qualified on MD 2026.1.237."""

from __future__ import annotations

import hashlib
import io
import math
import re
import struct
import zipfile
import zlib


class PacAuditError(ValueError):
    pass


PROFILE = "md-pac-serialized-rest/1"
MAX_PROJECT_BYTES = 64 * 1024 * 1024
_MAX_PAC_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 512
_MAX_PATTERNS = 64
_UUID = re.compile(r"[a-zA-Z0-9]{6}")
_NAME = re.compile(r"[ -~]{1,128}")
_DYNAMICS = b"mapPatternDynamics"
_REST_FIELD = b"bRestShapeFixed"
_FLAG = re.compile(rb"fSolidifyIntensity_v2.{4}bRestShapeFixed([\x00\x01])fSelfAvoidance", re.DOTALL)
_SOLIDIFIED = re.compile(rb"bActive[\x00\x01]bSolidified([\x00\x01])bFreeze")
_POSITION_FIELD = b"baSolidifiedPos3D"
_POSITION_PREFIX = b"listSteamStroke\x00\x00\x00\x00" + _POSITION_FIELD
_OWNER = re.compile(rb"bHalfSymmetryAligned[\x00\x01]qsUuid([a-zA-Z0-9]{6})qsUuidUTF8\1qsUniqueIdUTF8")
_ANNOTATION = b"mapAnnoForPatternName"
_XYZ = struct.Struct("<fff")


def _member(data: bytes, suffix: str, byte_limit: int) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ENTRIES:
                raise PacAuditError("Container exceeds the member limit")
            matches = [entry for entry in entries if entry.filename.lower().endswith(suffix)]
            if len(matches) != 1:
                raise PacAuditError(f"Expected exactly one {suffix} member")
            entry = matches[0]
            if entry.flag_bits & 1 or not 0 < entry.file_size <= byte_limit:
                raise PacAuditError(f"Unusable {suffix} member size or encryption flag")
            with archive.open(entry) as stream:
                member = stream.read(byte_limit + 1)
            if len(member) != entry.file_size:
                raise PacAuditError(f"Inconsistent {suffix} member length")
            return entry.filename, member
    except (zipfile.BadZipFile, EOFError, NotImplementedError, RuntimeError, zlib.error) as exc:
        raise PacAuditError(f"Unusable {suffix} container") from exc


def _patterns(pac: bytes, expected_patterns: dict[str, str]) -> list[dict]:
    if not isinstance(expected_patterns, dict) or not 0 < len(expected_patterns) <= _MAX_PATTERNS:
        raise PacAuditError("Expected the complete SDK UUID-to-name inventory")
    for uuid, name in expected_patterns.items():
        if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
            raise PacAuditError("SDK UUID does not match the audited PAC profile")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise PacAuditError("SDK pattern name does not match the audited PAC profile")

    # The proven record order is optional XYZ, dynamics, flags, UUID, then annotation.
    # Require a complete set of records and corroborate both UUID and pattern name against the SDK.
    count = len(expected_patterns)
    dynamics = [match.start() for match in re.finditer(_DYNAMICS, pac)]
    flags = list(_FLAG.finditer(pac))
    owners = list(_OWNER.finditer(pac))
    annotations = [match.start() for match in re.finditer(_ANNOTATION, pac)]
    if (pac.count(_REST_FIELD), len(dynamics), len(flags), len(owners), len(annotations)) != (count,) * 5:
        raise PacAuditError("PAC field and owner counts do not match the SDK inventory")

    results = []
    seen = set()
    previous_annotation = -1
    position_offsets = set()
    for start, flag, owner, annotation in zip(dynamics, flags, owners, annotations, strict=True):
        if not (previous_annotation < start < flag.start() < flag.end() <= owner.start() < owner.end() <= annotation):
            raise PacAuditError("PAC field, owner and name records do not interleave as expected")
        if flag.start() - start > 512 or annotation - owner.end() > 512:
            raise PacAuditError("PAC fields lie outside the audited record profile")
        uuid = owner.group(1).decode("ascii")
        if uuid in seen or uuid not in expected_patterns:
            raise PacAuditError("PAC pattern UUID is unexpected or duplicated")
        name = expected_patterns[uuid]
        escaped = re.escape(name.encode("ascii"))
        name_pattern = re.compile(rb"qsText" + escaped + rb"bFixedForAutoArrange[\x00\x01]qsTextUTF8"
                                  + escaped + rb"uiFontSize")
        names = list(name_pattern.finditer(pac, annotation, annotation + 512))
        if len(names) != 1:
            raise PacAuditError("PAC pattern name does not uniquely corroborate the SDK owner")
        solidify = list(_SOLIDIFIED.finditer(pac, start, flag.start()))
        if len(solidify) != 1:
            raise PacAuditError("PAC Solidify state is not uniquely bound to the SDK owner")

        position_field = pac.find(_POSITION_PREFIX, max(0, previous_annotation), start)
        row = {
            "pattern_uuid": uuid,
            "pattern_name": name,
            "serialized_flag_value": flag.group(1) == b"\x01",
            "flag_value_offset_in_pac": flag.start(1),
            "solidify_enabled": solidify[0].group(1) == b"\x01",
            "position_field_present": position_field >= 0,
            "position_count": 0,
            "position_array_offset_in_pac": None,
            "position_array_bytes": 0,
            "position_array_sha256": None,
            "serialized_positions_mm": None,
        }
        if position_field >= 0:
            positions_start = position_field + len(_POSITION_PREFIX)
            position_offsets.add(positions_start - len(_POSITION_FIELD))
            raw = pac[positions_start:start]
            if not raw or len(raw) % _XYZ.size:
                raise PacAuditError("PAC position array length is not a positive number of XYZ float32 triples")
            positions = tuple(_XYZ.iter_unpack(raw))
            if any(not all(map(math.isfinite, point)) for point in positions):
                raise PacAuditError("PAC position array contains non-finite coordinates")
            row.update(position_count=len(positions), position_array_offset_in_pac=positions_start,
                       position_array_bytes=len(raw), position_array_sha256=hashlib.sha256(raw).hexdigest(),
                       serialized_positions_mm=positions)
        seen.add(uuid)
        previous_annotation = names[0].end()
        results.append(row)
    if position_offsets != {match.start() for match in re.finditer(_POSITION_FIELD, pac)}:
        raise PacAuditError("PAC position fields do not uniquely bind to the SDK pattern records")
    return results


def inspect_project_bytes(data: bytes, expected_sha256: str, expected_patterns: dict[str, str]) -> dict:
    """Decode ZPAC or ZPRJ bytes without accessing files or the live solver.

    The result reports separate saved Rest and Solidify flags. ``serialized_positions_mm``
    is the saved ``baSolidifiedPos3D`` array, or None. The caller must independently bind
    vertex order and compare it to the target geometry before certifying a native write.
    """
    if not isinstance(data, bytes) or len(data) > MAX_PROJECT_BYTES:
        raise PacAuditError("Expected project bytes within the audit limit")
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != expected_sha256:
        raise PacAuditError("Project SHA-256 differs from the expected disposable snapshot")

    zpac_member = None
    if data.startswith(b" ZPRJ       CLO "):
        zpac_member, data = _member(data, ".zpac", MAX_PROJECT_BYTES)
    if not data.startswith(b" ZPAC       CLO "):
        raise PacAuditError("Unrecognized MD garment container header")
    pac_member, pac = _member(data, ".pac", _MAX_PAC_BYTES)
    return {
        "profile": PROFILE,
        "container": "zprj" if zpac_member else "zpac",
        "project_sha256": sha256,
        "zpac_member": zpac_member,
        "pac_member": pac_member,
        "pac_sha256": hashlib.sha256(pac).hexdigest(),
        "field": _REST_FIELD.decode("ascii"),
        "patterns": _patterns(pac, expected_patterns),
        "solver_rest_values_read": False,
        "solver_rest_write_verified": False,
    }
