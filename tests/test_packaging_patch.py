import re
import struct
from pathlib import Path

import pytest

from tools.patch_windows_installer import (
    LAUNCH_CHECKBOX_TEXT,
    SCOPE_BACK_MARKER,
    SCOPE_FINISH_MARKER,
    WELCOME_NEXT_MARKER,
    WIZARD_STRINGS,
    add_desktop_shortcut,
    add_install_wizard,
    add_launch_after_install,
    customize_wizard_text,
    sync_product_version,
)

ROOT = Path(__file__).resolve().parents[1]

# The parts of Briefcase's perUserOrMachine template that the wizard patch anchors to.
BRIEFCASE_UI = f"""<Wix>
        <UI>
            <DialogRef Id="UserExit" />
            <DialogRef Id="WelcomeDlg" />

            <!-- Scope handling has to work within the following constraints -->

            <!-- Let the user choose the scope. -->
{WELCOME_NEXT_MARKER}

{SCOPE_BACK_MARKER}

            <Publish
                Dialog="InstallScopeDlg"
                Control="Next"
                Order="1"
                Condition='WixAppFolder = "WixPerUserFolder"'
                Property="ALLUSERS"
                Value="{{}}" />

{SCOPE_FINISH_MARKER}
        </UI>
</Wix>
"""

BRIEFCASE_WXL = """<WixLocalization Culture="en-US" xmlns="http://wixtoolset.org/schemas/v4/wxl">
    <String
        Id="InstallScopeDlgPerMachineDescription"
        Value="[ProductName] will be installed in a per-machine folder." />
</WixLocalization>"""


def _assets(tmp_path):
    return [tmp_path / name for name in ("license.rtf", "banner.bmp", "dialog.bmp")]


def _new_dialog_routes(source: str) -> dict[tuple[str, str], str]:
    routes = {}
    for publish in re.findall(r"<Publish\b[^>]*/>", source):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', publish))
        if attrs.get("Event") == "NewDialog":
            routes[(attrs["Dialog"], attrs["Control"])] = attrs["Value"]
    return routes


def test_pinned_runtime_files_are_replaced_during_upgrade():
    from tools.patch_windows_installer import set_private_runtime_reinstall_mode

    source = '<Package Name="WhisperTray"><Media Id="1" /></Package>'
    patched = set_private_runtime_reinstall_mode(source)
    assert '<Property Id="REINSTALLMODE" Value="amus" />' in patched
    assert set_private_runtime_reinstall_mode(patched) == patched
    assert 'Value="amus"' in set_private_runtime_reinstall_mode(
        '<Package><Property Id="REINSTALLMODE" Value="omus" /></Package>'
    )


def test_high_compression_patch_is_idempotent():
    from tools.patch_windows_installer import set_cab_compression

    source = '<Media Id="1" Cabinet="product.cab" EmbedCab="yes" />'
    patched = set_cab_compression(source)
    assert 'CompressionLevel="high"' in patched
    assert set_cab_compression(patched) == patched
    assert 'CompressionLevel="medium"' in set_cab_compression(patched, "medium")
    with pytest.raises(ValueError):
        set_cab_compression('<Wix />')


def test_add_windows_desktop_shortcut_is_optional_and_idempotent():
    source = """<Wix>
            <ComponentRef Id="ApplicationShortcuts" />
        <Feature Id="DefaultFeature">
        </Feature>
</Wix>
"""
    patched = add_desktop_shortcut(source)
    assert '<Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" Secure="yes" />' in patched
    assert 'Condition="CREATE_DESKTOP_SHORTCUT = 1"' in patched
    assert 'Id="DesktopShortcut"' in patched
    assert '<ComponentRef Id="DesktopShortcutComponent" />' in patched
    assert add_desktop_shortcut(patched) == patched


def test_existing_desktop_shortcut_is_upgraded_to_optional():
    source = '''<StandardDirectory Id="DesktopFolder">
    <Component Id="DesktopShortcutComponent"><Shortcut Id="DesktopShortcut" /></Component>
</StandardDirectory>'''
    patched = add_desktop_shortcut(source)
    assert '<Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" Secure="yes" />' in patched
    assert 'Condition="CREATE_DESKTOP_SHORTCUT = 1"' in patched
    assert add_desktop_shortcut(patched) == patched


def test_existing_desktop_property_becomes_secure_for_elevated_installs():
    source = '''<Property Id="CREATE_DESKTOP_SHORTCUT" Value="1" />
<StandardDirectory Id="DesktopFolder">
    <Component Id="DesktopShortcutComponent" Condition="CREATE_DESKTOP_SHORTCUT = 1">
        <Shortcut Id="DesktopShortcut" />
    </Component>
</StandardDirectory>'''
    patched = add_desktop_shortcut(source)
    assert patched.count('Id="CREATE_DESKTOP_SHORTCUT"') == 1
    assert 'Secure="yes"' in patched


def test_sync_product_version_updates_reused_wix_template():
    source = '<Package Name="WhisperTray" Version="1.0.0" Manufacturer="KROU4">'
    assert 'Version="1.1.3"' in sync_product_version(source, "1.1.3")


