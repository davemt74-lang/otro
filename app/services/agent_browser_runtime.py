"""Installer-managed Chromium. Browser profiles remain ephemeral per worker."""
from __future__ import annotations
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path
from .agent_mission_runtime import MissionError

def runtime_root() -> Path:
    base=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]/'dist'
    return (base/'tools'/'browser').resolve()

@lru_cache(maxsize=4)
def _verified(path: str,size: int,modified: int,expected: str) -> bool:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()==expected

def executable() -> str | None:
    root=runtime_root();manifest=root/'manifest.json'
    if not manifest.is_file():
        if getattr(sys,'frozen',False):
            raise MissionError('The managed browser is missing. Install the complete HomeServer package.',503)
        return None
    try:
        data=json.loads(manifest.read_text())
        target=(root/data['executable']).resolve()
        if not target.is_relative_to(root) or not target.is_file(): raise ValueError()
        stat=target.stat()
        if not _verified(str(target),stat.st_size,stat.st_mtime_ns,data['sha256']): raise ValueError()
        return str(target)
    except (OSError,ValueError,KeyError,TypeError):
        raise MissionError('The managed browser failed verification. Reinstall the complete HomeServer package.',503) from None

def launch(playwright,*,args=None):
    path=executable()
    if path: return playwright.chromium.launch(headless=True,executable_path=path,args=args or [])
    try: return playwright.chromium.launch(channel='chrome',headless=True,args=args or [])
    except Exception: return playwright.chromium.launch(headless=True,args=args or [])

def certify(report_path: str):
    """Local CLI acceptance: real packaged browser, isolated contexts, exact forms."""
    from playwright.sync_api import sync_playwright
    from . import agent_browser_search_policy as search
    with sync_playwright() as pw:
        browser=launch(pw)
        try:
            first=browser.new_context(java_script_enabled=False,service_workers='block',accept_downloads=False,permissions=[])
            second=browser.new_context(java_script_enabled=False,service_workers='block',accept_downloads=False,permissions=[])
            first.add_cookies([{'name':'isolation','value':'private','domain':'example.com','path':'/'}])
            assert second.cookies()==[], 'Browser contexts share cookies'
            page=first.new_page();requests=[]
            html='<form method="get" action="/search"><input type="search" name="q" required><button>Search</button></form><form method="post" action="/search"><input name="q"></form>'
            def route(r):
                requests.append(r.request.url)
                r.fulfill(status=200,content_type='text/html',body=html)
            page.route('**/*',route);page.goto('https://example.com/search')
            forms=search.search_forms(page,'https://example.com');assert len(forms)==1
            form=forms[0];field=page.locator('input').first;field.fill('owner approved query')
            preview=search.review_search(page,'https://example.com',index=form['index'],fingerprint=form['fingerprint'])
            field.fill('changed query');before=len(requests)
            try:
                search.submit_search(page,'https://example.com',index=form['index'],fingerprint=form['fingerprint'],payload_hash=preview['payload_hash'])
                raise AssertionError('Changed query was submitted')
            except MissionError as exc: assert exc.status_code==409
            assert len(requests)==before
            field.fill('owner approved query')
            search.submit_search(page,'https://example.com',index=form['index'],fingerprint=form['fingerprint'],payload_hash=preview['payload_hash'])
            assert requests[-1].endswith('q=owner+approved+query')
            Path(report_path).write_text(json.dumps({'ok':True,'section':'A5B4','managed_browser':bool(executable()),'isolation':True,'exact_form_approval':True},indent=2))
        finally: browser.close()
