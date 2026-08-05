# Enhanced Overhaul Launcher

Enhanced Overhaul Launcher is a Windows utility dedicated exclusively to
Enhanced Overhaul Remix for For The King II. This repository contains the
complete Python source used to build the standalone launcher executable.

## What the launcher does

- Detects Steam installations of For The King II or accepts a manually selected
  game directory.
- Installs an official Enhanced Overhaul `.7z` or `.zip` archive selected by the
  user after the user downloads it from Nexus Mods.
- Validates archive paths, package identity, package version, file count, and
  expanded size before installation.
- Records SHA-256 hashes in a local install manifest.
- Verifies installed files against that manifest.
- Backs up existing files before replacing them.
- Restores backed-up files during uninstall.
- Preserves user-modified managed files before removing them.
- Launches For The King II when requested.

The launcher does not contain the mod package and never downloads Nexus files.
It does not access game saves or collect telemetry. See
[NETWORK_BEHAVIOR.md](NETWORK_BEHAVIOR.md) for its limited network behavior.

## Requirements for building

- Windows 10 or Windows 11, 64-bit
- Python 3.11, 64-bit
- PowerShell 5.1 or newer

Players do not need Python or the build dependencies. PyInstaller packages the
launcher and its runtime dependencies into one executable.

## Reproducible build procedure

```powershell
git clone https://github.com/SirPepperPot/EnhancedOverhaulRemixLauncher.git
cd EnhancedOverhaulRemixLauncher

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

python -m unittest discover -s tests -p "test_launcher.py" -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\BuildLauncher.ps1
```

The output is written to:

```text
dist\EnhancedOverhaulLauncher.exe
```

The build script runs the test suite, creates the PyInstaller executable, runs
the compiled smoke test, and writes a SHA-256 checksum beside the executable.
No game installation, mod archive, private workspace, or proprietary game file
is required to build the launcher.

## Repository layout

```text
src/                 Launcher source
tests/               Automated tests
assets/              Author-owned launcher icons
third_party/7zip/    7-Zip 24.09 binaries and license
```

## Privacy and credentials

This repository and launcher contain no API keys, GitHub tokens, Nexus
credentials, user reports, logs, saves, or personal configuration files.
