"""Capture a private ZPAC from the documented MD garment export API."""

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
import io
import os
from pathlib import Path
import stat
import tempfile
import zipfile
import zlib

from md_mcp_plugin.errors import PluginError


_HEADER = b' ZPAC       CLO '
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_PAC_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 512


@dataclass(frozen=True)
class ArchiveEvidence:
    payload: bytes = field(repr=False)
    sha256: str
    byte_count: int


def _error(code, message, *, attempted=False, uncertain=False, component=None):
    details = {'export_attempted': attempted, 'effects_started': uncertain, 'requires_recovery': uncertain}
    if component is not None:
        details['component'] = component
    return PluginError(code, message, **details)


def _check_bindings(expected, readers, *, attempted):
    bindings = ('scene', 'pose', 'scope', 'selection', 'scene')
    for name, value, read in zip(bindings, (*expected, expected[0]), (*readers, readers[0]), strict=True):
        try:
            matches = read() == value
        except Exception:
            matches = False
        if not matches:
            code = 'REST_ARCHIVE_STATE_UNCERTAIN' if attempted else 'REST_ARCHIVE_BINDING_MISMATCH'
            raise _error(code, f'The bound {name} could not be confirmed around garment export',
                         attempted=attempted, uncertain=attempted, component=name)


def _is_plain(info, file_type):
    return file_type(info.st_mode) and not (
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _same_object(first, second):
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _verify_pac(payload):
    if not payload.startswith(_HEADER):
        raise ValueError('Unexpected ZPAC header')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        pac_members = [member for member in members if member.filename.lower().endswith('.pac')]
        if len(members) > _MAX_MEMBERS or len(pac_members) != 1:
            raise ValueError('Expected a single garment PAC member')
        member = pac_members[0]
        if member.flag_bits & 1 or not 0 < member.file_size <= _MAX_PAC_BYTES:
            raise ValueError('Invalid garment PAC member size or encryption')
        with archive.open(member) as stream:
            if len(stream.read(_MAX_PAC_BYTES + 1)) != member.file_size:
                raise ValueError('Garment PAC member length differs')


def _read_evidence(path, directory_before, returned):
    if type(returned) is str and returned == '':
        raise _error('REST_ARCHIVE_NO_OUTPUT', 'Garment export reported no output', attempted=True)
    if type(returned) is not str or '\0' in returned or Path(returned) != path:
        raise _error('REST_ARCHIVE_INVALID_PATH', 'Garment export did not return the requested private path',
                     attempted=True, uncertain=True)
    try:
        directory_after = path.parent.lstat()
    except OSError:
        directory_after = None
    if directory_after is None or not _is_plain(directory_after, stat.S_ISDIR) or not _same_object(directory_before, directory_after):
        raise _error('REST_ARCHIVE_INVALID_PATH', 'The private garment export directory changed',
                     attempted=True, uncertain=True)
    try:
        info = path.lstat()
        if not _is_plain(info, stat.S_ISREG) or info.st_nlink != 1:
            raise _error('REST_ARCHIVE_INVALID_PATH', 'Garment output is not a private regular file',
                         attempted=True, uncertain=True)
        if not 0 < info.st_size <= _MAX_ARCHIVE_BYTES:
            raise ValueError('Garment output size is outside the archive bounds')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if not _same_object(info, opened):
                raise _error('REST_ARCHIVE_INVALID_PATH', 'The private garment output changed while opening',
                             attempted=True, uncertain=True)
            payload = stream.read(_MAX_ARCHIVE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(payload) != info.st_size or (after.st_mtime_ns, after.st_size) != (opened.st_mtime_ns, opened.st_size):
            raise ValueError('Garment output changed while reading')
        _verify_pac(payload)
    except FileNotFoundError:
        raise _error('REST_ARCHIVE_NO_OUTPUT', 'Garment export did not create the requested archive', attempted=True) from None
    except PluginError:
        raise
    except (ValueError, OSError, zipfile.BadZipFile, EOFError, RuntimeError, zlib.error):
        raise _error('REST_ARCHIVE_EVIDENCE_CORRUPT', 'The exported ZPAC or its PAC evidence is unreadable',
                     attempted=True) from None
    return ArchiveEvidence(payload, sha256(payload).hexdigest(), len(payload))


def capture(current, pose, scope, selected, *, read_current, read_pose, read_scope, read_selection):
    """Export once with fresh equivalent bindings on both sides, returning bytes only to the caller.

    Readers use comparable snapshots of the current scene, complete pose, scope,
    and selection. The pose snapshot omits per-capture IDs and artifact paths.
    A post-export binding failure raises an error with ``requires_recovery``.
    """
    if any(value is None for value in (current, pose, scope, selected)):
        raise _error('REST_ARCHIVE_BINDING_MISMATCH', 'Four existing garment state bindings are required')
    expected = deepcopy((current, pose, scope, selected))
    readers = (read_current, read_pose, read_scope, read_selection)
    try:
        exporter = getattr(import_module('export_api'), 'ExportZPacW', None)
    except ImportError:
        exporter = None
    if not callable(exporter):
        raise _error('REST_ARCHIVE_EXPORT_UNAVAILABLE', 'The documented ExportZPacW SDK entry is unavailable')

    attempted = False
    try:
        with tempfile.TemporaryDirectory(prefix='md-mcp-rest-archive-') as directory:
            directory_path = Path(directory).resolve(strict=True)
            directory_before = directory_path.lstat()
            path = directory_path / 'garment.zpac'
            _check_bindings(expected, readers, attempted=False)
            attempted = True
            try:
                returned = exporter(str(path))
            except Exception:
                _check_bindings(expected, readers, attempted=True)
                raise _error('REST_ARCHIVE_EXPORT_FAILED', 'The garment export SDK call failed', attempted=True) from None
            _check_bindings(expected, readers, attempted=True)
            return _read_evidence(path, directory_before, returned)
    except OSError:
        raise _error('REST_ARCHIVE_TEMP_STORAGE_FAILED', 'The private garment export storage could not be used or removed',
                     attempted=attempted, uncertain=attempted) from None
