"""Create an MD test project from the public neutral panel in MD's script editor."""

from hashlib import sha256
import json
from pathlib import Path

import export_api as export
import pattern_api as pattern


FIXTURE_PATH = Path(r"C:\md-mcp-plugin\docs\examples\neutral-panel.native.json")
OUTPUT_DIR = Path.home() / "MD-curve-demo"
FIXTURE_SHA256 = "3049284d61c14768ed4253737e54da964c94d7f186250e0fa7bcb55d22e495b0"


def outline(panel):
    return [
        [(point["PointType"], point["Position"]["x"], point["Position"]["y"])
         for point in edge["PointList"]]
        for edge in panel["ShapeInfo"]["LineList"]
    ]


def main():
    if pattern.GetPatternCount() or pattern.GetSeamlinePairGroupCount() or export.GetAvatarCount():
        raise RuntimeError("Open a new empty MD project reserved for the demo.")
    data = FIXTURE_PATH.read_bytes()
    if sha256(data).hexdigest() != FIXTURE_SHA256:
        raise RuntimeError("The fixture differs from the documented demo. Use the repository's original JSON.")
    reference, = json.loads(data)["PatternList"]

    OUTPUT_DIR.mkdir(exist_ok=False)
    cpx = OUTPUT_DIR / "neutral-panel.cpx"
    if not pattern.ConvertPatternJSONToCPX(str(FIXTURE_PATH), str(cpx)):
        raise RuntimeError("MD could not convert the sample pattern.")
    if pattern.GetPatternCount() != 1 or pattern.GetSeamlinePairGroupCount() or export.GetAvatarCount():
        raise RuntimeError("Expected one pattern, zero seam groups and zero avatars after conversion.")

    readback = OUTPUT_DIR / "neutral-panel.readback.json"
    if not pattern.ExportPatternJSON(str(readback)):
        raise RuntimeError("MD could not read back the converted pattern.")
    observed, = json.loads(readback.read_text(encoding="utf-8-sig"))["PatternList"]
    if observed["Name"] != reference["Name"] or outline(observed) != outline(reference):
        raise RuntimeError("The converted pattern differs from the reference geometry.")

    project = OUTPUT_DIR / "curve-demo-source.zprj"
    export.ExportZPrjW(str(project), False)
    if not project.is_file() or project.stat().st_size == 0:
        raise RuntimeError("MD did not save the sample project.")
    print(project)


if __name__ == "__main__":
    main()
