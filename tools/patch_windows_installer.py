"""Patch Briefcase's WiX template with the WhisperTray install experience."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import tomllib

DIRECTORY_MARKER = '        <Feature Id="DefaultFeature">'
COMPONENT_MARKER = '            <ComponentRef Id="ApplicationShortcuts" />'
UI_MARKER = "        <UI>"
EXIT_PUBLISH_MARKER = """            <Publish
                Dialog="ExitDialog"
                Control="Finish"
                Event="EndDialog"
                Value="Return"
                Order="999" />"""
SCOPE_FINISH_MARKER = """            <Publish
                Dialog="InstallScopeDlg"
                Control="Next"
                Order="99"
                Event="EndDialog"
                Value="Return" />"""

DESKTOP_COMPONENT = """        <Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" />

        <StandardDirectory Id="DesktopFolder">
            <Component Id="DesktopShortcutComponent" Condition="CREATE_DESKTOP_SHORTCUT = 1">
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

INSTALL_OPTIONS_DIALOG = """            <Dialog
                Id="InstallOptionsDlg"
                Width="370"
                Height="270"
                Title="[ProductName] Setup">
                <Control Id="BannerBitmap" Type="Bitmap" X="0" Y="0" Width="370" Height="44"
                    TabSkip="yes" Text="WixUI_Bmp_Banner" />
                <Control Id="Title" Type="Text" X="15" Y="7" Width="300" Height="15"
                    Transparent="yes" NoPrefix="yes" Text="{\\WixUI_Font_Title}Installation options" />
                <Control Id="Description" Type="Text" X="25" Y="55" Width="320" Height="30"
                    Text="Choose the shortcuts that WhisperTray should create." />
                <Control Id="DesktopShortcutCheckBox" Type="CheckBox" X="25" Y="98" Width="320" Height="18"
                    Property="CREATE_DESKTOP_SHORTCUT" CheckBoxValue="1"
                    Text="Create a shortcut on the Desktop" />
                <Control Id="BottomLine" Type="Line" X="0" Y="234" Width="370" Height="0" />
                <Control Id="Back" Type="PushButton" X="180" Y="243" Width="56" Height="17"
                    Text="!(loc.WixUIBack)" />
                <Control Id="Next" Type="PushButton" X="236" Y="243" Width="56" Height="17"
                    Default="yes" Text="!(loc.WixUINext)" />
                <Control Id="Cancel" Type="PushButton" X="304" Y="243" Width="56" Height="17"
                    Cancel="yes" Text="!(loc.WixUICancel)">
                    <Publish Event="SpawnDialog" Value="CancelDlg" />
                </Control>
            </Dialog>

"""

WIZARD_REFS = """            <DialogRef Id="InstallDirDlg" />
            <DialogRef Id="LicenseAgreementDlg" />
            <DialogRef Id="VerifyReadyDlg" />
"""

WIZARD_FLOW = """            <Publish
                Dialog="InstallScopeDlg"
                Control="Next"
                Order="99"
                Event="NewDialog"
                Value="InstallDirDlg" />

            <Publish Dialog="InstallDirDlg" Control="Back" Event="NewDialog" Value="InstallScopeDlg" />
            <Publish Dialog="InstallDirDlg" Control="Next" Event="CheckTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallDirDlg" Control="Next" Event="SetTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="3" />
            <Publish Dialog="InstallDirDlg" Control="Next" Event="NewDialog"
                Value="LicenseAgreementDlg" Order="4" />
            <Publish Dialog="InstallDirDlg" Control="ChangeFolder" Property="_BrowseProperty"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallDirDlg" Control="ChangeFolder" Event="SpawnDialog"
                Value="BrowseDlg" Order="2" />
            <Publish Dialog="LicenseAgreementDlg" Control="Back" Event="NewDialog" Value="InstallDirDlg" />
            <Publish Dialog="LicenseAgreementDlg" Control="Next" Event="NewDialog" Value="InstallOptionsDlg"
                Condition='LicenseAccepted = "1"' />
            <Publish Dialog="InstallOptionsDlg" Control="Back" Event="NewDialog" Value="LicenseAgreementDlg" />
            <Publish Dialog="InstallOptionsDlg" Control="Next" Event="NewDialog" Value="VerifyReadyDlg" />
            <Publish Dialog="VerifyReadyDlg" Control="Back" Event="NewDialog" Value="InstallOptionsDlg"
                Order="1" Condition="NOT Installed" />"""


