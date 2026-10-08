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
_CONSEQUENTIAL=re.compile(r"(?:checkout|payment|purchase|publish|message|send|delete|unsubscribe|logout|transfer|order)",re.I)

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
            if (form_origin!=origin or not re.fullmatch(r"/(?:[a-z0-9_-]+/)*(?:search|find)/?",path)
                or _CONSEQUENTIAL.search(path) or urlsplit(target).query): continue
            inputs=form.locator("input,textarea,select")
            if inputs.count()!=1: continue
            field=inputs.first
            if field.evaluate('(node) => node.tagName.toLowerCase()')!='input': continue
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

def review_search(page, origin: str, *, index: int, fingerprint: str):
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
    if not field.evaluate('(node) => node.checkValidity()'):
        raise MissionError('Required search field is invalid.',422)
    return {'index':index,'fingerprint':fingerprint,'action':expected['action'],
            'field':name,'query':value,'destination':url,'method':'GET',
            'required_fields':[{'name':name,'valid':True,'required':field.get_attribute('required') is not None}],
            'payload_hash':hashlib.sha256(url.encode()).hexdigest()}

def prepare_search(page,origin: str,*,index: int,fingerprint: str,query: str):
    expected=next((x for x in search_forms(page,origin) if x['index']==index),None)
    if not expected or expected['fingerprint']!=fingerprint: raise MissionError('Search form changed.',409)
    if type(query) is not str or not 1<=len(query)<=100 or any(ord(c)<32 for c in query):
        raise MissionError('Search query must contain 1 to 100 ordinary characters.',422)
    page.locator('form').nth(index).locator('input').first.fill(query,timeout=2500)

def submit_search(page, origin: str, *, index: int, fingerprint: str, payload_hash: str | None=None):
    review=review_search(page,origin,index=index,fingerprint=fingerprint)
    if payload_hash is not None and review['payload_hash']!=payload_hash:
        raise MissionError('Search query changed after review. Review it again.',409)
    url=review['destination']
    response=page.goto(url,wait_until="domcontentloaded",timeout=15000)
    if response is None or response.status>=400:
        raise MissionError("Search navigation failed.",502)
    return url
