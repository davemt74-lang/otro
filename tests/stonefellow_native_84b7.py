"""84B7: bounded, member-isolated Stonefellow audio segments."""
import json
from unittest.mock import patch
import httpx
from app.services import stonefellow_native as sf

count=0
def ok(cond, message):
    global count
    assert cond, message
    count+=1
    print("PASS",message)
def denied(fn):
    try: fn()
    except sf.StonefellowError: return True
    return False

member="member-12345678"
another="member-98765432"
vault={"CLOUD_ORIGIN":"https://stonefellow.example",
       sf._vault_key(member,"FAN"):"sfhs_"+"f"*64}
requests=[]
def transport(request):
    requests.append(request)
    if request.headers.get("authorization")!="Bearer "+vault[sf._vault_key(member,"FAN")]:
        return httpx.Response(401,json={"ok":False})
    if request.url.path==sf.FAN_PATH:
        return httpx.Response(200,json={"ok":True,"contract":"stonefellow.fan-music.v1",
          "tracks":[{"id":"song-one","title":"My Song","duration_seconds":10,
                     "can_stream":True,"can_download":True,
                     "vinyl_print_rights":{"available":2}}]})
    if request.url.path!=sf.AUDIO_PATH or request.url.params.get("id")!="song-one":
        return httpx.Response(404)
    return httpx.Response(206,headers={"content-type":"audio/mpeg",
           "content-range":"bytes 0-3/4","set-cookie":"dont-forward=1"},content=b"abcd")
def secret(key,name):return vault.get(name)
def apps(member_id):return {"items":[{"app_key":sf.APP_KEY,"allowed":True}]}
with patch.object(sf.homeserver_apps,"get",return_value={"installed_version":"1.0.0"}),\
     patch.object(sf.homeserver_app_security,"get_secret",side_effect=secret),\
     patch.object(sf.homeserver_app_security,"require_permission",return_value={}),\
     patch.object(sf.members,"assigned_apps",side_effect=apps),\
     patch.object(sf.members,"get_member",side_effect=lambda m:{"status":"active","role":"member"}):
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        resolver=lambda h:["93.184.215.14"]
        ok(denied(lambda:sf.fan_audio(another,"song-one",client=client,resolver=resolver)),
           "Other member cannot use first member's token")
        ok(denied(lambda:sf.fan_audio(member,"../secret",client=client,resolver=resolver)),
           "No path traversal for track ID")
        ok(denied(lambda:sf.fan_audio(member,"song-one",range_header="bytes=0-9000000",
                                     client=client,resolver=resolver)),
           "Reject oversized transfer ranges before network")
        ok(sf.fan_audio(member,"song-one",download=True,client=client,resolver=resolver)["body"]==b"abcd",
           "Native owned download chunks use the approved Cloud media endpoint")
        ok(requests[-1].url.params.get("download")=="1",
           "Download chunks require the distinct Cloud digital download entitlement")
        song=sf.fan_audio(member,"song-one",client=client,resolver=resolver)
        ok(song["body"]==b"abcd" and song["status_code"]==206,"Scoped playback data forwarded")
        ok("Authorization" not in song["headers"] and "Set-Cookie" not in song["headers"],
           "Cloud credentials and cookies never forwarded")
        ok(requests[-1].headers["range"]=="bytes=0-4194303",
           "HomeServer sends bounded default range")
        ok(denied(lambda:sf.fan_audio(member,"song-one",range_header="bytes=-99999999",
                                     client=client,resolver=resolver)),
           "Reject oversized suffix range")
        ok(len(requests)==2,"Rejected requests never contact Cloud")
        agent=sf.fan_agent_context(member,client=client,resolver=resolver)
        ok(agent["tracks"][0]["vinyl_credits_available"]==2 and agent["read_only"],
           "Member Agent context receives only this account's read-only music information")
        ok("sfhs_" not in json.dumps(agent) and not agent["purchase_execution"],
           "Agent context excludes Stonefellow bearer tokens and payment authority")
print("84B7 HomeServer media contract",count,"checks passed")

# Two chunk 8-byte download fixture, with bounded transfer size for tests.
with patch.object(sf, "MAX_SEGMENT_BYTES", 4), \
     patch.object(sf.homeserver_apps,"get",return_value={"installed_version":"1.0.0"}), \
     patch.object(sf.homeserver_app_security,"get_secret",side_effect=secret), \
     patch.object(sf.homeserver_app_security,"require_permission",return_value={}), \
     patch.object(sf.members,"assigned_apps",side_effect=apps), \
     patch.object(sf.members,"get_member",side_effect=lambda m:{"status":"active","role":"member"}):
    def stream_mock(request):
        ok(request.url.params.get("download")=="1", "Every native download chunk requires independent Cloud download entitlement")
        at = request.headers["range"]
        if at == "bytes=0-3":
            return httpx.Response(206,headers={"content-type":"audio/mpeg",
                "content-range":"bytes 0-3/8"},content=b"abcd")
        if at == "bytes=4-7":
            return httpx.Response(206,headers={"content-type":"audio/mpeg",
                "content-range":"bytes 4-7/8"},content=b"efgh")
        return httpx.Response(416)
    with httpx.Client(transport=httpx.MockTransport(stream_mock)) as client:
        first=sf.fan_download_prepare(member, "song-one", client=client, resolver=resolver)
        ok(b"".join(sf.fan_download_chunks(member,"song-one",first,
                 client=client,resolver=resolver))==b"abcdefgh",
           "Native download retrieves bounded verified segments")
        bad=httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(206,headers={"content-type":"audio/mpeg",
                "content-range":"bytes 0-5/8"},content=b"abcd")))
        with bad:
            ok(denied(lambda:sf.fan_audio(member,"song-one",client=bad,resolver=resolver)),
               "Truncated or forged Cloud range rejected")
        vault.pop(sf._vault_key(member,"FAN"),None)
        ok(denied(lambda:sf.fan_audio(member,"song-one",client=client,resolver=resolver)),
           "Revoked fan credentials stop the next audio segment")

# Embedded VP3 app must actually expose the tested music endpoints to users.
from app.services import homeserver_app_prebuilt as prebuilt
from io import BytesIO
from zipfile import ZipFile
package=prebuilt._package(prebuilt.CATALOG[sf.APP_KEY])
with ZipFile(BytesIO(package)) as archive:
    js=archive.read("assets/app.js").decode("utf-8")
    html=archive.read("index.html").decode("utf-8")
ok("nativePlayer" in html and "/audio/" in js and "/download/" in js,
   "Packaged Stonefellow app renders approved playback and owned downloads")
ok("vinyl_print_rights" in js and "ownerPanel" in html,
   "Packaged app shows read-only vinyl balances and independent admin scope")
ok("sfhs_" not in js and "ADMIN_CONNECTION_TOKEN" not in js,
   "Browser assets include no Cloud capability tokens")
ok(prebuilt.CATALOG[sf.APP_KEY]["version"]=="1.1.0",
   "Versioned built-in app advertises native music upgrade")
