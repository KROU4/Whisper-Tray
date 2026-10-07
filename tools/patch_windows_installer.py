"""Patch Briefcase's WiX template with the WhisperTray install experience."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from xml.sax.saxutils import escape

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
WELCOME_NEXT_MARKER = """            <Publish
                Dialog="WelcomeDlg"
                Control="Next"
                Event="NewDialog"
                Value="InstallScopeDlg" />"""
SCOPE_BACK_MARKER = """            <Publish
                Dialog="InstallScopeDlg"
                Control="Back"
                Event="NewDialog"
                Value="WelcomeDlg" />"""
SCOPE_COMMENT_MARKER = "            <!-- Scope handling"
WELCOME_REF_MARKER = '            <DialogRef Id="WelcomeDlg" />'
LOCALIZATION_END_MARKER = "</WixLocalization>"

# Bump when the generated wizard changes; templates patched by an older
# revision must be regenerated with `briefcase create windows`.
UI_REVISION = "3"

DESKTOP_PROPERTY = '<Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" Secure="yes" />'
DESKTOP_COMPONENT = f"""        {DESKTOP_PROPERTY}

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

# Folder and shortcut choices share one step, laid out like WixUI's InstallDirDlg.
INSTALL_LOCATION_DIALOG = """            <Dialog
                Id="InstallLocationDlg"
                Width="370"
                Height="270"
                Title="[ProductName] Setup">
                <Control Id="Next" Type="PushButton" X="236" Y="243" Width="56" Height="17"
                    Default="yes" Text="!(loc.WixUINext)" />
                <Control Id="Back" Type="PushButton" X="180" Y="243" Width="56" Height="17"
                    Text="!(loc.WixUIBack)" />
                <Control Id="Cancel" Type="PushButton" X="304" Y="243" Width="56" Height="17"
                    Cancel="yes" Text="!(loc.WixUICancel)">
                    <Publish Event="SpawnDialog" Value="CancelDlg" />
                </Control>
                <Control Id="BannerBitmap" Type="Bitmap" X="0" Y="0" Width="370" Height="44"
                    TabSkip="yes" Text="WixUI_Bmp_Banner" />
                <Control Id="BannerLine" Type="Line" X="0" Y="44" Width="370" Height="0" />
                <Control Id="BottomLine" Type="Line" X="0" Y="234" Width="370" Height="0" />
                <Control Id="Title" Type="Text" X="15" Y="6" Width="290" Height="15"
                    Transparent="yes" NoPrefix="yes" Text="{\\WixUI_Font_Title}Install location" />
                <Control Id="Description" Type="Text" X="25" Y="23" Width="280" Height="15"
                    Transparent="yes" NoPrefix="yes"
                    Text="Choose where to install [ProductName] and which shortcuts to add." />
                <Control Id="FolderLabel" Type="Text" X="20" Y="60" Width="290" Height="12"
                    NoPrefix="yes" Text="!(loc.InstallDirDlgFolderLabel)" />
                <Control Id="Folder" Type="PathEdit" X="20" Y="74" Width="320" Height="18"
                    Property="WIXUI_INSTALLDIR" Indirect="yes" />
                <Control Id="ChangeFolder" Type="PushButton" X="20" Y="96" Width="56" Height="17"
                    Text="!(loc.InstallDirDlgChange)" />
                <Control Id="ShortcutsLabel" Type="Text" X="20" Y="132" Width="290" Height="12"
                    NoPrefix="yes" Text="{\\WixUI_Font_Emphasized}Shortcuts" />
                <Control Id="DesktopShortcutCheckBox" Type="CheckBox" X="20" Y="147" Width="320" Height="14"
                    Property="CREATE_DESKTOP_SHORTCUT" CheckBoxValue="1"
                    Text="Add a &amp;desktop shortcut" />
                <Control Id="StartMenuNote" Type="Text" X="32" Y="163" Width="308" Height="12"
                    NoPrefix="yes" Text="[ProductName] is always added to the Start menu." />
            </Dialog>

"""

WIZARD_REFS = """            <DialogRef Id="LicenseAgreementDlg" />
            <DialogRef Id="VerifyReadyDlg" />
"""

# Welcome -> License -> Scope -> Location (+ shortcuts) -> Ready.
LICENSE_FLOW = """            <Publish Dialog="WelcomeDlg" Control="Next" Event="NewDialog" Value="LicenseAgreementDlg" />
            <Publish Dialog="LicenseAgreementDlg" Control="Back" Event="NewDialog" Value="WelcomeDlg" />
            <Publish Dialog="LicenseAgreementDlg" Control="Next" Event="NewDialog" Value="InstallScopeDlg"
                Condition='LicenseAccepted = "1"' />"""

SCOPE_BACK_FLOW = """            <Publish Dialog="InstallScopeDlg" Control="Back" Event="NewDialog" Value="LicenseAgreementDlg" />"""

