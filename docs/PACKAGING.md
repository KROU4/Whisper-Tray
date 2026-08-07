# Native packaging and release

WhisperTray uses Briefcase 0.3.25. Each package is built on its target operating
system because native runtimes, signing tools, and installer formats are not
cross-compiled.

## Local preparation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

After source changes run `briefcase update <platform>`. After dependency changes
run `briefcase update -r <platform>`.

## Windows MSI

```powershell
python -m briefcase create windows --no-input
python tools/add_windows_desktop_shortcut.py
python -m briefcase build windows --no-input
python -m briefcase package windows --no-input
```

Test installation and removal in a clean Windows 10 and Windows 11 VM. Confirm
Start menu launch, microphone permission, shortcut registration, insertion into
at least two applications, and removal of installer-owned files.

## macOS DMG and PKG

```bash
python -m briefcase create macOS --no-input
python -m briefcase build macOS --no-input
python -m briefcase package macOS -p dmg --adhoc-sign --no-input
python -m briefcase package macOS -p pkg --adhoc-sign --no-input
```

Ad-hoc signing is suitable only for CI validation. A public release needs an
Apple Developer certificate and notarization. Test both Intel and Apple Silicon
when both architectures are distributed.

## Linux package

```bash
python -m briefcase create linux --no-input
python -m briefcase build linux --no-input
python -m briefcase package linux --no-input
```

The package is distribution-specific. The GitHub workflow currently builds on
Ubuntu. X11 is required for the global shortcut; Wayland is a documented runtime
limitation.

## Automated release

`.github/workflows/release.yml` runs on `v*` tags. It:

1. tests the source on each runner;
2. builds Windows, macOS, and Linux packages;
3. uploads each package as a workflow artifact;
4. creates one GitHub Release containing all packages.

Create a release only from a clean, reviewed main branch:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

## Release acceptance

- Full test suite and lint pass on all three operating systems.
- No API key, transcript, log, local config, or audio exists anywhere in Git history.
- Windows MSI installs and uninstalls on a clean VM.
- macOS packages launch after signing/notarization checks.
- Linux package installs on the target Ubuntu release.
- Privacy mode is verified offline.
- Speed mode is verified with a disposable user-owned Groq key.
- Package size and SHA-256 are recorded in release notes.

The local Whisper model is downloaded after installation and is never embedded
in a release package.
