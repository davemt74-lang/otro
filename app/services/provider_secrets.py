from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from ..config import settings


CRYPTPROTECT_UI_FORBIDDEN = 0x1
PROVIDERS = ("anthropic", "openai", "openrouter", "elevenlabs")


class ProviderSecretError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _secret_path() -> Path:
    return settings.data_dir / "security" / "provider-credentials.dat"


def _windows_libraries():
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _free_blob(kernel32, blob: _DataBlob) -> None:
    if blob.pbData:
        kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))
        blob.pbData = ctypes.POINTER(ctypes.c_ubyte)()
        blob.cbData = 0


def _protect_windows(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    try:
        if not crypt32.CryptProtectData(
            ctypes.byref(source), "HomeServer provider credentials", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(data))
        _free_blob(kernel32, output)


def _unprotect_windows(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    try:
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        ):
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(data))
        _free_blob(kernel32, output)


def _encode(data: dict[str, str]) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _protect_windows(raw) if os.name == "nt" else raw


def _decode(payload: bytes) -> dict[str, str]:
    raw = _unprotect_windows(payload) if os.name == "nt" else payload
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ProviderSecretError("Provider credential store is invalid.")
    result: dict[str, str] = {}
    for provider in PROVIDERS:
        value = str(parsed.get(provider) or "").strip()
        if value:
            result[provider] = value
    return result


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def load_credentials() -> dict[str, str]:
    path = _secret_path()
    if not path.is_file():
        return {}
    try:
        return _decode(path.read_bytes())
    except Exception as exc:
        raise ProviderSecretError("Provider credentials could not be decrypted on this device.") from exc


def credential_status() -> dict:
    credentials = load_credentials()
    items = {}
    for provider in PROVIDERS:
        value = credentials.get(provider, "")
        items[provider] = {
            "configured": bool(value),
            "suffix": value[-4:] if value else "",
        }
    return {
        "providers": items,
        "protection": "windows-dpapi" if os.name == "nt" else "restricted-local-file",
    }


def save_credentials(updates: dict[str, str | None], clear: list[str] | None = None) -> dict:
    credentials = load_credentials()
    for provider in clear or []:
        if provider not in PROVIDERS:
            raise ProviderSecretError(f"Unknown provider: {provider}")
        credentials.pop(provider, None)
    for provider, raw_value in updates.items():
        if provider not in PROVIDERS:
            raise ProviderSecretError(f"Unknown provider: {provider}")
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        if len(value) > 4000:
            raise ProviderSecretError(f"{provider} API key is too long.")
        credentials[provider] = value
    _atomic_write(_secret_path(), _encode(credentials))
    return credential_status()


def get_api_key(provider: str) -> str | None:
    if provider not in PROVIDERS:
        raise ProviderSecretError(f"Unknown provider: {provider}")
    return load_credentials().get(provider)