WIZARD_FLOW = """            <Publish
                Dialog="InstallScopeDlg"
                Control="Next"
                Order="99"
                Event="NewDialog"
                Value="InstallLocationDlg" />

            <Publish Dialog="InstallLocationDlg" Control="Back" Event="NewDialog" Value="InstallScopeDlg" />
            <Publish Dialog="InstallLocationDlg" Control="Next" Event="CheckTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallLocationDlg" Control="Next" Event="SetTargetPath"
                Value="[WIXUI_INSTALLDIR]" Order="3" />
            <Publish Dialog="InstallLocationDlg" Control="Next" Event="NewDialog"
                Value="VerifyReadyDlg" Order="4" />
            <Publish Dialog="InstallLocationDlg" Control="ChangeFolder" Property="_BrowseProperty"
                Value="[WIXUI_INSTALLDIR]" Order="1" />
            <Publish Dialog="InstallLocationDlg" Control="ChangeFolder" Event="SpawnDialog"
                Value="BrowseDlg" Order="2" />
            <Publish Dialog="VerifyReadyDlg" Control="Back" Event="NewDialog" Value="InstallLocationDlg"
                Order="1" Condition="NOT Installed" />"""

LAUNCH_CHECKBOX_TEXT = "Open WhisperTray now"
EXIT_OPTIONAL_TEXT = (
    "WhisperTray runs in the system tray, next to the clock. "
    "If you don't see its icon, click the ^ arrow to show hidden icons."
)

# Friendlier wording for stock WixUI strings; [ProductName] is resolved by MSI.
WIZARD_STRINGS = {
    "WelcomeDlgDescription": (
        "Setup will install [ProductName], privacy-aware dictation for your desktop. "
        "Click Next to continue, or Cancel to exit."
    ),
    "LicenseAgreementDlgDescription": "Read the license terms and accept them to continue",
    "InstallScopeDlgDescription": "Choose who can use [ProductName] on this computer",
    "ExitDialogDescription": "[ProductName] is installed and ready to use. Click Finish to close Setup.",
}


def _xml_path(path: Path) -> str:
    return str(path.resolve()).replace("&", "&amp;")


def add_desktop_shortcut(source: str) -> str:
    """Add an optional Desktop shortcut controlled from the wizard."""
    if 'Id="DesktopShortcut"' in source:
        if 'Id="CREATE_DESKTOP_SHORTCUT"' not in source:
            source, count = re.subn(
                r'(?P<indent>^[ \t]*)<StandardDirectory Id="DesktopFolder">',
                rf'\g<indent>{DESKTOP_PROPERTY}\n\n'
                r'\g<indent><StandardDirectory Id="DesktopFolder">',
                source,
                count=1,
                flags=re.MULTILINE,
            )
            if count != 1:
                raise ValueError("Unsupported Briefcase WiX template: DesktopFolder marker was not found")
        # Secure, so the checkbox value also reaches an elevated per-machine install.
        source = source.replace('<Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" />', DESKTOP_PROPERTY, 1)
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
    """Add license, location/shortcut, and review steps to Briefcase's WiX UI."""
    revision = f'<Property Id="WHISPERTRAY_INSTALLER_UI_REVISION" Value="{UI_REVISION}" />'
    if revision in source:
        return source
    if 'Id="WHISPERTRAY_INSTALLER_UI_REVISION"' in source or 'Id="InstallOptionsDlg"' in source:
        raise ValueError(
            "WiX template was patched by an older WhisperTray installer revision; "
            "run `briefcase create windows` to regenerate it"
        )
    required = (
        UI_MARKER,
        WELCOME_NEXT_MARKER,
        SCOPE_BACK_MARKER,
        SCOPE_FINISH_MARKER,
        SCOPE_COMMENT_MARKER,
        WELCOME_REF_MARKER,
    )
    if any(marker not in source for marker in required):
        raise ValueError("Unsupported Briefcase WiX template: wizard markers were not found")

    branding = f"""        {revision}
        <WixVariable Id="WixUILicenseRtf" Value="{_xml_path(license_path)}" />
        <WixVariable Id="WixUIBannerBmp" Value="{_xml_path(banner_path)}" />
        <WixVariable Id="WixUIDialogBmp" Value="{_xml_path(dialog_path)}" />

"""
    source = source.replace(UI_MARKER, branding + UI_MARKER, 1)
    source = source.replace(WELCOME_REF_MARKER, WELCOME_REF_MARKER + "\n" + WIZARD_REFS, 1)
    source = source.replace(SCOPE_COMMENT_MARKER, INSTALL_LOCATION_DIALOG + SCOPE_COMMENT_MARKER, 1)
    source = source.replace(WELCOME_NEXT_MARKER, LICENSE_FLOW, 1)
    source = source.replace(SCOPE_BACK_MARKER, SCOPE_BACK_FLOW, 1)
    return source.replace(SCOPE_FINISH_MARKER, WIZARD_FLOW, 1)


