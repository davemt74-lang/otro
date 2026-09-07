from __future__ import annotations

import hashlib
import os
from pathlib import Path


ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, data_dir: str | Path):
        normalized = os.path.normcase(os.path.abspath(str(data_dir)))
        digest = hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:20]
        self.name = f"Local\\HomeServer-{digest}"
        self._handle = None
        self._fallback_fd: int | None = None
        self._fallback_path = Path(data_dir) / "runtime" / "instance.lock"

    def acquire(self) -> bool:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True

        # Development fallback. Production Windows builds use the named mutex above.
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fallback_fd = os.open(self._fallback_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.write(self._fallback_fd, str(os.getpid()).encode("ascii"))
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        if self._handle is not None:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
            self._handle = None
        if self._fallback_fd is not None:
            try:
                os.close(self._fallback_fd)
            finally:
                self._fallback_fd = None
                try:
                    self._fallback_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise RuntimeError("HomeServer is already running for this data directory")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
