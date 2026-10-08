"""A5B4 restrictive search-form policy. No POST, hidden fields or side effects.

Only owner-approved, same-origin GET navigation to a search/find path is allowed.
This is not a generic form submission engine.
"""
from __future__ import annotations
import hashlib
import re
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit
from .agent_mission_runtime import MissionError
from .agent_browser_policy import parse_url

_FIELDS={"q","query","search","term"}
_BLOCKED=("password","email","credit","card","payment","token","auth","secret","phone","address")
_MAX_FORMS=6

def search_forms(page, origin: str) -> list[dict]:
    items=[]
    for index, form in enumerate(page.locator("form").all()[:20]):
        try:
            if not form.is_visible(): continue
            method=(form.get_attribute("method") or "get").strip().lower()
            if method!="get": continue
            target=urljoin(page.url,(form.get_attribute("action") or page.url))
            _,_,form_origin=parse_url(target)
            path=urlsplit(target).path.lower()
            if form_origin!=origin or not re.search(r"(?:^|/)(?:search|find)(?:/|$)",path): continue
            inputs=form.locator("input,textarea,select")
            if inputs.count()!=1: continue
            field=inputs.first
            name=(field.get_attribute("name") or "").strip().lower()
            kind=(field.get_attribute("type") or "text").lower()
            if name not in _FIELDS or kind not in ("text","search"): continue
            if any(s in name for s in _BLOCKED): continue
            if not field.is_visible() or not field.is_enabled(): continue
            if field.get_attribute("readonly") is not None: continue
            ident=f"{index}|{target.split('?')[0]}|{name}|{kind}"
            fingerprint=hashlib.sha256(ident.encode()).hexdigest()[:24]
            items.append({"index":index,"fingerprint":fingerprint,
                          "action":target.split("?")[0][:500],
                          "label":(field.get_attribute("aria-label") or name)[:80],
                          "method":"GET"})
            if len(items)>=_MAX_FORMS: break
        except (MissionError,ValueError):
            continue
    return items

def submit_search(page, origin: str, *, index: int, fingerprint: str):
    if type(index) is not int or type(fingerprint) is not str:
        raise MissionError("Invalid search-form identity.",422)
    expected=next((x for x in search_forms(page,origin) if x["index"]==index),None)
    if not expected or expected["fingerprint"]!=fingerprint:
        raise MissionError("Search form changed; review it again.",409)
    form=page.locator("form").nth(index)
    field=form.locator("input,textarea,select").first
    name=(field.get_attribute("name") or "").strip()
    value=field.input_value(timeout=2500).strip()
    if not 1<=len(value)<=100 or any(ord(ch)<32 for ch in value):
        raise MissionError("Enter a search query of up to 100 characters.",422)
    target=urljoin(page.url,form.get_attribute("action") or page.url)
    parsed=urlsplit(target)
    if parsed.query:
        raise MissionError("Prepopulated query parameters are not allowed.",403)
    url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode({name:value}),""))
    _,_,to_origin=parse_url(url)
    if to_origin!=origin:
        raise MissionError("Search would leave the approved origin.",403)
    response=page.goto(url,wait_until="domcontentloaded",timeout=15000)
    if response is None or response.status>=400:
        raise MissionError("Search navigation failed.",502)
    return url
