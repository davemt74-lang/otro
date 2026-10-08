"""Ephemeral per-worker Chromium actor: all browser objects stay on one thread.

The model has no access to this actor. Only the user-authorized mission service
can submit commands. No local filesystem, cookies, CDP websocket or debugging
interface is exposed. Browser contexts die on stop, expiry or restart.
"""
from __future__ import annotations
import base64
import queue
import threading
import time
from concurrent.futures import Future
from urllib.parse import urljoin, urlsplit
from . import agent_browser_policy as policy
from .agent_mission_runtime import MissionError

MAX_SESSIONS = 2
SESSION_SECONDS = 600
MAX_LINKS = 24
MAX_IMAGE_BYTES = 145000
MAX_TEXT = 9000
_registry: dict[str, "LiveActor"] = {}
_guard = threading.RLock()


class LiveActor:
    def __init__(self, task_id: str, origin: str, pinned_ip: str, initial_url: str):
        self.task_id = task_id
        self.origin = origin
        self.pinned_ip = pinned_ip
        self.initial_url = initial_url
        self.started = time.monotonic()
        self.expires = self.started + SESSION_SECONDS
        self.requests: queue.Queue = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="vp3-browser-" + task_id[:8], daemon=True)
        self.thread.start()

    def _run(self):
        browser = None
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                args = ["--disable-background-networking", "--disable-sync",
                        "--disable-extensions", "--no-first-run",
                        "--host-resolver-rules=MAP " +
                        str(urlsplit(self.origin).hostname) + " " + self.pinned_ip]
                try:
                    browser = playwright.chromium.launch(channel="chrome", headless=True, args=args)
                except Exception:
                    browser = playwright.chromium.launch(headless=True, args=args)
                context = browser.new_context(
                    viewport={"width": 960, "height": 670},
                    java_script_enabled=False, service_workers="block",
                    accept_downloads=False, ignore_https_errors=False, permissions=[])
                page = context.new_page()
                def restricted(route):
                    request = route.request
                    try:
                        _, _, origin = policy.parse_url(request.url)
                        if origin != self.origin or request.method.upper() != "GET":
                            route.abort()
                        else:
                            route.continue_()
                    except Exception:
                        route.abort()
                page.route("**/*", restricted)
                while not self.stopped.is_set():
                    remaining = self.expires - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        kind, url, future = self.requests.get(timeout=min(1.0, remaining))
                    except queue.Empty:
                        continue
                    if future.cancelled():
                        continue
                    try:
                        if kind == "stop":
                            self.stopped.set()
                            future.set_result({"stopped": True})
                            break
                        if kind not in ("navigate", "snapshot"):
                            raise MissionError("Unsupported browser command.", 422)
                        if kind == "navigate":
                            validated, _, origin = policy.parse_url(url)
                            if origin != self.origin:
                                raise MissionError("Origin is not approved.", 403)
                            response = page.goto(validated, wait_until="domcontentloaded", timeout=15000)
                            if response is None or response.status >= 400:
                                raise MissionError("Browser navigation failed.", 502)
                        if not page.url.startswith("https://"):
                            raise MissionError("Browser has no public HTTPS page.", 403)
                        _, _, origin = policy.parse_url(page.url)
                        if origin != self.origin:
                            raise MissionError("Browser left the approved origin.", 403)
                        title = page.title()[:180]
                        body = page.locator("body").inner_text(timeout=4000)[:MAX_TEXT]
                        links = []
                        seen = set()
                        for anchor in page.locator("a[href]").all()[:100]:
                            href = anchor.get_attribute("href") or ""
                            target = urljoin(page.url, href)
                            try:
                                target, _, origin = policy.parse_url(target)
                            except MissionError:
                                continue
                            if origin != self.origin or target in seen:
                                continue
                            seen.add(target)
                            links.append({
                                "url": target,
                                "title": (anchor.inner_text(timeout=1000) or target)[:130],
                            })
                            if len(links) >= MAX_LINKS:
                                break
                        jpeg = page.screenshot(type="jpeg", quality=35, full_page=False,
                                               animations="disabled", timeout=7000)
                        image = base64.b64encode(jpeg).decode("ascii") if len(jpeg) <= MAX_IMAGE_BYTES else ""
                        future.set_result({
                            "url": page.url[:1400], "page_title": title, "text_snapshot": body,
                            "image_base64": image, "links": links,
                        })
                    except Exception as exc:
                        future.set_exception(exc)
                context.close()
        except Exception as exc:
            while True:
                try:
                    _, _, pending = self.requests.get_nowait()
                except queue.Empty:
                    break
                if not pending.done():
                    pending.set_exception(MissionError("Live browser failed: " + str(exc)[:160], 503))
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            self.stopped.set()
            with _guard:
                if _registry.get(self.task_id) is self:
                    _registry.pop(self.task_id, None)

    def submit(self, kind: str, url: str = "") -> dict:
        if self.stopped.is_set() or time.monotonic() >= self.expires:
            raise MissionError("Browser session expired or stopped.", 409)
        future = Future()
        try:
            self.requests.put_nowait((kind, url, future))
        except queue.Full as exc:
            raise MissionError("Browser session is busy.", 409) from exc
        try:
            return future.result(timeout=32)
        except TimeoutError as exc:
            future.cancel()
            raise MissionError("Browser session timed out; stop and retry safely.", 504) from exc

    def stop(self):
        self.stopped.set()
        # Expiry and cancellation may occur while a navigation is executing.
        # The service must fence all late results before any DB write.
        try:
            self.requests.put_nowait(("stop", "", Future()))
        except queue.Full:
            pass


def open_session(task_id: str, origin: str, pinned_ip: str, url: str) -> dict:
    with _guard:
        for key, actor in list(_registry.items()):
            if actor.stopped.is_set() or time.monotonic() >= actor.expires:
                actor.stop()
                _registry.pop(key, None)
        if task_id in _registry:
            raise MissionError("Worker already owns a browser session.", 409)
        if len(_registry) >= MAX_SESSIONS:
            raise MissionError("HomeServer browser session limit reached.", 409)
        actor = LiveActor(task_id, origin, pinned_ip, url)
        _registry[task_id] = actor
    try:
        return actor.submit("navigate", url)
    except Exception:
        close_session(task_id)
        raise


def execute(task_id: str, command: str, url: str = "") -> dict:
    with _guard:
        actor = _registry.get(task_id)
    if actor is None:
        raise MissionError("Browser session is not active; reauthorize to reopen.", 409)
    return actor.submit(command, url)


def active(task_id: str) -> bool:
    with _guard:
        actor = _registry.get(task_id)
    return bool(actor and not actor.stopped.is_set() and time.monotonic() < actor.expires)


def close_session(task_id: str) -> None:
    with _guard:
        actor = _registry.pop(task_id, None)
    if actor:
        actor.stop()


def shutdown() -> None:
    with _guard:
        keys = list(_registry)
    for key in keys:
        close_session(key)