def test_add_install_wizard_adds_expected_steps_and_branding(tmp_path):
    assets = _assets(tmp_path)
    patched = add_install_wizard(BRIEFCASE_UI, *assets)
    assert 'Id="InstallLocationDlg"' in patched
    assert 'Id="LicenseAgreementDlg"' in patched
    assert 'Id="VerifyReadyDlg"' in patched
    assert 'Id="InstallOptionsDlg"' not in patched
    assert 'Id="InstallDirDlg"' not in patched
    assert 'Property="CREATE_DESKTOP_SHORTCUT"' in patched
    assert 'Property="WIXUI_INSTALLDIR" Indirect="yes"' in patched
    assert 'Control="ChangeFolder" Property="_BrowseProperty"' in patched
    assert 'Event="CheckTargetPath"' in patched
    assert 'Event="SetTargetPath"' in patched
    assert 'Condition=\'LicenseAccepted = "1"\'' in patched
    assert 'Dialog="VerifyReadyDlg" Control="Install" Event="EndDialog"' not in patched
    assert '<Property Id="WHISPERTRAY_INSTALLER_UI_REVISION" Value="3" />' in patched
    assert str(Path(assets[0]).resolve()) in patched
    assert "{\\WixUI_Font_Title}Install location" in patched
    # Scope publishes from Briefcase stay intact.
    assert 'Property="ALLUSERS"' in patched
    assert add_install_wizard(patched, *assets) == patched


def test_install_wizard_flow_runs_license_first_and_back_navigation_mirrors_it(tmp_path):
    routes = _new_dialog_routes(add_install_wizard(BRIEFCASE_UI, *_assets(tmp_path)))
    forward = ["WelcomeDlg", "LicenseAgreementDlg", "InstallScopeDlg", "InstallLocationDlg", "VerifyReadyDlg"]
    for current, following in zip(forward, forward[1:]):
        assert routes[(current, "Next")] == following
        assert routes[(following, "Back")] == current
    assert ("InstallScopeDlg", "Next") in routes  # the template's EndDialog shortcut is gone
    assert "EndDialog" not in add_install_wizard(BRIEFCASE_UI, *_assets(tmp_path))


def test_install_wizard_rejects_templates_patched_by_an_older_revision(tmp_path):
    older = BRIEFCASE_UI.replace(
        "        <UI>",
        '        <Property Id="WHISPERTRAY_INSTALLER_UI_REVISION" Value="2" />\n        <UI>',
    ).replace(WELCOME_NEXT_MARKER, '            <Dialog Id="InstallOptionsDlg" />')
    with pytest.raises(ValueError, match="briefcase create windows"):
        add_install_wizard(older, *_assets(tmp_path))


@pytest.mark.parametrize("marker", [WELCOME_NEXT_MARKER, SCOPE_BACK_MARKER, SCOPE_FINISH_MARKER, "<!-- Scope handling"])
def test_install_wizard_requires_every_template_marker(tmp_path, marker):
    with pytest.raises(ValueError, match="wizard markers"):
        add_install_wizard(BRIEFCASE_UI.replace(marker, ""), *_assets(tmp_path))


def test_add_launch_after_install_uses_full_installed_path_and_is_idempotent():
    source = """<Wix>
        <UI>
            <Publish
                Dialog="ExitDialog"
                Control="Finish"
                Event="EndDialog"
                Value="Return"
                Order="999" />
        </UI>
</Wix>
"""
    patched = add_launch_after_install(source)
    assert f'Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="{LAUNCH_CHECKBOX_TEXT}"' in patched
    assert 'Id="WIXUI_EXITDIALOGOPTIONALTEXT" Value="WhisperTray runs in the system tray' in patched
    assert 'Id="WIXUI_EXITDIALOGOPTIONALCHECKBOX" Value="1"' in patched
    assert 'Id="LaunchWhisperTray"' in patched
    assert 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"' in patched
    assert 'Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed"' in patched
    assert add_launch_after_install(patched) == patched


def test_existing_launch_action_is_upgraded_to_full_path():
    source = '<CustomAction Id="LaunchWhisperTray" Directory="INSTALLFOLDER" ExeCommand="WhisperTray.exe" />'
    assert 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"' in add_launch_after_install(source)


def test_existing_launch_option_gets_current_copy():
    source = """        <Property Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="Launch WhisperTray" />
        <CustomAction Id="LaunchWhisperTray" ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show" />
"""
    patched = add_launch_after_install(source)
    assert f'Value="{LAUNCH_CHECKBOX_TEXT}"' in patched
    assert 'Value="Launch WhisperTray"' not in patched
    assert '        <Property Id="WIXUI_EXITDIALOGOPTIONALTEXT"' in patched
    assert add_launch_after_install(patched) == patched


def test_customize_wizard_text_overrides_stock_strings_once():
    patched = customize_wizard_text(BRIEFCASE_WXL)
    for string_id in WIZARD_STRINGS:
        assert patched.count(f'<String Id="{string_id}"') == 1
    assert 'Id="InstallScopeDlgPerMachineDescription"' in patched
    assert patched.rstrip().endswith("</WixLocalization>")
    assert customize_wizard_text(patched) == patched


def test_customize_wizard_text_replaces_stale_override():
    stale = BRIEFCASE_WXL.replace(
        "</WixLocalization>", '    <String Id="WelcomeDlgDescription" Value="Old text" />\n</WixLocalization>'
    )
    patched = customize_wizard_text(stale)
    assert "Old text" not in patched
    assert patched.count('Id="WelcomeDlgDescription"') == 1


def test_customize_wizard_text_rejects_unknown_localization_file():
    with pytest.raises(ValueError):
        customize_wizard_text("<Wix />")


@pytest.mark.parametrize(("name", "size"), [("banner.bmp", (493, 58)), ("dialog.bmp", (493, 312))])
def test_installer_bitmaps_match_wixui_format(name, size):
    data = (ROOT / "installer" / name).read_bytes()
    assert data[:2] == b"BM"
    width, height, planes, bits, compression = struct.unpack_from("<iiHHI", data, 18)
    assert (width, abs(height)) == size
    assert (planes, bits, compression) == (1, 24, 0)
