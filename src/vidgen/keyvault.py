"""API keys in Windows Credential Manager, not in settings.json as plain text.

Each key is a "generic credential" named ``AIVideoStudio/<name>``, readable only by the
signed-in Windows user (Control Panel > Credential Manager > Windows Credentials shows it).
Windows' own advapi32 functions through ctypes: no extra library.
Where Credential Manager can't be used (not Windows, or it refuses), ``available()`` is
False and the caller keeps the key where it was, so a key is never lost.
"""
from __future__ import annotations

import ctypes
import sys

PREFIX = "AIVideoStudio/"
_GENERIC = 1                 # CRED_TYPE_GENERIC
_LOCAL_MACHINE = 2           # CRED_PERSIST_LOCAL_MACHINE: this user, this PC, kept after sign-out


if sys.platform == "win32":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", _FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    try:
        _api = ctypes.WinDLL("advapi32", use_last_error=True)
        _api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
        _api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        _api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        _api.CredFree.argtypes = [ctypes.c_void_p]
    except (OSError, AttributeError):
        _api = None
else:
    _api = None


def available() -> bool:
    return _api is not None


def get(name: str) -> str | None:
    """The stored key, "" when there is none, None when Credential Manager can't be asked."""
    if _api is None:
        return None
    found = ctypes.POINTER(_CREDENTIAL)()
    if not _api.CredReadW(PREFIX + name, _GENERIC, 0, ctypes.byref(found)):
        return "" if ctypes.get_last_error() == 1168 else None   # 1168: ERROR_NOT_FOUND
    try:
        cred = found.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return blob.decode("utf-16-le", "replace")
    finally:
        _api.CredFree(found)


def put(name: str, value: str) -> bool:
    """Store (or, for "", delete) a key. True when Windows took it."""
    if _api is None:
        return False
    if not value:
        return delete(name)
    blob = value.encode("utf-16-le")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL(Type=_GENERIC, TargetName=PREFIX + name,
                       Comment="AI Video Studio API key", CredentialBlobSize=len(blob),
                       CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                       Persist=_LOCAL_MACHINE, UserName="AI Video Studio")
    return bool(_api.CredWriteW(ctypes.byref(cred), 0))


def delete(name: str) -> bool:
    """Remove a key. True when it is gone (or was never there)."""
    if _api is None:
        return False
    if _api.CredDeleteW(PREFIX + name, _GENERIC, 0):
        return True
    return ctypes.get_last_error() == 1168
