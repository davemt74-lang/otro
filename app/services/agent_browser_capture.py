"""Read-only Chromium capture. Fresh context for each worker visit."""
from __future__ import annotations
import base64
from urllib.parse import urlsplit
from . import agent_browser_policy as policy
from .agent_mission_runtime import MissionError

MAX_IMAGE=145000
MAX_TEXT=9000

def capture(url: str, origin: str, pinned_ip: str) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise MissionError("HomeServer browser dependency is missing.",503) from exc
    _,host,target_origin=policy.parse_url(url)
    if target_origin!=origin:
        raise MissionError("Browser destination left the approved origin.",403)
    with sync_playwright() as playwright:
        browser=None
        try:
            launch_args=["--disable-background-networking","--disable-sync",
                         "--disable-extensions","--no-first-run",
                         "--host-resolver-rules=MAP "+host+" "+pinned_ip]
            try:
                browser=playwright.chromium.launch(channel="chrome",headless=True,args=launch_args)
            except Exception:
                browser=playwright.chromium.launch(headless=True,args=launch_args)
            context=browser.new_context(
                viewport={"width":960,"height":670},java_script_enabled=False,
                service_workers="block",accept_downloads=False,
                ignore_https_errors=False,permissions=[])
            page=context.new_page()
            def restricted(route):
                req=route.request
                try:
                    _,_,actual_origin=policy.parse_url(req.url)
                    if req.method.upper()!="GET" or actual_origin!=origin:
                        route.abort()
                    else:
                        route.continue_()
                except Exception:
                    route.abort()
            page.route("**/*",restricted)
            response=page.goto(url,wait_until="domcontentloaded",timeout=15000)
            if response is None or response.status>=400:
                raise MissionError("Browser page returned an error.",502)
            if urlsplit(page.url).hostname!=host:
                raise MissionError("Browser escaped its approved host.",403)
            title=page.title()[:180]
            body=page.locator("body").inner_text(timeout=4000)[:MAX_TEXT]
            image=page.screenshot(type="jpeg",quality=35,
                                  full_page=False,timeout=7000,animations="disabled")
            screenshot=base64.b64encode(image).decode("ascii") if len(image)<=MAX_IMAGE else ""
            return {"url":page.url[:1400],"title":title,"text":body,"image_base64":screenshot}
        finally:
            if browser is not None:
                browser.close()
