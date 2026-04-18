# Equicord Plugin Installer

A small Windows-friendly GUI for [Equicord](https://github.com/Equicord/Equicord): clone or link the repo, copy custom **userplugins** into `src/userplugins`, run `pnpm build`, and inject into Discord using **Equilotl**—with a live log, saved linked folder, and non-interactive inject (`-branch auto|stable|canary|ptb`).

> **Disclaimer:** Third-party client modifications may violate Discord’s Terms of Service. Use at your own risk.

## Requirements

| For the GUI | For clone / build / inject |
|-------------|----------------------------|
| Python 3.10+ (if running `.py`) | [Git](https://git-scm.com) |
| [customtkinter](https://github.com/TomSchimansky/CustomTkinter) (`pip install customtkinter`) | [Node.js](https://nodejs.org) |
| | [pnpm](https://pnpm.io) |

Building the `.exe` also needs [PyInstaller](https://pyinstaller.org) (installed automatically by the build script).

## Run from source

```bash
pip install customtkinter
python equicord_manager.py
```

## Build a standalone `.exe` (Windows)

Double-click **`build_equicord_manager.bat`** or:

```bash
pip install -r requirements-manager-build.txt
python -m PyInstaller --noconfirm --clean EquicordManager.spec
```

Output: **`dist/EquicordManager.exe`** (single file, no console window).

The executable does **not** bundle Git, Node, or pnpm—they must still be installed and on your `PATH` when you use clone/build/inject features.

### Build on GitHub (CI)

The repo includes [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml). On every push to `main` (and on pull requests), GitHub Actions runs PyInstaller on **windows-latest** and uploads **`EquicordManager.exe`** as a workflow artifact.

1. Push these files to GitHub (including `.github/workflows/build-windows.yml`).
2. Open the repo on GitHub → **Actions** → select **Build Windows exe** → open the latest run.
3. Under **Artifacts**, download **EquicordManager-Windows** (zip containing the `.exe`).

You can also run the workflow manually: **Actions** → **Build Windows exe** → **Run workflow**.

## What it does

- **Setup:** Clone Equicord into a folder you choose, or **Link folder** to an existing Equicord repo (must contain `package.json`).
- **Manager:** Lists plugins under `src/userplugins` with **title** and **description** from each plugin’s `definePlugin({ ... })`.
- **Install Custom Plugin:** Pick a plugin folder; it is copied into `src/userplugins/<name>`, then `pnpm build` and inject run with output in the built-in log.
- **Reinstall / Update:** Opens a **Command Prompt** in the repo and runs `git pull`, `pnpm install`, and `node scripts/runInstaller.mjs -- --install -branch …` (avoids `pnpm` mangling `--` on Windows).
- **Discord target:** `AUTO` / Stable / Canary / PTB maps to Equilotl’s `-branch` flag so inject is not stuck on the interactive picker.
- **Saved settings:** Linked repo path and inject branch are stored under:
  - Windows: `%APPDATA%\equicord_manager\settings.json`
  - Linux / macOS: `~/.config/equicord_manager/settings.json`

## Project layout

| File | Purpose |
|------|---------|
| `equicord_manager.py` | Application |
| `EquicordManager.spec` | PyInstaller configuration |
| `build_equicord_manager.bat` | Windows build helper |
| `requirements-manager-build.txt` | Dependencies to build the `.exe` |

## License

See [LICENSE](LICENSE) (MIT).
