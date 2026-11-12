# Native bridge builds on Windows

The source bundle contains 21 native bridge targets. Use a Windows x64 installation of MSVC 2022, a Windows SDK, and the Qt 6.10.3 MSVC x64 release SDK. The checked build used Windows SDK `10.0.26100.0`. Qt headers, import libraries, the MD executable, Qt runtime DLLs, and the Visual C++ runtime come from separately installed products. [Qt 6.10 Windows build requirements](https://doc.qt.io/qt-6.10/windows-building.html)

From this directory, set these variables to the corresponding local installation paths. `MD_QT_RUNTIME` is the directory containing the Qt DLLs supplied by the installed MD application.

```powershell
python -B build_bridges.py `
  --recipes release-recipes.json `
  --out build `
  --qt-sdk $env:QT_6103_SDK `
  --vc $env:MSVC_TOOLS_DIR `
  --windows-kits $env:WINDOWS_KITS_DIR `
  --runtime-qt $env:MD_QT_RUNTIME
```

Use a new output directory for each build. Add `--target cloth_meshbox_select`, or another key from `release-recipes.json`, to build a subset. The MeshBox selector requires the installed MD Qt directory at build time. The generated path stays in the build output and binds the selection adapter to that directory.

The builder hashes the reviewed translation units and local includes. It compiles with the x64 MSVC tools, checks the C ABI export set and imported libraries against the reviewed bridge, and compares each imported Qt symbol with the installed runtime export table. This requires no MD session. Build output stores the receipt, bridge, and compiler/linker diagnostics per target. A build receipt covers the PE and dependency contract. In-app verification applies to the selected runtime cohort and the exact binary used there.

After a full build, assemble a fresh local-install copy of the Python package and the exact verified binaries. Run this from `native-build` inside the extracted release source.

```powershell
python -B stage_local_install.py --build build --out local-install
python -m pip install ./local-install
```

The staging step preserves the package’s native manifest schema and writes the exact built SHA-256 values in both the manifests and the affected hard pins in the new tree. Use the resulting package in a fresh MD session when switching native cohorts.

The distribution input is this source directory, the build script and release recipes. Use separate directories for locally built DLLs and logs. Qt and Microsoft licensing applies to their respective SDK and runtime components. Review the applicable terms before any separate binary distribution. [Qt licensing obligations](https://www.qt.io/development/open-source-lgpl-obligations)
