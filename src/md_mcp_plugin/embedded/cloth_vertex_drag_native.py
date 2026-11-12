"""Load the packaged viewport-bound MeshBox drag within MD's GUI callback."""

import ctypes
from functools import lru_cache
from hashlib import file_digest
import json
from pathlib import Path
import re


_DIRECTORY = Path(__file__).resolve().parents[1] / 'native' / 'cloth_vertex_drag'
_LIBRARY = 'md_qt_cloth_vertex_drag.dll'


def load():
    manifest = json.loads((_DIRECTORY / 'manifest.json').read_text(encoding='utf-8'))
    checksum = manifest.get('sha256')
    if (manifest.get('schema') != 'md-mcp-packaged-cloth-vertex-drag/2'
            or manifest.get('architecture') != 'x64' or manifest.get('qt_version') != '6.10.3'
            or manifest.get('library') != _LIBRARY or not isinstance(checksum, str)
            or re.fullmatch('[0-9a-f]{64}', checksum) is None or manifest.get('revision') != checksum):
        raise ValueError('Invalid packaged Cloth vertex drag manifest')
    return _load(_DIRECTORY / checksum / _LIBRARY, checksum)


@lru_cache(maxsize=1)
def _load(path, checksum):
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ValueError('Cloth vertex drag must be the packaged library')
    with path.open('rb') as stream:
        if file_digest(stream, 'sha256').hexdigest() != checksum:
            raise ValueError('Cloth vertex drag library checksum changed')
    library = ctypes.CDLL(str(path), winmode=0x00001100)
    version = library.mdqt_cloth_vertex_drag_abi_version
    version.argtypes = []
    version.restype = ctypes.c_int
    if version() != 2:
        raise ValueError('Cloth vertex drag ABI differs from the packaged contract')
    function = library.mdqt_cloth_vertex_drag
    function.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_size_t, *([ctypes.c_double] * 6),
                         ctypes.c_int, ctypes.c_int, ctypes.c_double, ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_char), ctypes.c_uint32,
                         ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    function.restype = ctypes.c_int
    return function, checksum
