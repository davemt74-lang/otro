from __future__ import annotations

import os
import sys
from pathlib import Path

# PyInstaller's Windows no-console bootloader sets standard streams to None.
# Restore harmless sinks before importing Uvicorn or other console-aware libraries.
if sys.stdin is None:
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from desktop.bootstrap import ensure_loopback_proxy_bypass, prepare_data_directory  # noqa: E402

# HomeServer's API is loopback-only. Ambient machine/user proxy settings must
# never intercept its own health checks or permission-enforced local dispatch.
ensure_loopback_proxy_bypass()
BOOTSTRAP_STATE = prepare_data_directory()

import json  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
import webbrowser  # noqa: E402

import pystray  # noqa: E402
import uvicorn  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from app.config import settings  # noqa: E402
from app.security import OWNER_CONTROL_TOKEN  # noqa: E402
from app.services import backups  # noqa: E402
from app.services.remote_bridge import RemoteBridgeWorker  # noqa: E402
from app.services.restore_runtime import apply_pending_restore_for_startup  # noqa: E402
from app.services.runtime_control import register_runtime_handler  # noqa: E402
from app.services.windows_integration import open_folder  # noqa: E402
from desktop.single_instance import SingleInstance  # noqa: E402
from desktop.update_runtime import spawn_pending_update  # noqa: E402


EXIT_ALREADY_RUNNING = 23
EXIT_SERVER_NOT_READY = 24
WATCHDOG_FAILURE_WINDOW_SECONDS = 300
WATCHDOG_STABLE_RESET_SECONDS = 60


def _icon() -> Image.Image:
    image = Image.new("RGB", (64, 64), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill="black")
    draw.rectangle((20, 20, 44, 44), fill="white")
    return image


def _open(path: str = "/") -> bool:
    url = f"http://{settings.host}:{settings.port}{path}"
    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except Exception:
        return False


def _authorized_path(next_path: str) -> str:
    next_query = urllib.parse.urlencode({"next": next_path})
    return f"/assets/authorize.html?{next_query}#owner={OWNER_CONTROL_TOKEN}"


def _recovery_path() -> str:
    return f"/#owner={OWNER_CONTROL_TOKEN}"


def _path_for_health(health: dict) -> str:
    return _recovery_path() if health.get("recovery") is True else _authorized_path("/")


def _wait_until_listening() -> dict | None:
    url = f"http://{settings.host}:{settings.port}/api/v1/health"
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("version") == settings.version:
                    return payload
        except Exception:
            time.sleep(0.2)
    return None


def _apply_staged_restore_before_server() -> None:
    try:
        apply_pending_restore_for_startup()
    except backups.BackupError:
        # The restore layer either rolls normal state back or leaves unreadable
        # live data quarantined/recoverable. Continue into normal preflight so a
        # restricted recovery server can start instead of restart-looping.
        pass


def _select_runtime_app():
    try:
        from app.database import initialize_database
        from app.services.knowledge import ensure_knowledge_index

        initialize_database()
        ensure_knowledge_index()
        from app.runtime import app as runtime_app

        return runtime_app, False, None
    except Exception as exc:
        from app.recovery import build_recovery_app

        reason = f"{type(exc).__name__}: database runtime initialization failed"
        return build_recovery_app(reason), True, reason


def _watchdog_state_path() -> Path:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    return settings.runtime_dir / "watchdog-state.json"


def _read_watchdog_state() -> dict:
    path = _watchdog_state_path()
    if not path.is_file():
        return {"count": 0, "first_failure_at": 0.0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"count": 0, "first_failure_at": 0.0}
    if not isinstance(payload, dict):
        return {"count": 0, "first_failure_at": 0.0}
    try:
        count = max(0, int(payload.get("count") or 0))
        first = max(0.0, float(payload.get("first_failure_at") or 0.0))
    except (TypeError, ValueError):
        return {"count": 0, "first_failure_at": 0.0}
    return {"count": count, "first_failure_at": first}