def _xml_path(path: Path) -> str:
    return str(path.resolve()).replace("&", "&amp;")


def add_desktop_shortcut(source: str) -> str:
    """Add an optional Desktop shortcut controlled from the wizard."""
    if 'Id="DesktopShortcut"' in source:
        if 'Id="CREATE_DESKTOP_SHORTCUT"' not in source:
            source, count = re.subn(
                r'(?P<indent>^[ \t]*)<StandardDirectory Id="DesktopFolder">',
                r'\g<indent><Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" />\n\n'
                r'\g<indent><StandardDirectory Id="DesktopFolder">',
                source,
                count=1,
                flags=re.MULTILINE,
            )
            if count != 1:
                raise ValueError("Unsupported Briefcase WiX template: DesktopFolder marker was not found")
        source = source.replace(
            '<Component Id="DesktopShortcutComponent">',
            '<Component Id="DesktopShortcutComponent" Condition="CREATE_DESKTOP_SHORTCUT = 1">',
            1,
        )
        return source
    if DIRECTORY_MARKER not in source or COMPONENT_MARKER not in source:
        raise ValueError("Unsupported Briefcase WiX template: required markers were not found")
    source = source.replace(DIRECTORY_MARKER, DESKTOP_COMPONENT + DIRECTORY_MARKER, 1)
    return source.replace(
        COMPONENT_MARKER,
        COMPONENT_MARKER + '\n            <ComponentRef Id="DesktopShortcutComponent" />',
        1,
    )


def add_install_wizard(source: str, license_path: Path, banner_path: Path, dialog_path: Path) -> str:
    """Add directory, license, shortcut, and review steps to Briefcase's WiX UI."""
    if 'Id="InstallOptionsDlg"' in source:
        if 'Id="WHISPERTRAY_INSTALLER_UI_REVISION"' in source:
            return source
        old_next = '            <Publish Dialog="InstallDirDlg" Control="Next" Event="NewDialog" Value="LicenseAgreementDlg" />'
        new_next = """            <Publish Dialog="InstallDirDlg" Control="Next" Event="CheckTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallDirDlg" Control="Next" Event="SetTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="3" />
            <Publish Dialog="InstallDirDlg" Control="Next" Event="NewDialog"
                Value="LicenseAgreementDlg" Order="4" />
            <Publish Dialog="InstallDirDlg" Control="ChangeFolder" Property="_BrowseProperty"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallDirDlg" Control="ChangeFolder" Event="SpawnDialog"
                Value="BrowseDlg" Order="2" />"""
        replacements = {
            old_next: new_next,
            '            <Publish Dialog="LicenseAgreementDlg" Control="Next" Event="NewDialog" Value="InstallOptionsDlg" />':
                '            <Publish Dialog="LicenseAgreementDlg" Control="Next" Event="NewDialog" '
                'Value="InstallOptionsDlg" Condition=\'LicenseAccepted = "1"\' />',
            '            <Publish Dialog="VerifyReadyDlg" Control="Back" Event="NewDialog" Value="InstallOptionsDlg" />':
                '            <Publish Dialog="VerifyReadyDlg" Control="Back" Event="NewDialog" '
                'Value="InstallOptionsDlg" Order="1" Condition="NOT Installed" />',
            '            <Publish Dialog="VerifyReadyDlg" Control="Install" Event="EndDialog" Value="Return" />': "",
        }
        for old, new in replacements.items():
            if old not in source:
                raise ValueError("Unsupported existing WhisperTray installer wizard")
            source = source.replace(old, new, 1)
        return source.replace(
            UI_MARKER,
            '        <Property Id="WHISPERTRAY_INSTALLER_UI_REVISION" Value="2" />\n\n' + UI_MARKER,
            1,
        )
    required = (UI_MARKER, SCOPE_FINISH_MARKER, '<DialogRef Id="WelcomeDlg" />')
    if any(marker not in source for marker in required):
        raise ValueError("Unsupported Briefcase WiX template: wizard markers were not found")

    branding = f"""        <Property Id="WHISPERTRAY_INSTALLER_UI_REVISION" Value="2" />
        <WixVariable Id="WixUILicenseRtf" Value="{_xml_path(license_path)}" />
        <WixVariable Id="WixUIBannerBmp" Value="{_xml_path(banner_path)}" />
        <WixVariable Id="WixUIDialogBmp" Value="{_xml_path(dialog_path)}" />

"""
    source = source.replace(UI_MARKER, branding + UI_MARKER, 1)
    source = source.replace(
        '            <DialogRef Id="WelcomeDlg" />',
        '            <DialogRef Id="WelcomeDlg" />\n' + WIZARD_REFS,
        1,
    )
    source = source.replace('            <!-- Scope handling', INSTALL_OPTIONS_DIALOG + '            <!-- Scope handling', 1)
    return source.replace(SCOPE_FINISH_MARKER, WIZARD_FLOW, 1)


