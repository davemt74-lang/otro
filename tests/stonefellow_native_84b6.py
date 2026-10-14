"""84B6: Stonefellow-native VP3 integration cannot bypass logged-in fan consent."""
from __future__ import annotations

import json
import secrets
from unittest.mock import patch

import httpx

from app.services import stonefellow_native as native
from app.services import homeserver_app_prebuilt as prebuilt

TOTAL = 0


def check(value: bool, label: str):
    global TOTAL
    if not value:
        raise AssertionError(label)
    TOTAL += 1
    print("PASS", label)


def denies(callback):
    try:
        callback()
    except native.StonefellowError:
        return True
    return False


cloud = "https://stonefellow.example"
member1 = "member-one-1234"
member2 = "member-two-1234"
vault = {}
pending = {}
verified_scope = {"fan.library.read": "sfhs_" + "1" * 64,
                  "admin.presence.read": "sfhs_" + "2" * 64}
allow_member = True


def mock_cloud(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host != "stonefellow.example":
        raise AssertionError("Unapproved network origin")
    if path == native.PAIR_PATH:
        body = json.loads(request.content)
        op = body["operation"]
        if op == "start":
            check(body["scope"] in native.SCOPES, "Pair requests have only fan or admin scope")
            request_id = secrets.token_hex(24)
            pending[request_id] = {**body, "approved": False}
            return httpx.Response(200, json={"ok": True,
                "contract": "stonefellow.homeserver-pair.v1",
                "request_id": request_id, "status": "pending",
                "scope": body["scope"],"expires_at":"2026-10-14T23:00:00Z"})
        if op == "redeem":
            row = pending.get(body["request_id"])
            if row is None or row.get("verifier") != body["verifier"]:
                return httpx.Response(403,json={"ok": False})
            if not row.get("approved"):
                return httpx.Response(200, json={"ok": True,"status":"pending"})
            return httpx.Response(200, json={"ok":True,"status":"connected",
                "scope":row["scope"],"member_binding":row["member_binding"],
                "credential_once":{"token":verified_scope[row["scope"]],
                                   "scope":row["scope"]}})
        raise AssertionError("Unknown Cloud pairing operation")
    authorization = request.headers.get("authorization", "")
    if path == native.FAN_PATH:
        if authorization == "Bearer " + verified_scope["fan.library.read"]:
            return httpx.Response(200,json={"ok":True,"contract":"stonefellow.fan-music.v1",
                "tracks":[{"id":"track-one","title":"Purchased Song","duration_seconds":185,
                           "can_stream":True,"can_download":True}],
                "other_user_orders":"never_returned"})
        return httpx.Response(401,json={"ok":False})
    if path == native.ADMIN_PATH:
        if authorization == "Bearer " + verified_scope["admin.presence.read"]:
            return httpx.Response(200,json={"ok":True,"contract":"stonefellow.admin-presence.v1",
                "catalog_track_count":5,"publishing_job_counts":{"queued":1}})
        return httpx.Response(401,json={"ok":False})
    raise AssertionError("Unknown Cloud endpoint")


def get_secret(app_key, key):
    return vault.get(key)


def set_secret(app_key, key, value):
    if app_key != native.APP_KEY:
        raise AssertionError("Unexpected secret vault")
    vault[key] = value
    return {"configured": True}


def remove_secret(app_key, key):
    vault.pop(key, None)
    return {"configured": True}


def assigned(member_id):
    if allow_member:
        return {"items":[{"app_key":native.APP_KEY,"allowed":1}]}
    return {"items":[]}


def fake_member(member_id):
    return {"member_id":member_id,"status":"active","role":"member"}


resolver=lambda host:["93.184.215.14"]
with patch.object(native.homeserver_apps,"get",return_value={"installed_version":"1.0.0"}), \
     patch.object(native.homeserver_app_security,"get_secret",side_effect=get_secret), \
     patch.object(native.homeserver_app_security,"set_secret",side_effect=set_secret), \
     patch.object(native.homeserver_app_security,"remove_secret",side_effect=remove_secret), \
     patch.object(native.homeserver_app_security,"require_permission",return_value={}), \
     patch.object(native.members,"assigned_apps",side_effect=assigned), \
     patch.object(native.members,"get_member",side_effect=fake_member):
    with httpx.Client(transport=httpx.MockTransport(mock_cloud),
                      follow_redirects=False,trust_env=False) as client:
        for bad in ["http://stonefellow.example","https://127.0.0.1",
                    "https://localhost","https://stonefellow.example:8443",
                    "https://attacker@stonefellow.example","https://stonefellow.example/private"]:
            check(denies(lambda bad=bad:native.normalize_origin(bad,resolver=resolver)),
                  "HomeServer blocks unsafe origin syntax")
        check(denies(lambda:native.normalize_origin(
            "https://stonefellow.example",resolver=lambda h:["192.168.0.4"])),
            "Private network DNS rebinding is blocked")
        configured=native.configure_origin(cloud,resolver=resolver)
        check(configured["origin_configured"] is True,
              "HomeServer owner sets the trusted Cloud origin")
        fan=native.start_pair("fan.library.read",member_id=member1,client=client,resolver=resolver)
        check(fan["account_login_required"] is True and fan["explicit_stonefellow_consent_required"] is True,
              "Fan must log into Stonefellow and explicitly grant HomeServer access")
        check(fan["authorization_url"].startswith(cloud+"/homeserver-connect.php?request_id="),
              "HomeServer offers first-party Stonefellow authorization page")
        serialized=json.dumps(fan)
        key=native._vault_key(member1,"PENDING_FAN")
        check("verifier" not in serialized and vault[key] and
              json.loads(vault[key])["verifier"] not in serialized,
              "Secret claim verifier never reaches browser or Agent Chat")
        check(native.finish_pair("fan.library.read",member_id=member1,
                                 client=client,resolver=resolver)["status"]==
              "awaiting_stonefellow_login_and_approval",
              "Unapproved Stonefellow login/consent cannot grant music access")
        check(native.fan_library(member1,client=client,resolver=resolver)["status"]==
              "not_connected","No fan library before Stonefellow authorization")
        check(denies(lambda:native.finish_pair("fan.library.read",member_id=member2,
                                               client=client,resolver=resolver)),
              "A second member cannot complete the first member's connection")
        pending[fan["request_id"]]["approved"]=True
        connected=native.finish_pair("fan.library.read",member_id=member1,
                                      client=client,resolver=resolver)
        check(connected["status"]=="connected" and
              vault[native._vault_key(member1,"FAN")]==verified_scope["fan.library.read"],
              "Approved Stonefellow fan token stored in member-specific protected HomeServer vault")
        check(key not in vault,"HomeServer deletes pending verifier after completed connection")
        library=native.fan_library(member1,client=client,resolver=resolver)
        check(library["count"]==1 and library["tracks"][0]["title"]=="Purchased Song",
              "Native HomeServer music catalog reads only fan-approved Stonefellow tracks")
        check(not library["admin_privileges"] and not library["local_stream_gateway_active"],
              "Fan permissions never imply administration or an unbuilt playback bridge")
        check(native.fan_library(member2,client=client,resolver=resolver)["count"]==0,
              "Other HomeServer member sees no purchased content")
        check(denies(lambda:native.start_pair("payments.write",member_id=member1,
                                              client=client,resolver=resolver)),
              "Provider payment or publishing scopes cannot be granted to member")
        admin=native.start_pair("admin.presence.read",client=client,resolver=resolver)
        check(admin["account_login_required"] and not admin["member_scoped"],
              "Admin must independently authorize from a Stonefellow administrator login")
        check(native.finish_pair("admin.presence.read",client=client,resolver=resolver)["status"]==
              "awaiting_stonefellow_login_and_approval",
              "Fan authorization does not automatically approve HomeServer administration")
        pending[admin["request_id"]]["approved"]=True
        admin_result=native.finish_pair("admin.presence.read",client=client,resolver=resolver)
        check(admin_result["status"]=="connected" and
              vault["ADMIN_CONNECTION_TOKEN"]!=vault[native._vault_key(member1,"FAN")],
              "Admin and fan receive cryptographically distinct scoped credentials")
        status=native.admin_status(client=client,resolver=resolver)
        check(status["catalog_track_count"]==5 and status["publishing_jobs"]["queued"]==1
              and not status["manufacturing_submission_enabled"],
              "Admin presence reads aggregate catalog and publishing queue without vendor writes")
        native.admin_disconnect()
        check("ADMIN_CONNECTION_TOKEN" not in vault and
              native.fan_library(member1,client=client,resolver=resolver)["count"]==1,
              "Admin disconnect does not revoke separate fan music identity")
        native.fan_disconnect(member1)
        check(native.fan_library(member1,client=client,resolver=resolver)["status"]=="not_connected",
              "Disconnect immediately removes local member access")
        global_app=prebuilt.CATALOG.get(native.APP_KEY)
        check(global_app is not None and global_app["kind"]=="stonefellow_music"
              and "network.external" in global_app["permissions"],
              "Stonefellow is a native VP3 app requiring owner-approved outbound network permission")
        package=prebuilt._package(global_app)
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            js=archive.read("assets/app.js").decode()
            html=archive.read("index.html").decode()
        check("Sign in to Stonefellow" in js and "My Purchased Music" in html,
              "Built-in VP3 app includes account login and explicit authorization UX")
        check("sfhs_" not in js and "ADMIN_CONNECTION_TOKEN" not in js,
              "Native UI never embeds access tokens or administrator secrets")

print(f"Section 84B6 VP3 native Stonefellow consent: {TOTAL} checks passed")
