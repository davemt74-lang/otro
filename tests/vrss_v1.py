"""Real isolated SQLite/API acceptance; controlled remote transport, no external requests."""
import copy
import io
import json
import os
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>News &amp; music</title><link>https://news.example/</link><description>News</description><item><guid>a</guid><title>First</title><description>&lt;p&gt;Hello&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;</description><link>/first</link><pubDate>Mon, 19 Oct 2026 12:00:00 GMT</pubDate><enclosure url="https://media.example/song.mp3" length="10" type="audio/mpeg"/></item><item><guid>b</guid><title>Second</title><description>More</description><link>javascript:bad()</link></item></channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><id>atom:a</id><title>Entry</title><summary>Text</summary><published>2026-10-19T12:00:00Z</published><link href="/entry"/><link rel="enclosure" href="https://media.example/clip.mp4" type="video/mp4"/></entry></feed>'''


def reject(fn, status=None):
    try:
        fn()
    except Exception as exc:
        from app.services.vrss import VRSSError
        assert isinstance(exc, VRSSError), repr(exc)
        if status:
            assert exc.status_code == status, (exc.status_code, status, str(exc))
        return
    raise AssertionError("Unsafe operation succeeded")


with tempfile.TemporaryDirectory(prefix="vp3-vrss-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import initialize_database, db
    from app.services import vrss as v, homeserver_app_prebuilt as packages, homeserver_apps
    initialize_database()
    packages.ensure_defaults()
    identity = v.request({"action": "bootstrap"})
    assert identity["capabilities"]["cloud_required"] is False
    p = identity["profile"]
    assert not p["enabled"] and p["actor_id"].startswith("urn:uuid:")
    assert v.request({"action": "bootstrap"})["viewer_id"] == p["actor_id"]
    assert list(Path(data).glob("app-data/*/sqlite/vrss.db"))
    reject(lambda: v.public_document("rss"), 404)
    payload = {"body": "Hello <script>alert(1)</script> & music", "visibility": "public", "request_id": str(uuid.uuid4()), "media": [{"kind": "audio", "url": "https://media.example/music.mp3", "alt": "Song"}]}
    post = v.create_post(payload)
    assert not post["replayed"]
    assert v.create_post(payload)["replayed"]
    reject(lambda: v.create_post({**payload, "body": "changed"}), 409)
    private = v.create_post({**payload, "body": "Secret", "visibility": "private", "request_id": str(uuid.uuid4())})
    profile_request = {"display_name": "Dave & music", "bio": "Local profile", "public_base": "https://home.example/community", "enabled": True, "expected_revision": p["revision"]}
    p = v.update_profile(profile_request)
    assert p["address_locked"]
    reject(lambda: v.update_profile(profile_request), 409)
    reject(lambda: v.update_profile({**profile_request, "expected_revision": p["revision"], "public_base": "https://different.example"}), 409)
    xml = v.public_document("rss")
    root = ET.fromstring(xml)
    assert len(root.findall("channel/item")) == 1
    assert b"Secret" not in xml
    assert root.findtext("channel/item/guid") == "urn:uuid:" + post["post_id"]
    desc = root.findtext("channel/item/description")
    assert "<script>" not in desc and "&lt;script&gt;" in desc
    assert root.find("channel/item/{http://search.yahoo.com/mrss/}content").get("url") == payload["media"][0]["url"]
    round_trip = v.parse_feed(xml, p["feed_url"])
    assert round_trip["items"][0]["media"][0]["kind"] == "audio"
    reject(lambda: v.create_post({**payload, "body": "invalid\ufffe", "request_id": str(uuid.uuid4())}), 422)
    reject(lambda: v.public_document("post", private["post_id"]), 404)
    public = v.public_document("post", post["post_id"])
    assert "request_id" not in public and "can_delete" not in public
    assert public["url"].startswith("https://home.example/community/")
    parsed = v.parse_feed(RSS, "https://news.example/rss")
    assert parsed["title"] == "News & music" and len(parsed["items"]) == 2
    assert parsed["items"][0]["body"] == "Hello" and parsed["items"][0]["url"] == "https://news.example/first"
    assert not parsed["items"][1]["url"] and parsed["items"][0]["media"][0]["kind"] == "audio"
    atom = v.parse_feed(ATOM, "https://atom.example/feed")
    assert atom["items"][0]["media"][0]["kind"] == "video"
    for blob in [b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///secret">]><rss version="2.0"/>', b"<html>Not a feed</html>", b"<rss>", b"\xff"]:
        reject(lambda: v.parse_feed(blob, "https://news.example/rss"), 422)
    reject(lambda: v.parse_feed(b"x" * (v.MAX_XML+1), "https://news.example/rss"), 413)
    for url in ["file:///etc/passwd", "http://127.0.0.1/rss", "http://[::1]/rss", "http://169.254.169.254/rss", "https://a:b@news.example/rss", "https://news.example:8443/rss", "https://localhost/rss", "https://news.example/rss#fragment"]:
        reject(lambda: v.safe_url(url), 422)
    calls = []
    def fetch(url):
        calls.append(url)
        return RSS, "https://news.example/rss"
    sub = v.subscribe("https://news.example/rss", fetch)
    assert v.subscribe("https://news.example/rss", fetch)["replayed"] and len(calls) == 1
    assert len(v.request({"action": "feed"})["feed"]["items"]) == 4
    assert len(v.request({"action": "feed", "mode": "profile"})["feed"]["items"]) == 2
    reject(lambda: v.refresh(sub["subscription_id"], fetch), 429)
    with v.database() as c:
        c.execute("UPDATE vrss_subscriptions SET checked_at=''")
    v.refresh(sub["subscription_id"], fetch)
    assert len(v.request({"action": "feed"})["feed"]["items"]) == 4, "Refresh duplicated items"
    with v.database() as c:
        c.execute("UPDATE vrss_subscriptions SET checked_at=''")
    def unsubscribe_in_flight(url):
        v.request({"action": "unsubscribe", "id": sub["subscription_id"]})
        return fetch(url)
    reject(lambda: v.refresh(sub["subscription_id"], unsubscribe_in_flight), 409)
    assert len(v.request({"action": "feed"})["feed"]["items"]) == 2
    def failure(url):
        raise v.VRSSError("Source unavailable", 502)
    reject(lambda: v.subscribe("https://broken.example/rss", failure), 502)
    assert v.request({"action": "subscriptions"})["subscriptions"][0]["error"] == "Source unavailable"
    v.request({"action": "post.delete", "id": post["post_id"]})
    assert v.create_post(payload)["deleted"]
    assert not ET.fromstring(v.public_document("rss")).findall("channel/item")
    # Public reads use neither owner cookies nor Cloud pairing; all writes remain owner gated.
    from fastapi.testclient import TestClient
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    client = TestClient(app)
    endpoint = "/api/v1/control/homeserver-apps/vrss/request"
    headers = {"X-VRSS-Request": "1"}
    assert client.post(endpoint, json={"action": "bootstrap"}, headers=headers).status_code == 401
    assert client.get("/vrss/v1/profile").status_code == 200
    assert client.get("/vrss/v1/rss").headers["cache-control"] == "no-store"
    assert client.post("/vrss/v1/profile", json={"enabled": True}).status_code == 405
    assert client.post("/__owner/session", headers={"X-HomeServer-Owner": OWNER_CONTROL_TOKEN}).status_code == 200
    assert client.post(endpoint, json={"action": "bootstrap"}).status_code == 403
    assert client.post(endpoint, json=[], headers=headers).status_code == 422
    assert client.post(endpoint, content=b"x"*65537, headers={**headers, "Content-Type": "application/json"}).status_code == 413
    assert client.post(endpoint, json={"action": "bootstrap"}, headers=headers).json()["viewer_id"] == p["actor_id"]
    html = client.get("/vrss/v1/profile", headers={"Accept": "text/html"})
    assert "Dave &amp; music" in html.text and "Follow RSS feed" in html.text
    p = v.update_profile({**profile_request, "enabled": False, "expected_revision": p["revision"]})
    assert client.get("/vrss/v1/rss").status_code == 404
    assert client.get("/vrss/v1/profile").status_code == 404
    # Actual connection construction pins the public IP, preserves TLS host, and drops redirect bodies.
    class Reply:
        def __init__(self, status=200, headers=None, body=RSS):
            self.status = status
            self.headers = headers or {}
            self.body = io.BytesIO(body)
        def getheader(self, key, default=None):
            return self.headers.get(key, default)
        def read(self, size):
            return self.body.read(size)
    responses = [Reply()]
    constructed = []
    class Connection:
        def __init__(self, host, **kwargs):
            self.host = host
            self.kwargs = kwargs
            constructed.append(self)
        def request(self, method, path, headers):
            assert method == "GET" and "Authorization" not in headers and "Cookie" not in headers
            self._create_connection((self.host, 443), 8)
        def getresponse(self):
            return responses.pop(0)
        def close(self):
            pass
    with patch.object(v.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), patch.object(v.socket, "create_connection", return_value=object()) as tcp, patch.object(v.http.client, "HTTPSConnection", Connection):
        blob, final = v.fetch_feed("https://news.example/rss")
        assert blob == RSS and constructed[0].host == "news.example"
        assert tcp.call_args.args[0] == ("8.8.8.8", 443)
        responses[:] = [Reply(302, {"Location": "http://127.0.0.1/private"})]
        reject(lambda: v.fetch_feed("https://news.example/rss"), 422)
        responses[:] = [Reply(200, {"Content-Length": str(v.MAX_XML+1)})]
        reject(lambda: v.fetch_feed("https://news.example/rss"), 413)
        responses[:] = [Reply(200, body=b"x"*(v.MAX_XML+1))]
        reject(lambda: v.fetch_feed("https://news.example/rss"), 413)
    with patch.object(v.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        reject(lambda: v.fetch_feed("https://news.example/rss"), 422)
    # Known F1 default upgrades; a deliberately stopped app stays stopped with local data intact.
    homeserver_apps.transition(v.APP_KEY, "stopped")
    with db() as c:
        c.execute("UPDATE homeserver_apps SET installed_version='1.0.0' WHERE app_key=?", (v.APP_KEY,))
    packages.ensure_defaults()
    assert homeserver_apps.get(v.APP_KEY)["installed_version"] == "1.6.0"
    assert homeserver_apps.get(v.APP_KEY)["lifecycle_state"] == "stopped"
    reject(lambda: v.request({"action": "bootstrap"}), 409)
    homeserver_apps.transition(v.APP_KEY, "running")
    assert v.request({"action": "bootstrap"})["viewer_id"] == p["actor_id"]
    # Outgoing escaping and large posts still produce a feed this same reader can ingest.
    with v.database() as c:
        for index in range(12):
            c.execute("INSERT INTO vrss_posts(id,request_id,fingerprint,body,media,visibility,created_at) VALUES(?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), str(uuid.uuid4()), "fixture", "&"*10000, "[]", "public", v.now()))
    p = v.update_profile({**profile_request, "enabled": True, "expected_revision": p["revision"]})
    output = v.public_document("rss")
    assert len(output) <= v.MAX_XML and v.parse_feed(output, p["feed_url"])["items"]
    with patch.object(v.homeserver_app_resources, "limits", return_value={"sqlite_limit_bytes": 1}):
        reject(lambda: v.create_post({**payload, "request_id": str(uuid.uuid4())}), 413)
    for invalid in [{"action": []}, {"action": "post.delete", "id": []}, {"action": "feed", "before": "bad"},
                    {"action": "post.create", **payload, "visibility": []},
                    {"action": "post.create", **payload, "media": [{"kind": []}]}]:
        reject(lambda: v.request(invalid), 422)
    with v.database() as c:
        for index in range(25):
            c.execute("INSERT INTO vrss_posts(id,request_id,fingerprint,body,media,visibility,created_at) VALUES(?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), str(uuid.uuid4()), "page", str(index), "[]", "private", v.now()))
    first_page = v.request({"action": "feed"})["feed"]
    assert first_page["has_more"] and len(first_page["items"]) == 20
    v.create_post({**payload, "request_id": str(uuid.uuid4())})
    second_page = v.request({"action": "feed", "before": first_page["next_cursor"]})["feed"]
    assert not ({x["id"] for x in first_page["items"]} & {x["id"] for x in second_page["items"]}), "New posts shifted the next page into duplicates"
    print("PASS VRSS local identity/storage, privacy, RSS/Atom import, media, public RSS round trip, exact retries, unsubscribe fences, bounded pinned transport, owner API, disable and default upgrade")
