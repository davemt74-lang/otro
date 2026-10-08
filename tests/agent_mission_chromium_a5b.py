"""Smoke-test installed Chromium isolation, without external network requests."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    try:
        browser=playwright.chromium.launch(channel="chrome",headless=True)
    except Exception:
        browser=playwright.chromium.launch(headless=True)
    try:
        a=browser.new_context(viewport={"width":960,"height":670},
                              java_script_enabled=False,service_workers="block",
                              accept_downloads=False,ignore_https_errors=False,
                              permissions=[])
        b=browser.new_context(viewport={"width":960,"height":670},
                              java_script_enabled=False,service_workers="block",
                              accept_downloads=False,ignore_https_errors=False,
                              permissions=[])
        try:
            first=a.new_page()
            first.set_content('<h1>Browser worker</h1><script>window.evil=1</script>')
            assert first.locator("h1").inner_text()=="Browser worker"
            assert first.evaluate("window.evil") is None, "JavaScript must remain disabled"
            image=first.screenshot(type="jpeg",quality=35,full_page=False)
            assert image[:3]==bytes([255,216,255]),"JPEG browser capture was invalid"
            a.add_cookies([{"name":"isolation","value":"hidden",
                            "url":"https://example.com"}])
            assert b.cookies("https://example.com")==[], "Worker contexts shared cookies"
        finally:
            a.close()
            b.close()
    finally:
        browser.close()
print("MISSION_A5B_CHROMIUM PASS: real Chromium launch, no JavaScript, screenshot and separate cookies")
