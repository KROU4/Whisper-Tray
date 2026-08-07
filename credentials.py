"""Secure Groq credential storage without serialising secrets into app JSON."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

SERVICE_NAME = "WhisperTray"
TARGET_NAME = "WhisperTray/GroqApiKey"
KEY_NAME = "GroqApiKey"


class CredentialStorageError(RuntimeError):
    """Credential backend could not safely persist a secret."""


class CredentialStore:
    """Use keyring on every desktop platform, with legacy Windows fallback.

    Environment variables are read-only convenience for CI and portable runs;
    they are never written by this class.
    """

    def _keyring(self):
        try:
            import keyring

            return keyring
        except ImportError:
            return None

    def get_groq_key(self) -> str:
        keyring = self._keyring()
        if keyring is not None:
            try:
                return (keyring.get_password(SERVICE_NAME, KEY_NAME) or "").strip()
            except Exception:
                # Continue to the old Windows vault only for upgrades from v1.
                pass
        if sys.platform == "win32":
            try:
                value = self._read_windows()
                if value:
                    return value
            except OSError:
                pass
        return os.environ.get("GROQ_API_KEY", "").strip()

    def set_groq_key(self, value: str) -> None:
        value = value.strip()
        if not value:
            self.delete_groq_key()
            return
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.set_password(SERVICE_NAME, KEY_NAME, value)
                return
            except Exception as exc:
                raise CredentialStorageError("The operating-system credential vault is unavailable.") from exc
        if sys.platform == "win32":
            self._write_windows(value)
            return
        raise CredentialStorageError("Install keyring to store the Groq key securely on this platform.")

    def delete_groq_key(self) -> None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.delete_password(SERVICE_NAME, KEY_NAME)
            except Exception:
                pass  # no saved key is a successful deletion
        if sys.platform == "win32":
            try:
                ctypes.windll.advapi32.CredDeleteW(TARGET_NAME, 1, 0)
            except (AttributeError, OSError):
                pass

    @staticmethod
    def _credential_type():
        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", ctypes.c_byte * 8),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        return CREDENTIAL

    def _read_windows(self) -> str:
        credential_type = self._credential_type()
        pointer = ctypes.POINTER(credential_type)()
        if not ctypes.windll.advapi32.CredReadW(TARGET_NAME, 1, 0, ctypes.byref(pointer)):
            return ""
        try:
            size = pointer.contents.CredentialBlobSize
            return ctypes.string_at(pointer.contents.CredentialBlob, size).decode("utf-16-le")
        finally:
            ctypes.windll.advapi32.CredFree(pointer)

    def _write_windows(self, value: str) -> None:
        credential_type = self._credential_type()
        encoded = value.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(encoded)
        credential = credential_type()
        credential.Type, credential.TargetName, credential.Persist = 1, TARGET_NAME, 2
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        credential.UserName = SERVICE_NAME
        if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()
