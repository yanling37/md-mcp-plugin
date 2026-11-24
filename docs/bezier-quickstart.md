# Edit one Bézier handle and verify the saved result

This example uses an asymmetric panel with one outer cubic and three straight sides. You will move the handle next to `(0, 1000)` by `+0.25 mm` in X and `−0.15 mm` in Y, then reopen the saved project and measure it again. The panel is the same geometry used in the free Agensi Skill. [Agensi Bézier Handle Editing](https://www.agensi.io/skills/marvelous-designer-b-zier-handle-editing)

## Connect the local client

1. Use Windows x64, Python 3.11 or later, and the validated MD build `2026.1.237` with Qt `6.10.3`. Sign in to MD and start the compatible loopback listener. If this is your first listener install, follow the pinned upstream setup, then run `md_start_listener` from MD's Plug-in menu. [Listener quick start](https://github.com/Laboon2501/MarvelousDesigner-MCP/tree/1d6aec0ac869fcbe2d8a33bbead99353b619555b#quick-start)
2. Follow the [curve-only native build](../native-build/README.md#build-only-the-curve-workflow). Install the staged package in the Python environment you will use for the MCP client. Keep the source checkout for the sample.
3. Run the following from the Windows machine running MD. Adjust `$python`, the port and the journal path to match your installation. Every client of the same listener must use the same writable journal directory.

   ```powershell
   $python = 'C:\mdmcp\venv\Scripts\python.exe'
   $env:MD_MCP_HOST = '127.0.0.1'
   $env:MD_MCP_PORT = '7421'
   $env:MD_MCP_DATA_DIR = 'C:\mdmcp\journal'
   & $python -m md_mcp_plugin doctor
   codex mcp add md-mcp-plugin `
     --env "MD_MCP_HOST=$env:MD_MCP_HOST" `
     --env "MD_MCP_PORT=$env:MD_MCP_PORT" `
     --env "MD_MCP_DATA_DIR=$env:MD_MCP_DATA_DIR" `
     -- $python -m md_mcp_plugin
   ```

   `doctor` reports local bridge and journal checks plus listener and host checks. Use `doctor --offline` to check the local install before starting MD. Reopen your local Codex session and use `/mcp` to check the server entry. Another local AI client can use the same stdio command, arguments and environment in its own configuration. [Codex MCP setup](https://learn.chatgpt.com/docs/extend/mcp?surface=cli#configure-with-the-cli)

## Create the sample project

Open a **new empty MD project** for this sample with simulation stopped. Choose **Plugins → Python Editor** and open [create-bezier-demo.py](examples/create-bezier-demo.py). Set `FIXTURE_PATH` to the absolute path of [neutral-panel.native.json](examples/neutral-panel.native.json) in your checkout and choose **Run Script**. The script checks for an empty project, loads the synthetic panel using MD's pattern conversion, and saves a fresh `curve-demo-source.zprj` in your home folder under `MD-curve-demo`. Set a fresh `OUTPUT_DIR` for another run. [MD Python Editor guide](https://developer.marvelousdesigner.com/python.html)

Copy the saved file to `curve-demo-input.zprj` in the same folder. Keep the source for hash comparison. Check the output path printed by the script and adjust `$demo` if you changed `OUTPUT_DIR`.

```powershell
$demo = Join-Path $env:USERPROFILE 'MD-curve-demo'
$source = Join-Path $demo 'curve-demo-source.zprj'
$work = Join-Path $demo 'curve-demo-input.zprj'
if (Test-Path -LiteralPath $work) { throw 'Choose a new working filename.' }
Copy-Item -LiteralPath $source -Destination $work
Get-FileHash -Algorithm SHA256 -LiteralPath $source, $work
```

## Ask the agent to edit and verify

Leave MD signed in with the demo project active and minimize it. Give the agent the absolute input and output paths. This prompt supplies the measured target and checks.

> In my running Marvelous Designer demo session, open `C:\Users\<me>\MD-curve-demo\curve-demo-input.zprj`. On `Agensi_Demo_Panel`, find the cubic between `(0, 1000)` and `(100, 1000)` mm. Move the handle next to `(0, 1000)` to `(30.25, 979.85)` mm. Save the edit to the new file `C:\Users\<me>\MD-curve-demo\curve-demo-result.zprj`. Reopen the unedited input and verify the original handle, then reopen the saved result and verify the changed handle. Report target and actual XY, error in millimetres, both endpoints, the opposite handle, and the source and input file SHA-256 before and after. Use a fresh request ID for each write and look up the original ID if a write times out.

For a client that calls tools directly, use this sequence. Fetch scene and viewport references from the current replies.

1. Call `md_connect` and `md_scene`. Read the active path and `scene_token`. Call `md_project_open` for the absolute input path with the current `scene_token` and a fresh `request_id`. Use `replace_current=true` only after confirming the current scene is the demo. Resolve the current UUID from the new `md_scene` result.
2. Call `md_select_pattern` with the UUID, current token and a fresh request ID. Use `scene_adapter_inspect_2d` to get the current view reference and fingerprint, then `scene_adapter_set_tool` with `tool="edit_curve_point"`. Inspect again for fresh references and call `scene_adapter_fit_selected`. Each action takes its own request ID.
3. Read `curve_handles(pattern_uuid, scene_token)` and pick its single outer cubic by the endpoint coordinates. Identify the handle next to `(0, 1000)` using the endpoint and handle role in the live response. Pass its fresh `handle_ref` to `set_curve_handle_xy` with `x_mm=30.25`, `y_mm=979.85` and `tolerance_mm=0.002`. Read the curve again after a successful write.
4. Use `md_project_save_copy` with a new absolute `.zprj` path. Check the saved file's `sha256` from the receipt. Open the unedited input, read its baseline, then open the saved result and read again. Resolve UUIDs and tokens on each reopen. Pass existing filenames only to `md_project_open` and a fresh output filename to `md_project_save_copy`.

## Compare with the recorded result

| Check | Recorded result on MD 2026.1.237 with the `v0.1.0a1` connector |
| --- | --- |
| Selected handle on the reopened input | `(30, 980)` mm |
| Target | `(30.25, 979.85)` mm |
| Selected handle on the reopened result | `(30.251148, 979.849910)` mm |
| Euclidean target error | `0.001152 mm` with a `0.002 mm` tolerance |
| Endpoints | `(0, 1000)` and `(100, 1000)` mm before and after |
| Opposite handle | `(70, 980)` mm before and after |
| Source and input files | Both SHA-256 hashes matched their starting values |

If the edit succeeds, record your time from setup to first verified reopen, the number of MCP tool calls and the manual steps you took. Report installation or edit failures through [GitHub Issues](https://github.com/yanling37/md-mcp-plugin/issues). Include the MD build, diagnostic check, failing tool and error code. You can use this public panel to reproduce the problem.

| Symptom | Check |
| --- | --- |
| Session reachable but bridge absent or rejected | Run `doctor` using the Python command configured in the MCP client. Check the staged package location, MD build and bridge hashes. |
| `NO_UNIQUE_ANCHOR_AND_BLUE_TARGET_SOLUTION` | Use the supplied asymmetric outline and check that only the sample piece is selected, Edit Curve Point is active, and Fit Selected ran. |
| A write times out or reports an uncertain result | Query `md_operation_status` with the original request ID. A timeout can leave a write in progress. |
