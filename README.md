# MD MCP Plugin

A local stdio MCP server for AI clients working with Marvelous Designer. It uses a loopback listener in MD and returns structured receipts for inspection, editing, verification, and recovery.

## Requirements

- Python 3.11 or later for the MCP server.
- Windows x64 for the native adapters. The live validation target is Marvelous Designer 2026.1.237 with Qt 6.10.3.
- An authenticated MD session with a compatible listener on `127.0.0.1:7421`. The current listener protocol is `ping` and `execute_python` from [MarvelousDesigner-MCP](https://github.com/Laboon2501/MarvelousDesigner-MCP).

The existing listener can execute Python inside MD. Keep it on loopback and connect trusted local clients.

The source distribution includes the adapter manifests and [Windows build recipes](native-build/README.md). To use a native tool, build and stage the matching bridges using those instructions. The runtime checks each bridge hash before loading. The source package starts and exposes its MCP tool schemas independently of those bridge files.

## Start the MCP server

Install the source package into a Python environment, then run the module as a stdio MCP server:

```sh
python -m pip install .
python -m md_mcp_plugin
```

Use that Python executable as the MCP client command with `-m md_mcp_plugin` as its arguments. Set `MD_MCP_HOST` and `MD_MCP_PORT` if the loopback endpoint differs. Give every client of the same listener the same writable `MD_MCP_DATA_DIR` so that they share the write journal.

## Use from an AI client

1. Call `md_capabilities` and `md_connect`. Read the scene before editing and work in a copy until the operation is verified.
2. Resolve the current pattern UUID and the operation-specific references. Supply the current scene token and a unique request ID with each write.
3. Inspect the result for the requested geometric verification. If a call times out, query `md_operation_status` for that request before retrying.

Use `mesh_read_scene(summary_only=True)` to compare the digest of complete OBJ geometry without transferring the full array payload. The summary retains the hashed OBJ asset path. Request the full snapshot when vertex references are needed.

The validated routes cover an individual Bézier control handle with fixed endpoints, straight and single-segment curved native cuts, single cloth vertex XYZ, stopped-simulation Selected Patterns Rest Update/Reset, and calibrated world-space placement. Rotation requires a current ring plan. Rest requires the matching native controls to be available. The decimal Grid operation verifies the selected current UUID and full OBJ. Its broader project scope is covered by offline tests and requires live qualification. `md_capabilities` reports the tested scope and current adapter constraints. Automatic MD Personal sign-in from a fresh desktop session remains unverified.

### Initialize Rest after starting MD

1. Open a saved disposable `.zprj` copy through `md_project_open` and keep MD minimized. Use `rest_adapter_inspect_host_controls` to check whether a Rest host is already initialized.
2. For an uninitialized host, use `rest_open_simulation_properties` and `rest_set_pinching_override` to enable Pinching Simulation Override. Keep the original checkbox value from the setting receipt.
3. Call `rest_initialize_host` with the current scene token, saved copy SHA-256, and a fresh request ID. Poll `md_operation_status` for the same request ID until it settles. The plugin starts and stops the simulation once, then verifies that Select/Move is restored and the simulation pinching tools are disabled. The background toolbar refresh is bound to the supported MD 2026.1.237 executable. In-memory vertex positions on the copy can change.
4. After `SUCCEEDED`, restore the original checkbox value, reopen your working project, and verify the hidden host with `rest_adapter_inspect_host_controls`. For an `UNKNOWN` result, use `md_operation_status` on the original request to check for a later receipt. The shared mutation journal stays locked while the outcome is unresolved.

## Run the offline tests

With the package and its Python dependencies installed, run `python -m unittest discover -s tests`. The tests use synthetic scenes and isolated temporary files. They do not connect to MD.

## License

[MIT](LICENSE). [Third-party notices](NOTICE.md).
