import ctypes
from ctypes import wintypes
import hashlib
import json
import os

from md_mcp_plugin.errors import PluginError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def parsed(value):
    return json.loads(value) if isinstance(value, str) else value


def process_identity():
    value = {"pid": os.getpid()}
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *[ctypes.byref(t) for t in times]):
            raise PluginError("PROCESS_IDENTITY_UNAVAILABLE", "Cannot query MD process creation time")
        value["creation_filetime"] = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return value


def pattern_rows():
    import pattern_api
    rows = []
    seen = set()
    for index in range(pattern_api.GetPatternCount()):
        information = parsed(pattern_api.GetPatternInformation(index))
        identity = information.get("uuid")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise PluginError("AMBIGUOUS_PATTERN", "Current pattern UUIDs are missing or duplicated")
        seen.add(identity)
        row = {"pattern_uuid": identity, "index": index, "information": information}
        if callable(getattr(pattern_api, "GetPatternInputInformation", None)):
            row["input_information"] = parsed(pattern_api.GetPatternInputInformation(index))
            row["topology_fingerprint"] = digest(row["input_information"])
        rows.append(row)
    return rows


def resolve_pattern(pattern_uuid):
    if not isinstance(pattern_uuid, str) or not pattern_uuid:
        raise PluginError("INVALID_PATTERN_REFERENCE", "A nonempty current pattern UUID is required")
    matches = [row for row in pattern_rows() if row["pattern_uuid"] == pattern_uuid]
    if len(matches) != 1:
        raise PluginError("STALE_PATTERN_REFERENCE", "Pattern UUID does not resolve uniquely in the current project", pattern_uuid=pattern_uuid)
    return matches[0]


def scene():
    import utility_api
    rows = pattern_rows()
    identity = {**process_identity(), "project_path": str(utility_api.GetProjectFilePath()),
                "md_version": [utility_api.GetMajorVersion(), utility_api.GetMinorVersion(), utility_api.GetPatchVersion()]}
    value = {"identity": identity, "patterns": rows, "pattern_count": len(rows),
             "fingerprint_scope": "process/project/pattern_input/quality; complete 3D mesh is not included"}
    if callable(getattr(utility_api, "GetSimulationQuality", None)):
        value["simulation_quality"] = list(utility_api.GetSimulationQuality())
    value["scene_token"] = digest(value)
    return value


def check_scene_token(token):
    actual = scene()
    if not isinstance(token, str) or token != actual["scene_token"]:
        raise PluginError("STALE_SCENE", "Read md_scene again before using references from this project", current_scene_token=actual["scene_token"])
    return actual
