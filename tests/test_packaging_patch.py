from tools.add_windows_desktop_shortcut import add_desktop_shortcut


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