def add_launch_after_install(source: str) -> str:
    """Add a checked launch option using the full installed executable path."""
    checkbox_text = f'<Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="{LAUNCH_CHECKBOX_TEXT}" />'
    optional_text = f'<Property Id="WIXUI_EXITDIALOGOPTIONALTEXT" Value="{EXIT_OPTIONAL_TEXT}" />'
    if 'Id="LaunchWhisperTray"' in source:
        command = 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"'
        for old_command in ('ExeCommand="WhisperTray.exe"', 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot;"'):
            source = source.replace(old_command, command, 1)
        source = re.sub(
            r'<Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="[^"]*" />',
            lambda _match: checkbox_text,
            source,
            count=1,
        )
        if 'Id="WIXUI_EXITDIALOGOPTIONALTEXT"' not in source:
            source = re.sub(
                r'(?P<indent>^[ \t]*)(?P<checkbox><Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT"[^>]*/>)',
                lambda match: f"{match['indent']}{match['checkbox']}\n{match['indent']}{optional_text}",
                source,
                count=1,
                flags=re.MULTILINE,
            )
        return source
    if UI_MARKER not in source or EXIT_PUBLISH_MARKER not in source:
        raise ValueError("Unsupported Briefcase WiX template: exit dialog markers were not found")
    action = f"""        {checkbox_text}
        {optional_text}
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


def customize_wizard_text(localization: str) -> str:
    """Override stock WixUI strings in Briefcase's unicode.wxl, updating earlier overrides in place."""
    if LOCALIZATION_END_MARKER not in localization:
        raise ValueError("Unsupported Briefcase WiX localization: </WixLocalization> was not found")
    for string_id, value in WIZARD_STRINGS.items():
        element = f'<String Id="{string_id}" Value="{escape(value, {chr(34): "&quot;"})}" />'
        pattern = rf'<String\s+Id="{string_id}"\s+Value="[^"]*"\s*/>'
        localization, count = re.subn(pattern, lambda _match, element=element: element, localization, count=1)
        if not count:
            localization = localization.replace(
                LOCALIZATION_END_MARKER, f"    {element}\n{LOCALIZATION_END_MARKER}", 1
            )
    return localization


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


def set_cab_compression(source: str, level: str = "high") -> str:
    """Set compression reproducibly, without changing the installed payload."""
    if level not in {"medium", "high"}:
        raise ValueError("Unsupported compression level")

    def replace(match):
        media = re.sub(r'\s+CompressionLevel="[^"]*"', "", match.group(0))
        return media.replace("/>", f'CompressionLevel="{level}" />')

    patched, count = re.subn(r'<Media\b[^>]*\bCabinet="[^"]+"[^>]*/>', replace, source)
    if not count:
        raise ValueError("Unsupported Briefcase WiX template: embedded cabinet was not found")
    return patched


def set_private_runtime_reinstall_mode(source: str) -> str:
    """Replace pinned private DLLs even when the previous bundle has newer versions.

    MSI costs files before removing the old product. Its default version rules
    can skip copying a DLL that the old product subsequently removes.
    All files here belong to this app's private installation directory.
    https://learn.microsoft.com/en-us/windows/win32/msi/reinstallmode
    """
    property_xml = '<Property Id="REINSTALLMODE" Value="amus" />'
    pattern = r'<Property\b[^>]*\bId="REINSTALLMODE"[^>]*/>'
    if re.search(pattern, source):
        return re.sub(pattern, property_xml, source)
    patched, count = re.subn(r'(<Package\b[^>]*>)', r'\1\n        ' + property_xml, source, count=1)
    if not count:
        raise ValueError("Unsupported WiX template: Package was not found")
    return patched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path("build/whispertray/windows/app/whispertray.wxs"))
    parser.add_argument("--version", help="MSI product version; defaults to project.version from pyproject.toml")
    parser.add_argument("--compression", choices=("medium", "high"), default="high")
    parser.add_argument("--license", type=Path, default=Path("installer/license.rtf"))
    parser.add_argument("--banner", type=Path, default=Path("installer/banner.bmp"))
    parser.add_argument("--dialog", type=Path, default=Path("installer/dialog.bmp"))
    parser.add_argument("--localization", type=Path, help="WiX .wxl file; defaults to unicode.wxl beside the .wxs")
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
    patched = set_cab_compression(patched, args.compression)
    patched = set_private_runtime_reinstall_mode(patched)
    localization_path = args.localization or args.path.with_name("unicode.wxl")
    localization = customize_wizard_text(localization_path.read_text(encoding="utf-8"))
    args.path.write_text(patched, encoding="utf-8")
    localization_path.write_text(localization, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
