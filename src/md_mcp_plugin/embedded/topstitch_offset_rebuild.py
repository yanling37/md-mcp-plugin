"""Typed assignment getters/setters and OBJ array evidence for current Topstitch."""

import math
import re

from . import world_frame
from .common import digest


GET_OFFSET = 'GetPatternAssignedTopstitchZOffset'
SET_OFFSET = 'SetPatternAssignedTopstitchZOffset'
OFFSET_STEP = .1


def _typed_method(api, name, arguments, result):
    method = getattr(api, name, None)
    signature = getattr(method, '__doc__', None)
    annotations = {
        'int': r'(?:int|typing\.SupportsInt|typing\.SupportsIndex)',
        'float': r'(?:float|typing\.SupportsFloat)',
    }
    items = [rf'[A-Za-z_]\w*\s*:\s*{annotations[value]}' for value in arguments]
    expected = rf'^\s*{name}\(\s*' + r'\s*,\s*'.join(items) + rf'\s*\)\s*->\s*{result}\s*$'
    if callable(method) and type(signature) is str and re.search(expected, signature, re.M):
        return method
    return None


def _methods(api):
    return (_typed_method(api, GET_OFFSET, ('int', 'int'), 'float'),
            _typed_method(api, SET_OFFSET, ('int', 'int', 'float'), r'(?:None|NoneType)'))


def _sdk_read(method, *args, rows=False):
    if not callable(method):
        return {'status': 'SDK_METHOD_UNAVAILABLE'}
    try:
        value = method(*args)
    except Exception as exc:
        return {'status': 'SDK_READ_FAILED', 'error': str(exc)[:500]}
    kind = type(value)
    result = {'status': 'READ', 'python_type': kind.__name__}
    if kind in (bool, int) or (kind is float and math.isfinite(value)):
        result['raw_value'] = value
    elif kind is str or kind is float:
        result['raw_value'] = str(value)[:500]
    elif rows and kind is list and len(value) <= 128 and all(
            type(row) is dict and len(row) <= 16
            and all(type(k) is type(v) is str and len(k) <= 256 and len(v) <= 256 for k, v in row.items()) for row in value):
        result['raw_value'] = value
    else:
        result['status'] = 'SDK_RETURN_NOT_SERIALIZED'
    return result


def _arrays_hash(arrays):
    return digest({key: arrays[key] for key in world_frame.MESH_FIELDS})
