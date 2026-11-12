"""Fixed Translate 3D reader. Loading is deferred until an authorized read."""
from __future__ import annotations

import ctypes
import hashlib
from pathlib import Path
import re
import sys

from . import canvas as _base

SCHEMA = "md-qt-world-numeric-probe/1"
STATE_KEY = "_md_mcp_plugin_world_numeric_probe_state"
CAPACITY = 1048576
CLASSES = {"DialogTranslate3D", "Marvelous::DialogTranslate3D"}


def _state():
    state = getattr(sys, STATE_KEY, None)
    if state is None:
        state = {"configuration": None, "library": None, "call": None, "process": None}
        setattr(sys, STATE_KEY, state)
    return state


def _hex(value):
    return type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None


def _matches_hash(path, sha256):
    return not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256


def install_native_bridge(path, sha256):
    """Pin the independently reviewed DLL through trusted bootstrap."""
    candidate = Path(path)
    if (not _hex(sha256) or not candidate.is_absolute() or candidate.name != "md_qt_world_numeric_probe.dll"
            or not _matches_hash(candidate, sha256)):
        raise ValueError("One unchanged pinned Translate 3D probe DLL required")
    configuration = {"path": str(candidate.resolve()), "sha256": sha256}
    state = _state()
    if state["library"] is not None and state["configuration"] != configuration:
        raise ValueError("Loaded Translate 3D probe cannot be hot-swapped")
    state["configuration"] = configuration
    return {"configured": True, "loaded": state["library"] is not None, "live_validation": "pending"}


def _load(process):
    state = _state()
    configuration = state["configuration"]
    if configuration is None or not _matches_hash(Path(configuration["path"]), configuration["sha256"]):
        raise ValueError("Pinned Translate 3D probe DLL changed")
    if state["library"] is None:
        library = ctypes.CDLL(configuration["path"], winmode=0x900)
        abi = library.mdqt_world_numeric_probe_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise ValueError("Translate 3D probe C ABI1 required")
        call = library.mdqt_world_numeric_probe_inspect
        call.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        call.restype = ctypes.c_int
        state.update(library=library, call=call, process=dict(process))
    if state["process"] != process:
        raise ValueError("Translate 3D probe belongs to another process lifetime")
    return state["call"]


def _validate(raw, code, process):
    if (type(raw) is not dict or raw.get("schema") != SCHEMA or code not in (0, 1)
            or type(raw.get("return_code")) is not int or raw["return_code"] != code
            or type(raw.get("process_id")) not in (int, float) or raw["process_id"] != process["pid"]
            or raw.get("process_creation_filetime") != str(process["creation_filetime"])
            or raw.get("qt_version") != "6.10.3" or raw.get("read_only") is not True
            or raw.get("effects_started") is not False or raw.get("write_contract_verified") is not False):
        raise ValueError("Translate 3D read-only receipt or process identity differs")
    if code == 1:
        if raw.get("verified") is not False or raw.get("status") != "READ_NOT_VERIFIED" or type(raw.get("reason")) is not str:
            raise ValueError("Incomplete Translate 3D receipt differs")
        return
    instances = raw.get("instances")
    if (raw.get("verified") is not True or raw.get("status") != "READ_COMPLETE"
            or type(instances) is not list or len(instances) > 4
            or type(raw.get("instance_count")) is not int or raw["instance_count"] != len(instances)):
        raise ValueError("Translate 3D instance receipt differs")
    for row in instances:
        if (type(row) is not dict or type(row.get("class_name")) is not str or row["class_name"] not in CLASSES
                or type(row.get("controls")) is not list or len(row["controls"]) > 128
                or type(row.get("metadata")) is not dict):
            raise ValueError("Unexpected Translate 3D instance or controls")


def _failure(code, message, status="REJECTED"):
    return {"status": status, "result": {"verified": False, "read_only": True, "write_contract_verified": False},
            "effects_started": False, "requires_recovery": False, "error": {"code": code, "message": message}}


def inspect(params):
    try:
        if type(params) is not dict or "scene_token" not in params or params.keys() - {"scene_token", "budget_ms"}:
            return _failure("INVALID_PARAMETERS", "Expected scene_token and optional budget_ms")
        token, budget = params["scene_token"], params.get("budget_ms", 500)
        if not _hex(token) or type(budget) is not int or not 50 <= budget <= 1500:
            return _failure("INVALID_PARAMETERS", "Current scene token and integer budget [50,1500] required")
        if _state()["configuration"] is None:
            return _failure("WORLD_NUMERIC_PROBE_NOT_CONFIGURED", "The embedded bootstrap must configure the probe", "UNSUPPORTED")
        before = _base._scene()
        if before["scene_token"] != token:
            return _failure("STALE_SCENE", "Read the current scene again")
        process = _base._environment()["process"]
        call = _load(process)
        output = ctypes.create_string_buffer(CAPACITY)
        written, needed = ctypes.c_uint32(), ctypes.c_uint32()
        code = call(process["pid"], process["creation_filetime"], budget, output, len(output),
                    ctypes.byref(written), ctypes.byref(needed))
        if code < 0:
            return _failure("NATIVE_READ_FAILED", f"Translate 3D probe returned {code}")
        if not 0 < written.value < CAPACITY or needed.value != written.value + 1 or output[written.value] != b"\0":
            raise ValueError("Translate 3D native output bounds differ")
        raw = _base._json(output[:written.value].decode("utf8"))
        _validate(raw, code, process)
        if _base._scene()["scene_token"] != token:
            return _failure("SCENE_CHANGED_DURING_READ", "Translate 3D read requires a stable scene token")
        raw["scene_token"] = token
        return {"status": "SUCCEEDED" if code == 0 else "REJECTED", "result": raw, "effects_started": False,
                "requires_recovery": False, "error": None if code == 0 else {"code": raw["status"], "message": raw["reason"]}}
    except Exception as error:
        return _failure("WORLD_NUMERIC_PROBE_FAILED", str(error))


OPERATIONS = {"world_numeric_probe.inspect": inspect}
