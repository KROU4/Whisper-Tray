"""Add the installer-owned desktop shortcut missing from Briefcase's MSI template."""

from __future__ import annotations

import argparse
from pathlib import Path

DIRECTORY_MARKER = '        <Feature Id="DefaultFeature">'
COMPONENT_MARKER = '            <ComponentRef Id="ApplicationShortcuts" />'
DESKTOP_COMPONENT = """        <StandardDirectory Id="DesktopFolder">
            <Component Id="DesktopShortcutComponent">
                <Shortcut
                    Id="DesktopShortcut"
                    Name="WhisperTray"
                    Icon="ProductIcon"
                    Description="Privacy-aware desktop dictation."
                    Target="[INSTALLFOLDER]WhisperTray.exe"
                    WorkingDirectory="INSTALLFOLDER" />
                <RegistryValue
                    Root="HKMU"
                    Key="Software\\WhisperTray"
                    Name="desktopShortcut"
                    Type="integer"
                    Value="1"
                    KeyPath="yes" />
            </Component>
        </StandardDirectory>

"""


def add_desktop_shortcut(source: str) -> str:
    if 'Id="DesktopShortcut"' in source:
        return source
    if DIRECTORY_MARKER not in source or COMPONENT_MARKER not in source:
        raise ValueError("Unsupported Briefcase WiX template: required markers were not found")
    source = source.replace(DIRECTORY_MARKER, DESKTOP_COMPONENT + DIRECTORY_MARKER, 1)
    return source.replace(
        COMPONENT_MARKER,
        COMPONENT_MARKER + '\n            <ComponentRef Id="DesktopShortcutComponent" />',
        1,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("build/whispertray/windows/app/whispertray.wxs"),
    )
    args = parser.parse_args()
    original = args.path.read_text(encoding="utf-8")
    patched = add_desktop_shortcut(original)
    args.path.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
