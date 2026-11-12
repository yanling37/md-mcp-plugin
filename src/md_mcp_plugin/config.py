from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    timeout: float
    data_dir: Path
    source_dir: Path

    @classmethod
    def from_env(cls):
        host = os.environ.get("MD_MCP_HOST", "127.0.0.1")
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("This local plugin accepts only loopback MD listeners")
        port = int(os.environ.get("MD_MCP_PORT", "7421"))
        timeout = float(os.environ.get("MD_MCP_TIMEOUT", "90"))
        if not 1 <= port <= 65535 or not 0 < timeout <= 600:
            raise ValueError("Invalid MD listener port or timeout")
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "md-mcp-plugin"
        return cls(host, port, timeout, Path(os.environ.get("MD_MCP_DATA_DIR", str(default))).resolve(), Path(__file__).resolve().parent.parent)
