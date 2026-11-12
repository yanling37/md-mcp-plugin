"""One bounded JSON-line RPC, no automatic retry or process/window control."""
import json
import socket
import uuid

from .errors import PluginError


class Transport:
    def __init__(self, config):
        self.config = config

    def request(self, method, params=None):
        request_id = uuid.uuid4().hex
        payload = json.dumps({"id": request_id, "method": method, "params": params or {}}, allow_nan=False).encode("utf8") + b"\n"
        phase = "connect"
        try:
            with socket.create_connection((self.config.host, self.config.port), timeout=self.config.timeout) as connection:
                phase = "send"
                connection.sendall(payload)
                phase = "receive"
                data = bytearray()
                while b"\n" not in data:
                    chunk = connection.recv(65536)
                    if not chunk:
                        raise PluginError("CONNECTION_CLOSED", "MD closed the connection before a receipt", outcome="unknown", retry_safe=False)
                    data.extend(chunk)
                    if len(data) > 32 * 1024 * 1024:
                        raise PluginError("RESPONSE_TOO_LARGE", "MD response exceeds 32 MiB", outcome="unknown", retry_safe=False)
        except ConnectionRefusedError as exc:
            raise PluginError("LISTENER_UNAVAILABLE", "The local MD listener is not running", outcome="not_sent", retry_safe=True) from exc
        except (OSError, TimeoutError) as exc:
            raise PluginError("TRANSPORT_ERROR", str(exc), phase=phase, outcome="not_sent" if phase == "connect" else "unknown", retry_safe=phase == "connect") from exc
        try:
            reply = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf8"))
        except (ValueError, UnicodeError) as exc:
            raise PluginError("INVALID_RECEIPT", "Listener returned invalid JSON", outcome="unknown", retry_safe=False) from exc
        if not isinstance(reply, dict) or reply.get("id") != request_id:
            raise PluginError("RECEIPT_MISMATCH", "Listener receipt does not match the request", outcome="unknown", retry_safe=False)
        if "error" in reply:
            raise PluginError("LISTENER_ERROR", str(reply["error"]), outcome="unknown", retry_safe=False)
        return reply.get("result")

    def ping(self):
        return self.request("ping")

    def execute(self, operation, params, *, operation_id=None, scene_token=None):
        # Data is a JSON string literal, never interpolated as executable code.
        payload = json.dumps({"operation": operation, "params": params, "operation_id": operation_id, "scene_token": scene_token}, ensure_ascii=True, allow_nan=False)
        source = str(self.config.source_dir)
        code = ("import sys as _mdp_sys, json as _mdp_json, hashlib as _mdp_hash, importlib as _mdp_import\n"
                f"_mdp_source = {source!r}\n"
                "if _mdp_source not in _mdp_sys.path: _mdp_sys.path.insert(0, _mdp_source)\n"
                "from md_mcp_plugin.embedded import dispatch as _mdp_dispatch\n"
                "with open(_mdp_dispatch.__file__, 'rb') as _mdp_stream: _mdp_revision = _mdp_hash.sha256(_mdp_stream.read()).hexdigest()\n"
                "if getattr(_mdp_dispatch, '_LOADED_REVISION', None) != _mdp_revision:\n"
                "    _mdp_receipts = dict(getattr(_mdp_dispatch, '_RECEIPTS', {}))\n"
                "    _mdp_payloads = dict(getattr(_mdp_dispatch, '_PAYLOADS', {}))\n"
                "    _mdp_dispatch = _mdp_import.reload(_mdp_dispatch)\n"
                "    _mdp_dispatch._RECEIPTS.update(_mdp_receipts)\n"
                "    _mdp_dispatch._PAYLOADS.update(_mdp_payloads)\n"
                "    _mdp_dispatch._LOADED_REVISION = _mdp_revision\n"
                f"result = _mdp_dispatch.execute(_mdp_json.loads({payload!r}))\n")
        envelope = self.request("execute_python", {"code": code})
        if not isinstance(envelope, dict) or envelope.get("error"):
            raise PluginError("EMBEDDED_EXECUTION_FAILED", "MD did not return a structured operation receipt", receipt=envelope, outcome="unknown", retry_safe=False)
        result = envelope.get("result")
        if not isinstance(result, dict) or result.get("protocol") != "md-mcp-plugin/1":
            raise PluginError("INVALID_EMBEDDED_RECEIPT", "MD operation receipt has the wrong protocol", outcome="unknown", retry_safe=False)
        return result
