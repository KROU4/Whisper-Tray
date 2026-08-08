from tools.patch_windows_installer import add_desktop_shortcut, add_launch_after_install, sync_product_version


def test_add_windows_desktop_shortcut_is_idempotent():
    source = """<Wix>
            <ComponentRef Id="ApplicationShortcuts" />
        <Feature Id="DefaultFeature">
        </Feature>
</Wix>
"""
    patched = add_desktop_shortcut(source)
    assert 'Id="DesktopShortcut"' in patched
    assert '<ComponentRef Id="DesktopShortcutComponent" />' in patched
    assert add_desktop_shortcut(patched) == patched


def test_sync_product_version_updates_reused_wix_template():
    source = '<Package Name="WhisperTray" Version="1.0.0" Manufacturer="KROU4">'

    assert 'Version="1.1.1"' in sync_product_version(source, "1.1.1")


def test_add_launch_after_install_is_checked_and_idempotent():
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
    assert 'Event="DoAction"' in patched
    assert 'Condition="WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed"' in patched
    assert add_launch_after_install(patched) == patched
