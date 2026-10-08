"""Real Chromium regression for strictly limited GET search-form submission."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from app.services.agent_browser_search_policy import search_forms,submit_search
from app.services.agent_mission_runtime import MissionError

html="""<!doctype html><title>Search</title>
<form method="get" action="/search">
<label>Query<input name="q" type="search" value=""></label>
<button type="submit">Find</button></form>
<form method="post" action="/search"><input name="query"></form>
<form method="get" action="/checkout"><input name="q"></form>
<form method="get" action="/search"><input name="q"><input type="hidden" name="token" value="x"></form>
<form method="get" action="https://other.example/search"><input name="q"></form>"""
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    try:
        ctx=browser.new_context(java_script_enabled=False,service_workers="block",
            accept_downloads=False,permissions=[])
        page=ctx.new_page()
        requests=[]
        def handler(route):
            requests.append((route.request.method,route.request.url))
            if route.request.url.startswith("https://example.com/"):
                route.fulfill(status=200,content_type="text/html",body=html)
            else: route.abort()
        page.route("**/*",handler)
        page.goto("https://example.com/search")
        available=search_forms(page,"https://example.com")
        assert len(available)==1,available
        candidate=available[0]
        assert candidate["method"]=="GET"
        page.locator('form').first.locator('input[name="q"]').fill("safe report")
        assert submit_search(page,"https://example.com",
            index=candidate["index"],fingerprint=candidate["fingerprint"]).endswith("?q=safe+report")
        assert requests[-1][0]=="GET"
        assert requests[-1][1].endswith("?q=safe+report")
        try:
            submit_search(page,"https://example.com",index=candidate["index"],fingerprint="f"*24)
            raise AssertionError("Old/incorrect form fingerprint accepted")
        except MissionError as exc: assert exc.status_code==409
        try:
            submit_search(page,"https://example.com",index=2,fingerprint=candidate["fingerprint"])
            raise AssertionError("Checkout form accepted")
        except MissionError as exc: assert exc.status_code==409
        print("A5B4_CHROMIUM PASS: GET search only, URL encoding, POST/checkout/hidden/cross-origin rejection and fingerprint checks")
    finally: browser.close()