def add_launch_after_install(source: str) -> str:
    """Add a checked launch option using the full installed executable path."""
    if 'Id="LaunchWhisperTray"' in source:
        source = source.replace(
            'ExeCommand="WhisperTray.exe"',
            'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"',
            1,
        )
        return source.replace(
            'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot;"',
            'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"',
            1,
        )
    if UI_MARKER not in source or EXIT_PUBLISH_MARKER not in source:
        raise ValueError("Unsupported Briefcase WiX template: exit dialog markers were not found")
    action = """        <Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="Launch WhisperTray" />
        <Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOX" Value="1" />
        <CustomAction
            Id="LaunchWhisperTray"
            Directory="INSTALLFOLDER"
            ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"
            Execute="immediate"
            Impersonate="yes"
            Return="asyncNoWait" />

"""
    publish = """            <Publish
                Dialog="ExitDialog"
                Control="Finish"
                Event="DoAction"
                Value="LaunchWhisperTray"
                Order="1"
                Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed" />

"""
    source = source.replace(UI_MARKER, action + UI_MARKER, 1)
    return source.replace(EXIT_PUBLISH_MARKER, publish + EXIT_PUBLISH_MARKER, 1)


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
    parser.add_argument("path", nargs="?", type=Path, default=Path("build/whispertray/windows/app/whispertray.wxs"))
    parser.add_argument("--version", help="MSI product version; defaults to project.version from pyproject.toml")
    parser.add_argument("--license", type=Path, default=Path("installer/license.rtf"))
    parser.add_argument("--banner", type=Path, default=Path("installer/banner.bmp"))
    parser.add_argument("--dialog", type=Path, default=Path("installer/dialog.bmp"))
    args = parser.parse_args()
    version = args.version
    if version is None:
        with Path("pyproject.toml").open("rb") as pyproject:
            version = str(tomllib.load(pyproject)["project"]["version"])
    for asset in (args.license, args.banner, args.dialog):
        if not asset.is_file():
            raise FileNotFoundError(f"Installer asset not found: {asset}")
    original = args.path.read_text(encoding="utf-8")
    patched = sync_product_version(original, version)
    patched = add_desktop_shortcut(patched)
    patched = add_install_wizard(patched, args.license, args.banner, args.dialog)
    patched = add_launch_after_install(patched)
    args.path.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
