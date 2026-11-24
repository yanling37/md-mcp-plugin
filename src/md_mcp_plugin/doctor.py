"""Read-only setup checks for the documented Windows control-handle workflow."""

from dataclasses import replace
from hashlib import file_digest
import json
import platform
import re
import struct
import sys

from .config import Config
from .errors import PluginError
from .transport import Transport


MD_VERSION = (2026, 1, 237)
BRIDGES = {
    "scene_adapter": ("md-mcp-packaged-scene-adapter/6", "md_qt_scene_adapter_v6_bridge.dll"),
    "canvas": ("md-mcp-packaged-canvas/1", "md_qt_canvas_bridge.dll"),
}
BUILD_HINT = "Build and stage the curve profile shown in native-build/README.md, then install that package."


def check(name, ok, code, message, **details):
    return dict(check=name, ok=ok, code=code, message=message, **details)


def bridge_check(directory, name, schema, library):
    label = f"bridge.{name}"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Expected a manifest object")
        sha = manifest.get("sha256")
        if (manifest.get("schema") != schema or manifest.get("library") != library
                or manifest.get("architecture") != "x64" or manifest.get("qt_version") != "6.10.3"
                or not isinstance(sha, str) or re.fullmatch(r"[a-f0-9]{64}", sha) is None
                or manifest.get("revision") != sha):
            raise ValueError("Unsupported bridge manifest")
    except FileNotFoundError:
        return check(label, False, "BRIDGE_MANIFEST_MISSING", BUILD_HINT)
    except (OSError, ValueError):
        return check(label, False, "BRIDGE_MANIFEST_INVALID", BUILD_HINT)

    try:
        with (directory / sha / library).open("rb") as bridge:
            actual = file_digest(bridge, "sha256").hexdigest()
    except FileNotFoundError:
        return check(label, False, "BRIDGE_DLL_MISSING", BUILD_HINT)
    except OSError:
        return check(label, False, "BRIDGE_DLL_UNREADABLE", "Check that this Python can read the installed bridge file.")
    if actual != sha:
        return check(label, False, "BRIDGE_HASH_MISMATCH", BUILD_HINT)
    return check(label, True, "OK", "Installed DLL matches its manifest. Live loading requires MD.")


def journal_check(directory):
    # Journal construction creates directories. Read its lease directly instead.
    try:
        with (directory / "mutation.lock").open("r", encoding="utf-8") as lease:
            owner = json.load(lease)
    except FileNotFoundError:
        return check("journal", True, "OK", "No active mutation lease found.")
    except (OSError, ValueError):
        return check("journal", False, "JOURNAL_UNREADABLE", "Check the configured shared journal and its permissions.")
    request_id = owner.get("request_id") if isinstance(owner, dict) else None
    if not isinstance(request_id, str) or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
        return check("journal", False, "JOURNAL_LEASE_INVALID", "Inspect the active lease before sending an edit.")
    return check("journal", False, "MUTATION_LEASE_ACTIVE", "Wait for the current writer or query md_operation_status.",
                 request_id=request_id)


def host_checks(config):
    transport = Transport(replace(config, timeout=min(config.timeout, 5)))
    try:
        transport.ping()
    except PluginError as exc:
        hint = "Start the compatible listener in the signed-in MD session and check MD_MCP_HOST and MD_MCP_PORT."
        return [check("listener", False, exc.code, hint, host=config.host, port=config.port)]

    listener = check("listener", True, "OK", "The listener answered ping.", host=config.host, port=config.port)
    try:
        receipt = transport.execute("scene.snapshot", {})
    except PluginError as exc:
        return [listener, check("md_version", False, exc.code,
                               "Ping worked. Check that the MD listener can run the plugin's scene read.")]
    scene = receipt.get("result")
    identity = scene.get("identity") if isinstance(scene, dict) else None
    version = identity.get("md_version") if isinstance(identity, dict) else None
    if receipt.get("status") != "SUCCEEDED" or not isinstance(version, list) or len(version) != 3 or any(
            type(part) is not int for part in version):
        return [listener, check("md_version", False, "MD_VERSION_UNAVAILABLE", "MD did not return a version in the scene read.")]
    supported = tuple(version) == MD_VERSION
    hint = "MD matches the qualified build." if supported else "The curve workflow is qualified on MD 2026.1.237."
    return [listener, check("md_version", supported, "OK" if supported else "MD_VERSION_UNQUALIFIED", hint,
                            version=".".join(map(str, version)))]


def run(*, offline=False):
    supported = sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"} and struct.calcsize("P") == 8
    checks = [check("platform", supported, "OK" if supported else "UNSUPPORTED_PLATFORM",
                    "The native curve workflow requires 64-bit Python on Windows x64.")]
    try:
        config = Config.from_env()
    except (OSError, ValueError):
        checks.append(check("configuration", False, "INVALID_CONFIGURATION",
                            "Check the loopback host, port, timeout and journal directory in the MD_MCP environment variables."))
    else:
        native = config.source_dir / "md_mcp_plugin" / "native"
        checks.extend(bridge_check(native / name, name, *spec) for name, spec in BRIDGES.items())
        checks.append(journal_check(config.data_dir))
        if not offline and supported:
            checks.extend(host_checks(config))
    return {"ok": all(item["ok"] for item in checks), "offline": offline, "checks": checks}
