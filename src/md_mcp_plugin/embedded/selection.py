"""Current-UUID pattern selection through the documented SDK, without OS input."""
from md_mcp_plugin.errors import PluginError
from .common import scene


def _read():
    import pattern_api as api
    current = scene()
    for name in ("GetSelectedPatternViaIndex", "GetSelectedPattern"):
        if not callable(getattr(api, name, None)):
            raise PluginError("SELECTION_API_UNAVAILABLE", "Required selection getter is unavailable", function=name, effects_started=False)
    rows = []
    for paper in current["patterns"]:
        selected = api.GetSelectedPatternViaIndex(paper["index"])
        if type(selected) is not bool:
            raise PluginError("INVALID_SELECTION_READBACK", "Selection flags must be native booleans", effects_started=False)
        rows.append({"pattern_uuid": paper["pattern_uuid"], "index": paper["index"], "name": paper["information"].get("name"), "selected": selected})
    names = api.GetSelectedPattern()
    if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
        raise PluginError("INVALID_SELECTION_READBACK", "Selected pattern names must be a list of strings", effects_started=False)
    return current, rows, names


def inspect_selection(params):
    if params:
        raise PluginError("INVALID_PARAMETERS", "Selection inspection has no parameters", effects_started=False)
    current, rows, names = _read()
    return {"selected": [r for r in rows if r["selected"]], "patterns": rows,
            "selected_names": names, "scene_token": current["scene_token"]}


def select_pattern(params):
    try:
        if set(params) - {'pattern_uuid','refresh'} or 'pattern_uuid' not in params or not isinstance(params["pattern_uuid"], str) or type(params.get('refresh',False)) is not bool:
            raise ValueError("A current pattern UUID is required")
        before, rows, names = _read()
        matches = [r for r in rows if r["pattern_uuid"] == params["pattern_uuid"]]
        if len(matches) != 1:
            raise ValueError("Pattern UUID does not resolve uniquely in the current project")
        target = matches[0]
        import pattern_api as api
        if not callable(getattr(api, "SelectPatternViaIndex", None)):
            raise ValueError("SelectPatternViaIndex is unavailable")
    except Exception as exc:
        raise PluginError("SELECTION_PREFLIGHT_FAILED", str(exc), effects_started=False) from exc
    previous = [r["pattern_uuid"] for r in rows if r["selected"]]
    if not params.get('refresh') and previous == [target["pattern_uuid"]] and names == [target["name"]]:
        return {"status": "NO_OP", "verified": True, "effects_started": False,
                "selected": [target], "previously_selected_uuids": previous, "scene_token": before["scene_token"]}
    returned = api.SelectPatternViaIndex(target["index"], False)
    after, selected_rows, selected_names = _read()
    selected = [r for r in selected_rows if r["selected"]]
    verified = (returned is None and [r["pattern_uuid"] for r in selected] == [target["pattern_uuid"]]
                and selected_names == [target["name"]] and before["scene_token"] == after["scene_token"])
    return {"status": "SUCCEEDED" if verified else "PARTIAL", "verified": verified,
            "effects_started": True, "requires_recovery": not verified, "api_return": returned,
            "selected": selected, "selected_names": selected_names,
            "previously_selected_uuids": previous, "scene_token": after["scene_token"]}


OPERATIONS = {"selection.inspect": inspect_selection, "selection.select": select_pattern}
