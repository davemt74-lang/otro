"""VRSS v1: one local owner, public RSS and a private RSS/Atom reader.

Remote social writes are deliberately absent. No Cloud identity or token is used.
"""
from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import re
import socket
import sqlite3
import ssl
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import homeserver_apps, homeserver_app_resources

APP_KEY = "vp3.feed"
CONTRACT = "vp3.vrss.v1"
MAX_XML = 1024 * 1024
MAX_ITEMS = 100
SCHEMA = """
CREATE TABLE IF NOT EXISTS vrss_profile (
 id INTEGER PRIMARY KEY CHECK(id=1), actor_id TEXT NOT NULL,
 display_name TEXT NOT NULL DEFAULT 'My profile', bio TEXT NOT NULL DEFAULT '',
 public_base TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0,
 address_locked INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS vrss_posts (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
 body TEXT NOT NULL, media TEXT NOT NULL, visibility TEXT NOT NULL,
 created_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vrss_subscriptions (
 id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
 site_url TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
 checked_at TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS vrss_items (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id TEXT NOT NULL,
 item_key TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 url TEXT NOT NULL, media TEXT NOT NULL, published_at TEXT NOT NULL,
 received_at TEXT NOT NULL, UNIQUE(subscription_id,item_key),
 FOREIGN KEY(subscription_id) REFERENCES vrss_subscriptions(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS vrss_items_sub ON vrss_items(subscription_id,seq);
CREATE INDEX IF NOT EXISTS vrss_items_time ON vrss_items(received_at,seq);
CREATE INDEX IF NOT EXISTS vrss_posts_time ON vrss_posts(created_at,seq);
"""


