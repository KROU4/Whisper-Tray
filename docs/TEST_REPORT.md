# Release test report

Date: 2026-08-08  
Host: Windows 11, Python 3.12, CPU fallback for local Whisper

## Automated checks

- 33 tests passed.
- Ruff passed.
- Python bytecode compilation passed.
- Dependency consistency check passed.
- Current-tree secret scan passed.

## Real audio transcription

Windows Speech Synthesis generated a 16 kHz WAV containing:

> Привет. Это реальная проверка распознавания речи в приложении Whisper Tray.

The real `faster-whisper` `tiny` model processed the WAV locally on CPU. It
returned:

> Привет. Это реальная проверка распознавания речи в приложении У и спертрей.

This proves the real WAV, decoder, local model and normalization path. It also
shows why the smallest model is not the recommended accuracy baseline.

## Windows MSI

- Native MSI build completed.
- Silent installation returned exit code 0.
- Start menu shortcut was created.
- Desktop shortcut was created.
- Installed application stayed running after launch.
- First-run onboarding rendered from the installed bundle.
- Silent uninstall returned exit code 0.
- Both installer-owned shortcuts were removed.

Final Windows artifact at the time of this report:

- File: `dist/WhisperTray-1.0.0.msi`
- Size: 135.8 MiB
- SHA-256: `610E779A3022C1106925ECDDC8E3D07DCFA7F5E1448248C94C88287AD9A6C9D8`

The checksum must be refreshed after any subsequent rebuild.

## Visual evidence

`artifacts/screenshots` contains native Windows captures for onboarding,
settings, diagnostics, tray menu, all five main states, and all HUD terminal
states. Screenshot review cannot prove screen-reader behavior or every DPI and
multi-monitor combination.

## Cross-platform limits

macOS and Linux packages are configured for target-host CI builds. They were
not executed on this Windows host. Public macOS distribution still requires
Apple signing and notarization. Linux global shortcuts currently require X11.
