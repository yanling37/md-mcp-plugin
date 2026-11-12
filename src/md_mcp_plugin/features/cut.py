"""Current-project native Cut tools; runtime presence is not live acceptance."""
from __future__ import annotations

import re


def capabilities() -> dict:
    return {
        "live_validation": "pending",
        "cut.probe_runtime": {"status": "supported", "basis": "Symbol/doc metadata only; no Cut or geometry writer calls."},
        "cut.inspect": {"status": "unverified", "basis": "Fresh UUID/runtime-child references and optional public native JSON; current MD export availability needs validation."},
        "cut.along_internal_shape": {
            "status": "unverified",
            "basis": "One open straight/cubic line, one genuine internal shape; requires an explicit runtime ordinal contract plus positive geometry mapping and one native call/readback.",
        },
        "surface_preservation": {"status": "unsupported", "basis": "No generic 3D/Rest/UV preservation reader/acceptance contract. Required by default."},
        "cut_and_sew_readback": {"status": "unsupported", "basis": "Native boolean is passed faithfully, but automatic seam ownership/range preservation is not yet verified."},
        "multi_shape_or_closed_cut": {"status": "unsupported", "basis": "No proven generic UUID to GetInnerShapeList ordinal mapping; never enumerate candidate indices."},
        "historical_evidence": {
            "E21": "CutPatternAlongInternalShapes(0,0,True) returned False; 2 patterns/1 seam unchanged.",
            "limit": "This is one historical failure, not proof of a stub or current-runtime execution.",
        },
    }


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
        raise ValueError(f"{label} must be a nonempty unpadded string (maximum 512 characters)")
    return value


def register(mcp, context) -> None:
    @mcp.tool()
    def cut_probe_runtime(scene_token: str | None = None) -> dict:
        """Read actual SDK symbol signatures only. Presence never proves native Cut works."""
        return context.call("cut.probe_runtime", {}, scene_token=scene_token)

    @mcp.tool()
    def cut_inspect(pattern_uuid: str, scene_token: str | None = None) -> dict:
        """Resolve a current UUID and inspect topology-bound shape/line references and distinct child/ordinal namespaces. Read-only."""
        _text(pattern_uuid, "pattern_uuid")
        return context.call("cut.inspect", {"pattern_uuid": pattern_uuid}, scene_token=scene_token)

    @mcp.tool()
    def cut_along_internal_shape(
        pattern_uuid: str,
        shape_uuid: str,
        topology_fingerprint: str,
        scene_token: str,
        line_uuid: str | None = None,
        cut_and_sew: bool = False,
        require_surface_preservation: bool = True,
        request_id: str | None = None,
    ) -> dict:
        """Cut once along an existing whole internal shape, never create a line or retry indices. Use refs from cut_inspect. Coordinates/tolerances are 2D mm. Default 3D/Rest/UV preservation is unsupported; explicitly false requests only native 2D partition evidence. Cut & Sew also reports unverified seam readback."""
        for name, value in (("pattern_uuid", pattern_uuid), ("shape_uuid", shape_uuid), ("scene_token", scene_token)):
            _text(value, name)
        if line_uuid is not None:
            _text(line_uuid, "line_uuid")
        if not isinstance(topology_fingerprint, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", topology_fingerprint) is None:
            raise ValueError("Use the topology_fingerprint returned by cut_inspect")
        if type(cut_and_sew) is not bool or type(require_surface_preservation) is not bool:
            raise ValueError("cut_and_sew and require_surface_preservation must be booleans")
        return context.call(
            "cut.along_internal_shape",
            {
                "pattern_uuid": pattern_uuid,
                "shape_uuid": shape_uuid,
                "topology_fingerprint": topology_fingerprint,
                "line_uuid": line_uuid,
                "cut_and_sew": cut_and_sew,
                "require_surface_preservation": require_surface_preservation,
            },
            mutation=True, request_id=request_id, scene_token=scene_token,
        )
