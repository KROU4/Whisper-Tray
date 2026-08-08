from tools.add_windows_desktop_shortcut import add_desktop_shortcut, sync_product_version


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

    assert 'Version="1.1.0"' in sync_product_version(source, "1.1.0")