def _write_watchdog_state(count: int, first_failure_at: float) -> None:
    path = _watchdog_state_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "count": max(0, int(count)),
                "first_failure_at": max(0.0, float(first_failure_at)),
                "updated_at": time.time(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _reset_watchdog_state() -> None:
    try:
        _watchdog_state_path().unlink(missing_ok=True)
    except OSError:
        pass


def _register_watchdog_failure(max_failures: int) -> bool:
    now = time.time()
    state = _read_watchdog_state()
    first = float(state["first_failure_at"])
    count = int(state["count"])
    if first <= 0 or now - first > WATCHDOG_FAILURE_WINDOW_SECONDS:
        first = now
        count = 0
    count += 1
    try:
        _write_watchdog_state(count, first)
    except OSError:
        # Fail closed on restart-loop prevention if the state cannot be stored.
        return False
    return count < max(1, int(max_failures))


def _restart_command() -> list[str]:
    args = [arg for arg in sys.argv[1:] if arg != "--restart-child"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args, "--restart-child"]
    return [sys.executable, str(Path(__file__).resolve()), *args, "--restart-child"]


def _restart_environment() -> dict[str, str]:
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # PyInstaller 6.9+ treats sys.executable children as worker processes by
        # default. A self-restart must be a new top-level onefile instance so it
        # unpacks into its own temporary directory and can outlive this process.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


class RuntimeController:
    def __init__(self, asgi_app, *, recovery_mode: bool = False):
        self.recovery_mode = recovery_mode
        self.config = uvicorn.Config(
            asgi_app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        self.server = uvicorn.Server(self.config)
        self.thread: threading.Thread | None = None
        self.tray: pystray.Icon | None = None
        self.remote_bridge = None if recovery_mode else RemoteBridgeWorker()
        self.restart_requested = False
        self.shutdown_requested = False
        self.update_requested = False
        self._command_lock = threading.Lock()
        self._stop_scheduled = False
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_started_at = 0.0
        self._watchdog_state_reset = False
        self._watchdog_max_failures = 3

    def _run_server(self) -> None:
        self.server.run()

    def start_threaded(self) -> None:
        self.thread = threading.Thread(target=self._run_server, name="homeserver-api", daemon=False)
        self.thread.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(1.0):
            thread = self.thread
            if thread is None or thread.is_alive():
                if (
                    not self._watchdog_state_reset
                    and self._watchdog_started_at > 0
                    and time.monotonic() - self._watchdog_started_at
                    >= WATCHDOG_STABLE_RESET_SECONDS
                ):
                    _reset_watchdog_state()
                    self._watchdog_state_reset = True
                continue
            with self._command_lock:
                intentional = (
                    self.restart_requested
                    or self.shutdown_requested
                    or self.update_requested
                )
                if intentional:
                    return
                self.restart_requested = _register_watchdog_failure(
                    self._watchdog_max_failures
                )
                self._stop_scheduled = True
            if self.tray is not None:
                try:
                    self.tray.stop()
                except Exception:
                    pass
            return

    def start_watchdog(self) -> None:
        try:
            from app.services.device_rollout import get_settings
            rollout = get_settings()
            enabled = bool(rollout.get("watchdog_enabled"))
            self._watchdog_max_failures = max(
                1,
                int(rollout.get("max_failed_starts") or 3),
            )
        except Exception:
            enabled = True
            self._watchdog_max_failures = 3
        if not enabled:
            return
        self._watchdog_stop.clear()
        self._watchdog_started_at = time.monotonic()
        self._watchdog_state_reset = False
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="homeserver-runtime-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop_watchdog(self) -> None:
        self._watchdog_stop.set()
        thread = self._watchdog_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._watchdog_thread = None

    def start_remote_bridge(self) -> None:
        if self.remote_bridge is not None:
            self.remote_bridge.start()

    def stop_remote_bridge(self) -> None:
        if self.remote_bridge is not None:
            self.remote_bridge.stop()

    def stop_server(self) -> None:
        self.server.should_exit = True
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                self.server.force_exit = True
                self.thread.join(timeout=3)

    def _finish_runtime_command(self) -> None:
        self.server.should_exit = True
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass

    def handle_command(self, command: str) -> bool:
        if command not in {"restart", "shutdown", "apply_update"}:
            return False
        with self._command_lock:
            if command == "restart":
                self.restart_requested = True
            elif command == "apply_update":
                self.update_requested = True
            else:
                self.shutdown_requested = True
            if not self._stop_scheduled:
                self._stop_scheduled = True
                # Give the HTTP request that initiated restart/shutdown a chance
                # to flush its response before Uvicorn begins graceful shutdown.
                threading.Timer(0.25, self._finish_runtime_command).start()
        return True

    def run_headless(self) -> None:
        register_runtime_handler(self.handle_command)
        self.start_remote_bridge()
        try:
            self.server.run()
        finally:
            self.stop_remote_bridge()
            register_runtime_handler(None)

    def open_control_center(self, _icon=None, _item=None) -> None:
        _open(_recovery_path() if self.recovery_mode else _authorized_path("/"))

    def open_system(self, _icon=None, _item=None) -> None:
        _open(_recovery_path() if self.recovery_mode else _authorized_path("/system"))

    def open_remote_bridge(self, _icon=None, _item=None) -> None:
        _open(_recovery_path() if self.recovery_mode else _authorized_path("/remote"))

    def open_tasks(self, _icon=None, _item=None) -> None:
        _open(_recovery_path() if self.recovery_mode else _authorized_path("/tasks"))

    def open_api_docs(self, _icon=None, _item=None) -> None:
        if self.recovery_mode:
            self.open_control_center()
        else:
            _open(_authorized_path("/docs"))

    def open_data_folder(self, _icon=None, _item=None) -> None:
        open_folder(settings.data_dir)

    def create_backup(self, icon: pystray.Icon | None = None, _item=None) -> None:
        try:
            item = backups.create_backup("tray")
            if icon is not None:
                icon.notify(f"Created {item['name']}", "HomeServer backup")
        except Exception:
            if icon is not None:
                icon.notify("Backup could not be created. Open HomeServer for diagnostics.", "HomeServer backup")

    def restart(self, _icon=None, _item=None) -> None:
        self.handle_command("restart")

    def quit(self, _icon=None, _item=None) -> None:
        self.handle_command("shutdown")

    def _schedule_initial_open(self) -> None:
        # A recovery condition should always be visible to the owner. Normal
        # Windows sign-in startup is intentionally background-only; an explicit
        # user launch should always open the appropriate HomeServer workspace.
        if self.recovery_mode:
            threading.Timer(0.6, self.open_control_center).start()
            return
        if "--background" in sys.argv:
            return

        try:
            from app.services.system_state import first_run_status, mark_first_run_prompted

            setup = first_run_status()
            if not setup["complete"]:
                if not setup["prompted"]:
                    mark_first_run_prompted()
                threading.Timer(0.6, self.open_system).start()
                return
        except Exception:
            pass

        threading.Timer(0.6, self.open_control_center).start()

    def run_tray(self) -> None:
        self.start_threaded()
        health = _wait_until_listening()
        if health is None:
            self.stop_server()
            raise SystemExit(EXIT_SERVER_NOT_READY)

        self.start_remote_bridge()
        self.start_watchdog()
        register_runtime_handler(self.handle_command)
        try:
            self.tray = pystray.Icon(
                "homeserver",
                _icon(),
                "HomeServer Recovery" if self.recovery_mode else "HomeServer",
                menu=pystray.Menu(
                    pystray.MenuItem("Open HomeServer", self.open_control_center, default=True),
                    pystray.MenuItem("Tasks & Notifications", self.open_tasks, enabled=not self.recovery_mode),
                    pystray.MenuItem("Setup & Diagnostics", self.open_system),
                    pystray.MenuItem("Remote Bridge", self.open_remote_bridge, enabled=not self.recovery_mode),
                    pystray.MenuItem("Open Data Folder", self.open_data_folder),
                    pystray.MenuItem("Create Backup", self.create_backup, enabled=not self.recovery_mode),
                    pystray.MenuItem("API Docs", self.open_api_docs, enabled=not self.recovery_mode),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Restart HomeServer", self.restart),
                    pystray.MenuItem("Quit", self.quit),
                ),
            )
            self._schedule_initial_open()
            self.tray.run()
        finally:
            register_runtime_handler(None)
            self.stop_watchdog()
            self.stop_remote_bridge()
            self.stop_server()


def main() -> None:
    instance = SingleInstance(settings.data_dir)
    if not instance.acquire():
        # Background/headless starts preserve the historical process-contract
        # exit code. An explicit user launch acts as "Open HomeServer" when the
        # server is already alive instead of failing silently behind the mutex.
        if "--headless" in sys.argv or "--background" in sys.argv:
            raise SystemExit(EXIT_ALREADY_RUNNING)
        health = _wait_until_listening()
        if health is not None:
            _open(_path_for_health(health))
            return
        raise SystemExit(EXIT_ALREADY_RUNNING)

    restart_requested = False
    update_requested = False
    try:
        _apply_staged_restore_before_server()
        asgi_app, recovery_mode, _reason = _select_runtime_app()
        controller = RuntimeController(asgi_app, recovery_mode=recovery_mode)
        if "--headless" in sys.argv:
            controller.run_headless()
        else:
            controller.run_tray()
        restart_requested = controller.restart_requested
        update_requested = controller.update_requested
    finally:
        instance.release()

    if update_requested:
        time.sleep(0.15)
        if not spawn_pending_update():
            subprocess.Popen(
                _restart_command(),
                close_fds=True,
                env=_restart_environment(),
            )
    elif restart_requested:
        time.sleep(0.15)
        subprocess.Popen(
            _restart_command(),
            close_fds=True,
            env=_restart_environment(),
        )


if __name__ == "__main__":
    main()
