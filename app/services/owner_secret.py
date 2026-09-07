from __future__ import annotations

import ctypes
import os
import secrets
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect_windows(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "HomeServer owner bootstrap secret",
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _unprotect_windows(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output),
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _encode(secret: str) -> bytes:
    raw = secret.encode("utf-8")
    return _protect_windows(raw) if os.name == "nt" else raw


def _decode(payload: bytes) -> str:
    raw = _unprotect_windows(payload) if os.name == "nt" else payload
    value = raw.decode("utf-8")
    if len(value) < 40:
        raise ValueError("Owner secret is invalid")
    return value


_RECOVERED_CORRUPT_SECRET = False


def load_or_create_owner_secret() -> str:
    global _RECOVERED_CORRUPT_SECRET
    path = settings.owner_secret_path
    if path.is_file():
        try:
            return _decode(path.read_bytes())
        except Exception:
            _RECOVERED_CORRUPT_SECRET = True
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            try:
                path.replace(path.with_name(f"{path.name}.invalid-{timestamp}"))
            except OSError:
                pass

    secret = secrets.token_urlsafe(48)
    _atomic_write(path, _encode(secret))
    return secret


def owner_secret_metadata() -> dict:
    return {
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
        "path": str(settings.owner_secret_path),
        "exists": settings.owner_secret_path.is_file(),
        "recovered_corrupt_secret": _RECOVERED_CORRUPT_SECRET,
    }