class VRSSError(homeserver_apps.HomeServerAppError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def running():
    try:
        app = homeserver_apps.get(APP_KEY)
    except homeserver_apps.HomeServerAppError as exc:
        raise VRSSError("Feed is not installed.", 404) from exc
    if app.get("lifecycle_state") != "running" or not app.get("installed_version"):
        raise VRSSError("Feed is not running.", 409)


@contextmanager
def database():
    running()
    path = homeserver_app_resources.sqlite_path(APP_KEY, "vrss.db")
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        if "hosting_site_id" not in {row[1] for row in connection.execute("PRAGMA table_info(vrss_profile)")}:
            connection.execute("ALTER TABLE vrss_profile ADD COLUMN hosting_site_id TEXT NOT NULL DEFAULT ''")
        connection.execute("INSERT OR IGNORE INTO vrss_profile(id,actor_id) VALUES(1,?)", ("urn:uuid:" + str(uuid.uuid4()),))
        yield connection
        running()
        # Check projected database pages before commit, including sibling app databases.
        used = connection.execute("PRAGMA page_count").fetchone()[0] * connection.execute("PRAGMA page_size").fetchone()[0]
        siblings = sum(f.stat().st_size for f in path.parent.glob("*.db") if f != path)
        if used + siblings > homeserver_app_resources.limits(APP_KEY)["sqlite_limit_bytes"]:
            raise VRSSError("Feed app storage quota exceeded.", 413)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def text(value, limit, label, required=False):
    if not isinstance(value, str) or len(value) > limit or any(not (c in "\n\r\t" or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise VRSSError(label + " is invalid.", 422)
    value = value.strip()
    if required and not value:
        raise VRSSError(label + " is required.", 422)
    return value


def safe_url(value, *, https_only=False):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise VRSSError("Enter a public HTTP or HTTPS address.", 422)
    try:
        p = urlsplit(value)
        host = (p.hostname or "").encode("idna").decode("ascii").lower()
        port = p.port
        if (p.scheme not in ({"https"} if https_only else {"http", "https"}) or not host
                or p.username is not None or p.password is not None or p.fragment or "\\" in value
                or port not in (None, 80 if p.scheme == "http" else 443)):
            raise ValueError()
        try:
            if not ipaddress.ip_address(host).is_global or ipaddress.ip_address(host).is_multicast:
                raise VRSSError("Local and private network addresses are not supported.", 422)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal")) or not re.fullmatch(r"[a-z0-9.-]+", host):
                raise VRSSError("Enter a public host name.", 422)
        netloc = "[" + host + "]" if ":" in host else host
        return urlunsplit((p.scheme, netloc, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise VRSSError("Enter a public HTTP or HTTPS address.", 422) from exc


def optional_url(value, base=""):
    try:
        return safe_url(urljoin(base, value)) if value else ""
    except VRSSError:
        return ""


def profile(connection):
    p = dict(connection.execute("SELECT * FROM vrss_profile WHERE id=1").fetchone())
    p["enabled"] = bool(p["enabled"])
    p["address_locked"] = bool(p["address_locked"])
    p["profile_url"] = p["public_base"] + "/vrss/v1/profile" if p["public_base"] else ""
    p["feed_url"] = p["public_base"] + "/vrss/v1/rss" if p["public_base"] else ""
    return p


def hosting_options():
    """Use existing Cloud-owned bindings and TLS; never provision DNS locally."""
    from . import hosting_cloud_control, hosting_public, hosting_runtime
    sites = []
    for site in hosting_runtime.list_sites():
        site_id = str(site["site_id"])
        try:
            binding = hosting_cloud_control.binding_for_site(site_id)
        except hosting_runtime.HostingError:
            continue
        if not binding or binding.get("target_app_key") != APP_KEY:
            continue
        try:
            route = hosting_public.route_status_for_site(site_id) or {}
        except hosting_runtime.HostingError:
            route = {}
        hostname = str(site.get("requested_hostname") or "")
        if not hostname:
            continue
        sites.append({"site_id": site_id, "name": site["display_name"], "hostname": hostname,
                      "public_base": "https://" + hostname,
                      "ready": bool(route.get("route_ready")) and route.get("hostname") == hostname,
                      "tls_state": route.get("tls_state", "pending")})
    return {"sites": sorted(sites, key=lambda row: row["site_id"]), "authority": "cloud_hosting"}


def hosted_address(site_id, base, *, require_ready=True):
    candidate = next((row for row in hosting_options()["sites"] if row["site_id"] == site_id), None)
    if not candidate or candidate["public_base"] != base or (require_ready and not candidate["ready"]):
        raise VRSSError("The assigned Hosting address is unavailable. Check its subdomain and HTTPS status.", 409)
    return candidate


def update_profile(body):
    name = text(body.get("display_name"), 120, "Display name", True)
    bio = text(body.get("bio"), 2000, "Bio")
    base = text(body.get("public_base", ""), 2048, "Public address")
    if base:
        base = safe_url(base, https_only=True).rstrip("/")
        if urlsplit(base).query or any(s in {".", ".."} for s in urlsplit(base).path.split("/")):
            raise VRSSError("Public address must be an HTTPS origin or path without a query.", 422)
    enabled = body.get("enabled")
    if type(enabled) is not bool or (enabled and not base):
        raise VRSSError("Public publishing needs an HTTPS address and your explicit choice.", 422)
    hosting_site_id = text(body.get("hosting_site_id", ""), 160, "Hosting site")
    with database() as c:
        p = profile(c)
        if type(body.get("expected_revision")) is not int or body["expected_revision"] != p["revision"]:
            raise VRSSError("Profile changed. Reload before saving.", 409)
        if p["address_locked"] and base != p["public_base"]:
            raise VRSSError("This published address is permanent in VRSS v1. Address migration is not available yet.", 409)
        if hosting_site_id:
            # Disabling remains possible after revocation/offline routing.
            if enabled or hosting_site_id != p["hosting_site_id"] or base != p["public_base"]:
                hosted_address(hosting_site_id, base, require_ready=enabled)
        if p["hosting_site_id"] and p["address_locked"] and hosting_site_id != p["hosting_site_id"]:
            raise VRSSError("This profile is bound to its published Hosting site. Address migration is not available yet.", 409)
        c.execute("UPDATE vrss_profile SET display_name=?,bio=?,public_base=?,enabled=?,address_locked=?,hosting_site_id=?,revision=revision+1 WHERE id=1",
                  (name, bio, base, int(enabled), int(enabled or p["address_locked"]), hosting_site_id))
        return profile(c)


def media_values(value):
    if not isinstance(value, list) or len(value) > 4:
        raise VRSSError("Attach up to four media links.", 422)
    result = []
    for m in value:
        if not isinstance(m, dict) or not isinstance(m.get("kind"), str) or m.get("kind") not in {"image", "audio", "video", "link"}:
            raise VRSSError("Invalid media attachment.", 422)
        result.append({"kind": m["kind"], "url": safe_url(m.get("url"), https_only=True), "alt": text(m.get("alt", ""), 250, "Media description")})
    return result


def create_post(body):
    content = text(body.get("body", ""), 10000, "Post")
    media = media_values(body.get("media", []))
    visibility = body.get("visibility")
    if not isinstance(visibility, str) or visibility not in {"public", "private"} or not (content or media):
        raise VRSSError("Choose an audience and write a post or attach media.", 422)
    try:
        request_id = str(uuid.UUID(body.get("request_id", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise VRSSError("A post request ID is required.", 422) from exc
    fingerprint = hashlib.sha256(json.dumps([content, media, visibility], sort_keys=True).encode()).hexdigest()
    with database() as c:
        old = c.execute("SELECT * FROM vrss_posts WHERE request_id=?", (request_id,)).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise VRSSError("This request ID belongs to a different post.", 409)
            return {"post_id": old["id"], "replayed": True, "deleted": bool(old["deleted"])}
        if c.execute("SELECT count(*) FROM vrss_posts").fetchone()[0] >= 10000:
            raise VRSSError("Local post storage is full.", 409)
        if c.execute("SELECT count(*) FROM vrss_posts WHERE created_at>=?", (datetime.fromtimestamp(time.time()-3600, timezone.utc).isoformat(),)).fetchone()[0] >= 60:
            raise VRSSError("Please wait before publishing more posts.", 429)
        post_id = str(uuid.uuid4())
        c.execute("INSERT INTO vrss_posts(id,request_id,fingerprint,body,media,visibility,created_at) VALUES(?,?,?,?,?,?,?)",
                  (post_id, request_id, fingerprint, content, json.dumps(media), visibility, now()))
        return {"post_id": post_id, "replayed": False, "deleted": False}


def post_value(row, p):
    return {"id": row["id"], "body": row["body"], "media": json.loads(row["media"]), "visibility": row["visibility"],
            "created_at": row["created_at"], "author": {"id": p["actor_id"], "display_name": p["display_name"]}, "can_delete": True}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value, limit=10000):
    parser = PlainText()
    parser.feed(value[:50000])
    return "".join(parser.parts).strip()[:limit]


def parse_feed(blob, base):
    if len(blob) > MAX_XML:
        raise VRSSError("Feed exceeds the 1 MB limit.", 413)
    try:
        xml = blob.decode("utf-8-sig")
        if re.search(r"<!\s*(DOCTYPE|ENTITY)", xml, re.I):
            raise ValueError("DTD")
        root = ET.fromstring(xml)
    except (UnicodeError, ET.ParseError, ValueError) as exc:
        raise VRSSError("This is not a supported UTF-8 RSS or Atom feed.", 422) from exc
    if sum(1 for _ in root.iter()) > 20000:
        raise VRSSError("Feed contains too many elements.", 413)
    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == "rss" and root.get("version") == "2.0" and root.find("channel") is not None:
        channel = root.find("channel")
        entries = channel.findall("item")[:MAX_ITEMS]
        title = plain(channel.findtext("title", "Feed"), 250)
        site = optional_url(channel.findtext("link", ""), base)
        is_atom = False
    elif root.tag == atom + "feed":
        channel = root
        entries = root.findall(atom + "entry")[:MAX_ITEMS]
        title = plain(root.findtext(atom + "title", "Feed"), 250)
        site = ""
        is_atom = True
    else:
        raise VRSSError("Enter a direct RSS 2.0 or Atom feed URL.", 422)
    items = []
    for entry in entries:
        prefix = atom if is_atom else ""
        title_text = plain(entry.findtext(prefix + "title", ""), 1000)
        raw_body = entry.findtext(atom + "content", entry.findtext(atom + "summary", "")) if is_atom else entry.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", entry.findtext("description", ""))
        body = plain(raw_body)
        if not (title_text or body):
            continue
        if is_atom:
            links = entry.findall(atom + "link")
            link = next((x.get("href", "") for x in links if x.get("rel", "alternate") == "alternate"), "")
            enclosures = [x for x in links if x.get("rel") == "enclosure"]
            guid = entry.findtext(atom + "id", "")
            date = entry.findtext(atom + "published", entry.findtext(atom + "updated", ""))
        else:
            link = entry.findtext("link", "")
            enclosures = entry.findall("enclosure") + entry.findall("{http://search.yahoo.com/mrss/}content")
            guid = entry.findtext("guid", "")
            date = entry.findtext("pubDate", "")
        url = optional_url(link, base)
        media = []
        for enc in enclosures[:4]:
            enc_url = optional_url(enc.get("href" if is_atom else "url", ""), base)
            mime = enc.get("type", "").split("/", 1)[0] or enc.get("medium", "")
            if enc_url:
                media.append({"url": enc_url, "kind": mime if mime in {"image", "audio", "video"} else "link", "alt": ""})
        published = ""
        try:
            dt = datetime.fromisoformat(date.replace("Z", "+00:00")) if is_atom else parsedate_to_datetime(date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except (ValueError, TypeError, OverflowError):
            pass
        # Source identity stays scoped to a subscription; remote IDs never become local IDs.
        identity = guid[:4096] or url or json.dumps([title_text, body, date])
        items.append({"key": hashlib.sha256(identity.encode()).hexdigest(), "title": title_text, "body": body,
                      "url": url, "media": media, "published_at": published})
    return {"title": title or "Feed", "site_url": site, "items": items}


def fetch_feed(url):
    """Pin a validated public IP before TCP/TLS, including every redirect; no proxies/cookies."""
    deadline = time.monotonic() + 25
    for _ in range(5):
        if time.monotonic() > deadline:
            raise VRSSError("Feed request timed out.", 504)
        url = safe_url(url)
        p = urlsplit(url)
        try:
            addresses = {x[4][0] for x in socket.getaddrinfo(p.hostname, 443 if p.scheme == "https" else 80, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in addresses):
                raise VRSSError("Feed resolves to a local or private network address.", 422)
            ip = sorted(addresses)[0]
            if p.scheme == "https":
                connection = http.client.HTTPSConnection(p.hostname, timeout=8, context=ssl.create_default_context())
            else:
                connection = http.client.HTTPConnection(p.hostname, timeout=8)
            # HTTPSConnection still wraps with the original hostname for SNI and certificate verification.
            connection._create_connection = lambda address, timeout=8, source_address=None: socket.create_connection((ip, address[1]), timeout, source_address)
            try:
                connection.request("GET", urlunsplit(("", "", p.path or "/", p.query, "")), headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml", "Accept-Encoding": "identity", "User-Agent": "VP3-VRSS/1.0"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    target = safe_url(urljoin(url, response.getheader("Location", "")))
                    if p.scheme == "https" and urlsplit(target).scheme != "https":
                        raise VRSSError("Feed redirected to an insecure address.", 422)
                    url = target
                    continue
                if response.status != 200:
                    raise VRSSError("Feed could not be fetched (HTTP " + str(response.status) + ").", 502)
                if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise VRSSError("Compressed feed responses are not supported yet.", 422)
                length = response.getheader("Content-Length")
                if length and int(length) > MAX_XML:
                    raise VRSSError("Feed exceeds the 1 MB limit.", 413)
                data = bytearray()
                while len(data) <= MAX_XML:
                    if time.monotonic() > deadline:
                        raise VRSSError("Feed request timed out.", 504)
                    chunk = response.read(min(65536, MAX_XML + 1 - len(data)))
                    if not chunk:
                        return bytes(data), url
                    data.extend(chunk)
                raise VRSSError("Feed exceeds the 1 MB limit.", 413)
            finally:
                connection.close()
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise VRSSError("Feed is unavailable. Check the address and try again.", 502) from exc
    raise VRSSError("Feed redirected too many times.", 422)


def subscriptions(c):
    return [dict(r) for r in c.execute("SELECT * FROM vrss_subscriptions ORDER BY title,id")]


def subscribe(url, fetcher=None):
    url = safe_url(url)
    # Subscribe stores first, then imports through the same revision fence used by refresh.
    with database() as c:
        old = c.execute("SELECT id FROM vrss_subscriptions WHERE url=?", (url,)).fetchone()
        if old:
            return {"subscription_id": old[0], "replayed": True}
        if c.execute("SELECT count(*) FROM vrss_subscriptions").fetchone()[0] >= 100:
            raise VRSSError("You can follow up to 100 feeds in VRSS v1.", 409)
        key = str(uuid.uuid4())
        c.execute("INSERT INTO vrss_subscriptions(id,url,title) VALUES(?,?,?)", (key, url, urlsplit(url).hostname))
    refresh(key, fetcher)
    return {"subscription_id": key, "replayed": False}


def refresh(key, fetcher=None):
    fetcher = fetcher or fetch_feed
    with database() as c:
        sub = c.execute("SELECT * FROM vrss_subscriptions WHERE id=?", (key,)).fetchone()
        if not sub:
            raise VRSSError("Feed subscription was removed.", 404)
        # One active refresh per source; reserve a new generation before leaving the transaction.
        if sub["checked_at"] and time.time() - datetime.fromisoformat(sub["checked_at"]).timestamp() < 60:
            raise VRSSError("Wait one minute before refreshing this feed again.", 429)
        revision = sub["revision"] + 1
        c.execute("UPDATE vrss_subscriptions SET revision=?,checked_at=? WHERE id=?", (revision, now(), key))
        url = sub["url"]
    try:
        blob, final_url = fetcher(url)
        parsed = parse_feed(blob, final_url)
    except VRSSError as exc:
        with database() as c:
            c.execute("UPDATE vrss_subscriptions SET error=? WHERE id=? AND revision=?", (str(exc)[:300], key, revision))
        raise
    with database() as c:
        sub = c.execute("SELECT * FROM vrss_subscriptions WHERE id=? AND revision=?", (key, revision)).fetchone()
        if not sub:
            raise VRSSError("Subscription changed while the feed was loading.", 409)
        c.execute("UPDATE vrss_subscriptions SET title=?,site_url=?,error='' WHERE id=?", (parsed["title"], parsed["site_url"], key))
        for item in reversed(parsed["items"]):
            c.execute("""INSERT INTO vrss_items(subscription_id,item_key,title,body,url,media,published_at,received_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subscription_id,item_key) DO UPDATE SET
                title=excluded.title,body=excluded.body,url=excluded.url,media=excluded.media,published_at=excluded.published_at""",
                      (key, item["key"], item["title"], item["body"], item["url"], json.dumps(item["media"]), item["published_at"], now()))
        c.execute("DELETE FROM vrss_items WHERE subscription_id=? AND seq NOT IN (SELECT seq FROM vrss_items WHERE subscription_id=? ORDER BY seq DESC LIMIT 100)", (key, key))
        return {"imported": len(parsed["items"])}


def request(body):
    action = body.get("action")
    if not isinstance(action, str):
        raise VRSSError("A VRSS action is required.", 422)
    if action in {"post.delete", "unsubscribe", "refresh"}:
        try:
            uuid.UUID(body.get("id", ""))
        except (ValueError, TypeError, AttributeError) as exc:
            raise VRSSError("A valid item ID is required.", 422) from exc
    result = {}
    if action == "profile.update":
        result["profile"] = update_profile(body)
    elif action == "post.create":
        result.update(create_post(body))
    elif action == "post.delete":
        with database() as c:
            c.execute("UPDATE vrss_posts SET deleted=1 WHERE id=?", (body.get("id"),))
            result["deleted"] = True
    elif action == "subscribe":
        result.update(subscribe(body.get("url")))
    elif action == "refresh":
        result.update(refresh(body.get("id")))
    elif action == "unsubscribe":
        with database() as c:
            c.execute("DELETE FROM vrss_subscriptions WHERE id=?", (body.get("id"),))
            result["removed"] = True
    elif action not in {"bootstrap", "profile", "feed", "subscriptions", "hosting"}:
        raise VRSSError("Unknown VRSS action.", 422)
    with database() as c:
        p = profile(c)
        if action in {"bootstrap", "profile"}:
            result["profile"] = p
        if action == "bootstrap":
            result["capabilities"] = {"rss_reader": True, "local_posts": True, "public_rss": True, "social_writes": False, "cloud_required": False}
        if action == "subscriptions":
            result["subscriptions"] = subscriptions(c)
        if action == "feed":
            before = body.get("before")
            params = []
            where = ""
            if before not in (None, "", 0):
                try:
                    if not isinstance(before, str) or len(before) > 160:
                        raise ValueError()
                    cursor = json.loads(before)
                    if (not isinstance(cursor, list) or len(cursor) != 3 or not isinstance(cursor[0], str)
                            or len(cursor[0]) > 40 or type(cursor[1]) is not int or cursor[1] not in {0, 1}
                            or type(cursor[2]) is not int or cursor[2] < 1):
                        raise ValueError()
                    where = " WHERE (sort_at,kind,seq)<(?,?,?)"
                    params = cursor
                except (ValueError, TypeError) as exc:
                    raise VRSSError("Invalid feed page.", 422) from exc
            timeline = "SELECT 0 AS kind,seq,created_at AS sort_at FROM vrss_posts WHERE deleted=0"
            if body.get("mode") != "profile":
                timeline += " UNION ALL SELECT 1 AS kind,seq,received_at AS sort_at FROM vrss_items"
            page = c.execute("SELECT * FROM (" + timeline + ")" + where + " ORDER BY sort_at DESC,kind DESC,seq DESC LIMIT 21", params).fetchall()
            items = []
            for pointer in page[:20]:
                if pointer["kind"] == 0:
                    row = c.execute("SELECT * FROM vrss_posts WHERE seq=?", (pointer["seq"],)).fetchone()
                    items.append(post_value(row, p))
                else:
                    row = c.execute("SELECT i.*,s.title AS source_title,s.url AS feed_url FROM vrss_items i JOIN vrss_subscriptions s ON s.id=i.subscription_id WHERE i.seq=?", (pointer["seq"],)).fetchone()
                    items.append({"id": "rss:" + row["subscription_id"] + ":" + row["item_key"], "body": row["body"], "title": row["title"],
                                  "media": json.loads(row["media"]), "created_at": row["published_at"] or row["received_at"], "sort_at": row["received_at"],
                                  "url": row["url"], "author": {"display_name": row["source_title"], "feed_url": row["feed_url"]}, "visibility": "source", "can_delete": False})
            last = page[19] if len(page) > 20 else None
            result["feed"] = {"items": items, "has_more": bool(last), "next_cursor": json.dumps([last["sort_at"], last["kind"], last["seq"]]) if last else ""}
    if action in {"bootstrap", "hosting"}:
        result["hosting"] = hosting_options()
    return {"ok": True, "contract": CONTRACT, "viewer_id": p["actor_id"], **result}


def public_document(kind, post_id=""):
    with database() as c:
        p = profile(c)
        if not p["enabled"] or not p["public_base"]:
            raise VRSSError("Public VRSS publishing is disabled.", 404)
        if p["hosting_site_id"]:
            try:
                hosted_address(p["hosting_site_id"], p["public_base"])
            except VRSSError as exc:
                raise VRSSError("Public VRSS Hosting is unavailable.", 404) from exc
        rows = c.execute("SELECT * FROM vrss_posts WHERE deleted=0 AND visibility='public' ORDER BY seq DESC LIMIT 100").fetchall()
        public = {"contract": CONTRACT, "id": p["actor_id"], "name": p["display_name"], "bio": p["bio"], "url": p["profile_url"], "rss": p["feed_url"], "capabilities": {"rss": True, "social_writes": False}}
        if kind == "profile":
            return public
        if kind == "post":
            row = c.execute("SELECT * FROM vrss_posts WHERE id=? AND deleted=0 AND visibility='public'", (post_id,)).fetchone()
            if not row:
                raise VRSSError("Post not found.", 404)
            value = post_value(row, p)
            value.pop("can_delete")
            value["author"] = public
            value["url"] = p["public_base"] + "/vrss/v1/posts/" + row["id"]
            return value
        ET.register_namespace("media", "http://search.yahoo.com/mrss/")
        root = ET.Element("rss", {"version": "2.0"})
        channel = ET.SubElement(root, "channel")
        for key, value in [("title", p["display_name"]), ("link", p["profile_url"]), ("description", p["bio"] or "Posts from " + p["display_name"]), ("generator", "VP3 VRSS 1.0")]:
            ET.SubElement(channel, key).text = value
        for row in rows:
            item = ET.SubElement(channel, "item")
            url = p["public_base"] + "/vrss/v1/posts/" + row["id"]
            ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = "urn:uuid:" + row["id"]
            ET.SubElement(item, "link").text = url
            ET.SubElement(item, "pubDate").text = format_datetime(datetime.fromisoformat(row["created_at"]))
            # RSS descriptions allow HTML; escape user text before XML serialization.
            description = html.escape(row["body"]).replace("\n", "<br>")
            for media in json.loads(row["media"]):
                description += '<p><a href="' + html.escape(media["url"], quote=True) + '">' + html.escape(media["alt"] or media["kind"]) + '</a></p>'
            ET.SubElement(item, "description").text = description
            # Media RSS supports unknown byte lengths without inventing RSS enclosure metadata.
            for media in json.loads(row["media"]):
                if media["kind"] != "link":
                    ET.SubElement(item, "{http://search.yahoo.com/mrss/}content", {"url": media["url"], "medium": media["kind"]})
            if len(ET.tostring(root, encoding="utf-8", xml_declaration=True)) > MAX_XML:
                channel.remove(item)
                break
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
