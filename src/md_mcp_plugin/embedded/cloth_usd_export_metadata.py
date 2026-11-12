"""Read the documented MD USD export binding without constructing options or calling it."""

from importlib import import_module
from inspect import getattr_static
import re


_EXPORTERS = ("ExportUSD", "ExportUSDW")
_OPTIONS = {
    "ImportExportOption": ("bExportGarment", "bExportAvatar"),
    "ExportUSDOption": ("m_bExportSimulationData",),
}
_EXPLICIT_PATH = re.compile(
    r"^\s*(?:\d+\.\s*)?(ExportUSDW|ExportUSD)\(\s*"
    r"[A-Za-z_]\w*\s*:\s*str\s*,\s*"
    r"[A-Za-z_]\w*\s*:\s*(?:ApiTypes\.)?ImportExportOption\s*,\s*"
    r"[A-Za-z_]\w*\s*:\s*(?:ApiTypes\.)?ExportUSDOption\s*\)\s*"
    r"->\s*(?:typing\.)?(?:List|list)\[\s*str\s*\]\s*$"
)
_MISSING = object()


def _module(name):
    try:
        return import_module(name)
    except ImportError:
        return None


def _exporter_row(export_api, name):
    export = getattr_static(export_api, name, None) if export_api is not None else None
    doc = getattr(export, "__doc__", None)
    doc = doc if type(doc) is str else ""
    bounded = len(doc) <= 4096
    overloads = []
    if callable(export) and bounded:
        for line in doc.splitlines():
            match = _EXPLICIT_PATH.fullmatch(line)
            if match and match.group(1) == name:
                overloads.append(line.strip())
    return {
        "present": export is not None,
        "callable": callable(export),
        "docstring": doc[:4096],
        "docstring_truncated": not bounded,
        "documented_explicit_path_overloads": overloads,
        "explicit_path_signature_observed": len(overloads) == 1,
    }


def _options_row(api_types, name, fields):
    cls = getattr_static(api_types, name, None) if api_types is not None else None
    available = isinstance(cls, type)
    return {
        "class_present": available,
        "descriptor_presence": {
            field: available and getattr_static(cls, field, _MISSING) is not _MISSING for field in fields
        },
    }


def probe():
    """Observe only named public bindings and option descriptors in the current runtime."""
    export_api = _module("export_api")
    api_types = _module("ApiTypes")
    functions = {name: _exporter_row(export_api, name) for name in _EXPORTERS}
    options = {name: _options_row(api_types, name, fields) for name, fields in _OPTIONS.items()}
    fields_present = all(row["class_present"] and all(row["descriptor_presence"].values()) for row in options.values())
    return {
        "schema": "md-cloth-usd-export-metadata/1",
        "metadata_only": True,
        "modules_present": {"export_api": export_api is not None, "ApiTypes": api_types is not None},
        "exporters": functions,
        "options": options,
        "documented_export_binding_observed": fields_present and any(
            row["explicit_path_signature_observed"] for row in functions.values()
        ),
        "option_instances_constructed": 0,
        "export_calls": 0,
        "export_dispatch_authorized": False,
        "solver_reference_xyz_verified": False,
    }
