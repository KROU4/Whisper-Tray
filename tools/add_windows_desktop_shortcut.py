"""Add the installer-owned desktop shortcut missing from Briefcase's MSI template."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import tomllib

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


def sync_product_version(source: str, version: str) -> str:
    """Keep a reused Briefcase/WiX template aligned with pyproject.toml."""
    patched, count = re.subn(
        r'(<Package\b[^>]*\bVersion=")[^"]+("[^>]*>)',
        rf"\g<1>{version}\g<2>",
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("Unsupported Briefcase WiX template: package version was not found")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("build/whispertray/windows/app/whispertray.wxs"),
    )
    parser.add_argument("--version", help="MSI product version; defaults to project.version from pyproject.toml")
    args = parser.parse_args()
    version = args.version
    if version is None:
        with Path("pyproject.toml").open("rb") as pyproject:
            version = str(tomllib.load(pyproject)["project"]["version"])
    original = args.path.read_text(encoding="utf-8")
    patched = add_desktop_shortcut(sync_product_version(original, version))
    args.path.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
