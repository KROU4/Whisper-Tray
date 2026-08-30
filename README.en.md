# WhisperTray

[Website](https://krou4.github.io/Whisper-Tray/en/) | [Русский](README.md) | [English](README.en.md)

<p align="center">
  <img src="assets/whispertray-logo.png" width="160" alt="WhisperTray logo">
</p>

WhisperTray is a desktop dictation app for Windows, macOS, and Linux. Press a
global shortcut, speak, and the app transcribes your speech and inserts the
result into the active window. It can run locally or use Groq when you
explicitly choose the cloud profile and provide your own API key.

> Current release: [WhisperTray 1.1.3](https://github.com/KROU4/Whisper-Tray/releases/latest).
> Native installers are built automatically for Windows, macOS, and Linux.

## Features

- Records from the selected microphone, with a ten-minute limit per dictation.
- Local `faster-whisper` transcription in the Privacy profile.
- Groq transcription in the Speed profile with your API key.
- Recording, processing, result, and error status in the window, overlay, and
  tray.
- Independent launch-at-login and start-in-tray controls in Settings.
- Transcribes individual audio and video files.
- Optional local transcript history.

## Profiles and privacy

| Profile | Where audio goes | Fallback |
| --- | --- | --- |
| Privacy | It stays on the device and uses a local Whisper model | None |
| Speed | It is sent to Groq | A local model only when explicitly enabled in Settings |

Privacy is the default. A Groq failure never changes the profile or sends audio
to another service. The Groq API key is stored in the operating-system
credential vault, not in `config.json`.

## Getting a Groq API key

The key is required only for the Speed profile. The local Privacy profile does
not require a Groq account.

1. Sign in to [GroqCloud Console](https://console.groq.com/) or create an
   account.
2. Open [API Keys](https://console.groq.com/keys).
3. Select **Create API Key**, enter a recognizable name such as
   `WhisperTray`, and copy the generated key.
4. Select the Speed profile in WhisperTray. You can enter the key during the
   first run or later under Settings → General → Groq API key.
5. Select Test key, then save the settings.

Do not put the key in `config.json`, send it to other people, or publish it on
GitHub. WhisperTray stores it in the operating-system credential vault. If a
key is exposed, delete it in GroqCloud Console and create a new one.

### Groq limits

WhisperTray uses `whisper-large-v3-turbo` by default. Groq currently lists the
following base limits for this model on the Free plan:

| Limit | Free plan |
| --- | ---: |
| Requests per minute | 20 |
| Requests per day | 2,000 |
| Audio per hour | 7,200 seconds, or 2 hours |
| Audio per day | 28,800 seconds, or 8 hours |
| Maximum uploaded file size | 25 MB |

Whichever limit is reached first applies. When a quota is exhausted, Groq
returns `429 Too Many Requests`; transcription can continue after the quota
resets. The exact limits for an account are always available on the
[GroqCloud Limits](https://console.groq.com/settings/limits) page because Groq
may change them independently of WhisperTray releases. Groq also publishes
reference tables in [Rate Limits](https://console.groq.com/docs/rate-limits)
and [Speech to Text](https://console.groq.com/docs/speech-to-text).

WhisperTray also has its own ten-minute limit per dictation. This does not
increase or replace the Groq quotas. For paid usage, the official
`whisper-large-v3-turbo` price at the time of this README update is $0.04 per
hour of processed audio, and the Developer plan accepts files up to 100 MB.

## Install and first run

Download the installer for your OS from [GitHub Releases](https://github.com/KROU4/Whisper-Tray/releases):

- Windows: `.msi`
- macOS: `.dmg` or `.pkg`
- Linux: the native package built for Ubuntu by the release workflow

On Windows, the setup wizard lets you choose the destination folder, review
the license agreement, optionally create a Desktop shortcut, and open the
WhisperTray main window when installation finishes.

The first run uses three short steps: choose local or cloud processing, prepare
that transcription method, and configure the microphone and hotkey. Then:

1. In Privacy, prepare or download a local model in Settings.
2. In Speed, enter and test a Groq key. It is saved only after it can be
   stored in the system credential vault.
3. To change the shortcut, open Settings → General, select Change next to the
   hotkey, and press the new key combination.

Local models are not part of the installer and download on demand. The sizes
shown in the app range from about 75 MB (`tiny`) to 2.9 GB (`large`).

### Permissions and limitations

- Windows: allow microphone access in Privacy settings.
- macOS: allow Microphone and Accessibility access. Accessibility is required
  for global shortcuts and text insertion.
- Linux: the microphone must be available to the desktop session. Global
  shortcuts work on X11; on Wayland the app explicitly reports that they are
  unavailable.

## Data location

| System | Application directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\\WhisperTray` |
| macOS | `~/Library/Application Support/WhisperTray` |
| Linux | `$XDG_DATA_HOME/WhisperTray` or `~/.local/share/WhisperTray` |

This directory holds settings, technical logs, optional history, and file
transcription results. Logs exclude audio, transcript text, and credentials.

## Development

Python 3.10 or later is required. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python main.py
```

On macOS and Linux, activate the environment with `source .venv/bin/activate`.
Check changes with:

```powershell
python -m pytest -q
ruff check .
python -m compileall -q .
```

`requirements.txt` and `requirements-dev.txt` are pinned dependency files
generated from their matching `.in` files with `uv pip compile`.

## Packaging and releases

WhisperTray uses Briefcase for native packages, so each package must be built
on its target OS. The `.github/workflows/release.yml` workflow runs for `v*`
tags, tests the project on Windows, macOS, and Ubuntu, builds native packages,
and creates a GitHub Release. A local Windows build uses:

```powershell
python -m briefcase create windows --no-input
python -m briefcase build windows --no-input
python -m briefcase package windows --no-input
```

For macOS or Linux, replace `windows` with `macOS` or `linux` and run the
commands on that operating system.

## Security

Never commit `config.json`, logs, audio, transcript history, or API keys. If a
key enters Git history, revoke it immediately. See the
[security policy](.github/SECURITY.md) for vulnerability reporting guidance.
