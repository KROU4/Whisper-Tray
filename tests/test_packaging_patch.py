from pathlib import Path

from tools.patch_windows_installer import (
    add_desktop_shortcut,
    add_install_wizard,
    add_launch_after_install,
    sync_product_version,
)


def test_add_windows_desktop_shortcut_is_optional_and_idempotent():
    source = """<Wix>
            <ComponentRef Id="ApplicationShortcuts" />
        <Feature Id="DefaultFeature">
        </Feature>
</Wix>
"""
    patched = add_desktop_shortcut(source)
    assert 'Id="CREATE_DESKTOP_SHORTCUT" Value="1"' in patched
    assert 'Condition="CREATE_DESKTOP_SHORTCUT = 1"' in patched
    assert 'Id="DesktopShortcut"' in patched
    assert '<ComponentRef Id="DesktopShortcutComponent" />' in patched
    assert add_desktop_shortcut(patched) == patched


def test_existing_desktop_shortcut_is_upgraded_to_optional():
    source = '''<StandardDirectory Id="DesktopFolder">
    <Component Id="DesktopShortcutComponent"><Shortcut Id="DesktopShortcut" /></Component>
</StandardDirectory>'''
    patched = add_desktop_shortcut(source)
    assert 'Id="CREATE_DESKTOP_SHORTCUT" Value="1"' in patched
    assert 'Condition="CREATE_DESKTOP_SHORTCUT = 1"' in patched


def test_sync_product_version_updates_reused_wix_template():
    source = '<Package Name="WhisperTray" Version="1.0.0" Manufacturer="KROU4">'
    assert 'Version="1.1.3"' in sync_product_version(source, "1.1.3")


def test_add_install_wizard_adds_expected_steps_and_branding(tmp_path):
    source = """<Wix>
        <UI>
            <DialogRef Id="WelcomeDlg" />
            <!-- Scope handling
            <Publish
                Dialog="InstallScopeDlg"
                Control="Next"
                Order="99"
                Event="EndDialog"
                Value="Return" />
        </UI>
</Wix>
"""
    assets = [tmp_path / name for name in ("license.rtf", "banner.bmp", "dialog.bmp")]
    patched = add_install_wizard(source, *assets)
    assert 'Id="InstallDirDlg"' in patched
    assert 'Id="LicenseAgreementDlg"' in patched
    assert 'Id="InstallOptionsDlg"' in patched
    assert 'Id="VerifyReadyDlg"' in patched
    assert 'Property="CREATE_DESKTOP_SHORTCUT"' in patched
    assert 'Control="ChangeFolder" Property="_BrowseProperty"' in patched
    assert 'Event="SetTargetPath"' in patched
    assert 'Condition=\'LicenseAccepted = "1"\'' in patched
    assert 'Dialog="VerifyReadyDlg" Control="Install" Event="EndDialog"' not in patched
    assert str(Path(assets[0]).resolve()) in patched
    assert add_install_wizard(patched, *assets) == patched


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
    assert 'Id="WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT" Value="Launch WhisperTray"' in patched
    assert 'Id="WIXUI_EXITDIALOGOPTIONALCHECKBOX" Value="1"' in patched
    assert 'Id="LaunchWhisperTray"' in patched
    assert 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"' in patched
    assert 'Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed"' in patched
    assert add_launch_after_install(patched) == patched


def test_existing_launch_action_is_upgraded_to_full_path():
    source = '<CustomAction Id="LaunchWhisperTray" Directory="INSTALLFOLDER" ExeCommand="WhisperTray.exe" />'
    assert 'ExeCommand="&quot;[INSTALLFOLDER]WhisperTray.exe&quot; --show"' in add_launch_after_install(source)
