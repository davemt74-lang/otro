"""A5B3 non-submitting, script-disabled browser controls.

No arbitrary CSS, JavaScript, submit buttons, file inputs, password fields,
credentials, browser storage or form submission. Indices and fingerprints refer
only to visible enabled safe controls in the current page snapshot.
"""
from __future__ import annotations
import hashlib
import re
from .agent_mission_runtime import MissionError

SELECTOR = "input, textarea, select, details > summary"
MAX_CONTROLS = 32
MAX_OPTIONS = 16
MAX_VALUE = 300
TEXT_TYPES = ("text", "search")
CHECK_TYPES = ("checkbox", "radio")


def candidates(page) -> list[dict]:
    result = []
    locator = page.locator(SELECTOR)
    for position in range(min(locator.count(), 120)):
        element = locator.nth(position)
        if not element.is_visible() or not element.is_enabled():
            continue
        tag = (element.evaluate("(node) => node.tagName.toLowerCase()") or "").lower()
        field_type = (element.get_attribute("type") or "text").lower() if tag == "input" else ""
        if (element.get_attribute("readonly") is not None or
            element.get_attribute("disabled") is not None):
            continue
        if tag == "input" and field_type in TEXT_TYPES:
            kind = "fill"
        elif tag == "textarea":
            kind = "fill"
        elif tag == "input" and field_type in CHECK_TYPES:
            kind = "check"
        elif tag == "select":
            kind = "select"
        elif tag == "summary":
            kind = "toggle"
        else:
            continue
        label = (
            element.get_attribute("aria-label") or
            element.get_attribute("placeholder") or
            element.get_attribute("name") or
            element.get_attribute("id") or
            (element.inner_text(timeout=1000) if tag == "summary" else "") or
            kind
        )
        label = re.sub(r"\s+", " ", str(label)).strip()[:100]
        identity = "|".join([
            str(position), tag, field_type,
            str(element.get_attribute("name") or ""),
            str(element.get_attribute("id") or ""), label,
        ])
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        candidate = {"index": position, "kind": kind, "label": label,
                     "fingerprint": fingerprint}
        if kind == "select":
            options = element.locator("option")
            if options.count() > MAX_OPTIONS:
                continue
            candidate["options"] = [
                {"index": j, "label": re.sub(r"\s+", " ", options.nth(j).inner_text()).strip()[:100]}
                for j in range(options.count())
            ]
        result.append(candidate)
        if len(result) >= MAX_CONTROLS:
            break
    return result


def apply(page, *, index: int, fingerprint: str, kind: str, value=None) -> None:
    if type(index) is not int or type(fingerprint) is not str:
        raise MissionError("Invalid browser element identity.", 422)
    available = candidates(page)
    control = next((c for c in available if c["index"] == index), None)
    if (not control or control["fingerprint"] != fingerprint
        or control["kind"] != kind):
        raise MissionError("Browser control changed; obtain a new proposal.", 409)
    element = page.locator(SELECTOR).nth(index)
    if kind == "fill":
        if not isinstance(value, str) or len(value) > MAX_VALUE:
            raise MissionError("Input must be a string of at most 300 characters.", 422)
        if any(ord(c) < 32 and c not in "\t\n" for c in value):
            raise MissionError("Input contains forbidden control characters.", 422)
        element.fill(value, timeout=3500)
    elif kind == "check":
        if value is not True:
            raise MissionError("Checking requires an explicit true value.", 422)
        element.check(timeout=3500)
    elif kind == "select":
        if type(value) is not int or not 0 <= value < len(control.get("options", [])):
            raise MissionError("Choose one displayed select option.", 422)
        element.select_option(index=value, timeout=3500)
    elif kind == "toggle":
        if value is not True:
            raise MissionError("Expanding details requires explicit confirmation.", 422)
        element.click(timeout=3500)
    else:
        raise MissionError("This browser interaction is not permitted.", 403)
