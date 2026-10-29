"""Swappable carrier driver; only this module knows Twilio's REST contract."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CarrierError(RuntimeError):
    pass


class Carrier(Protocol):
    def send_sms(self, sender: str, recipient: str, body: str) -> str: ...
    def start_call(self, sender: str, recipient: str, callback: str) -> str: ...


@dataclass
class TwilioCarrier:
    account_sid: str
    auth_token: str

    def _post(self, resource: str, fields: dict[str, str]) -> str:
        if not self.account_sid or not self.auth_token:
            raise CarrierError("Twilio is not configured")
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/{resource}.json"
        bearer = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        req = Request(url, data=urlencode(fields).encode(), method="POST",
                      headers={"Authorization": f"Basic {bearer}",
                               "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(req, timeout=12) as result:
                payload = json.loads(result.read(65536))
        except (HTTPError, URLError, ValueError, TimeoutError) as exc:
            # Provider error bodies may contain phone numbers or account details.
            raise CarrierError("The carrier rejected or could not complete the request") from exc
        sid = payload.get("sid")
        if not isinstance(sid, str) or not sid:
            raise CarrierError("The carrier returned no operation ID")
        return sid

    def send_sms(self, sender: str, recipient: str, body: str) -> str:
        return self._post("Messages", {"From": sender, "To": recipient, "Body": body})

    def start_call(self, sender: str, recipient: str, callback: str) -> str:
        return self._post("Calls", {"From": sender, "To": recipient, "Url": callback,
                                    "Method": "POST"})
