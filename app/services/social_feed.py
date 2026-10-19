"""Owner-operated Feed uses the paired VP3 identity; credentials never reach app JS."""
from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import homeserver_apps
from .https_bridge_session import https_session_matches, load_https_session
from .remote_identity import load_or_create_remote_identity

APP_KEY = "vp3.feed"
CONTRACT = "vp3.social-feed.v1"
FIELDS = {"action", "mode", "before", "user_id", "q", "body", "media", "visibility", "request_id", "id", "following", "display_name", "username", "bio", "is_public", "expected_revision"}
ACTIONS = {"bootstrap", "feed", "profile", "people", "post.create", "post.delete", "profile.update", "follow"}


class FeedError(homeserver_apps.HomeServerAppError):
    pass


def request(body: dict, client: httpx.Client | None = None) -> dict:
    app = homeserver_apps.get(APP_KEY)
    if app["lifecycle_state"] != "running" or not app.get("installed_version"):
        raise FeedError("Feed is not running.", 409)
    if body.get("action") not in ACTIONS:
        raise FeedError("Unknown Feed action.", 422)
    payload = {k: v for k, v in body.items() if k in FIELDS}
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 65536:
        raise FeedError("Post request is too large.", 413)
    session = load_https_session()
    suffix = "/api/homeserver-https-poll-v1300.php"
    if not session or not session["endpoint"].endswith(suffix):
        raise FeedError("Connect HomeServer to your VP3 account to use Feed.", 409)
    token = session["session_token"]
    device = load_or_create_remote_identity()["device_id"]
    endpoint = session["endpoint"][:-len(suffix)] + "/api/social-feed-v1.php"
    try:
        def send(connection):
            return connection.post(endpoint, json=payload, headers={"Authorization": "Bearer " + token, "X-HomeServer-Device": device, "Accept": "application/json"})
        if client is not None:
            response = send(client)
        else:
            with httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0), follow_redirects=False) as connection:
                response = send(connection)
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FeedError("Feed is unavailable. Keep your draft and retry after reconnecting.", 503) from exc
    if not https_session_matches(token):
        raise FeedError("Your Cloud pairing changed. Reload Feed before continuing.", 409)
    if homeserver_apps.get(APP_KEY)["lifecycle_state"] != "running":
        raise FeedError("Feed was stopped while this request was running.", 409)
    if not response.is_success or not isinstance(result, dict) or not result.get("ok"):
        message = str(result.get("error") or "Feed is unavailable.")[:300] if isinstance(result, dict) else "Invalid Feed response."
        raise FeedError(message, response.status_code if response.status_code in {401, 403, 404, 409, 413, 419, 422, 429} else 502)
    if result.get("contract") != CONTRACT or type(result.get("viewer_id")) is not int or result["viewer_id"] < 1:
        raise FeedError("Update the matching Cloud Feed release.", 502)
    origin = urlsplit(session["endpoint"])
    result["cloud_origin"] = urlunsplit((origin.scheme, origin.netloc, "", "", ""))
    return result
