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
    if request.url.path!=sf.AUDIO_PATH or request.url.params.get("id")!="song-one":
        return httpx.Response(404)
    return httpx.Response(206,headers={"content-type":"audio/mpeg",
           "content-range":"bytes 0-3/100","set-cookie":"dont-forward=1"},content=b"abcd")
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
        ok(denied(lambda:sf.fan_audio(member,"song-one",download=True,client=client,resolver=resolver)),
           "Full downloads remain on Stonefellow website")
        song=sf.fan_audio(member,"song-one",client=client,resolver=resolver)
        ok(song["body"]==b"abcd" and song["status_code"]==206,"Scoped playback data forwarded")
        ok("Authorization" not in song["headers"] and "Set-Cookie" not in song["headers"],
           "Cloud credentials and cookies never forwarded")
        ok(requests[-1].headers["range"]=="bytes=0-4194303",
           "HomeServer sends bounded default range")
        ok(denied(lambda:sf.fan_audio(member,"song-one",range_header="bytes=-99999999",
                                     client=client,resolver=resolver)),
           "Reject oversized suffix range")
        ok(len(requests)==1,"Rejected requests never contact Cloud")
print("84B7 HomeServer media contract",count,"checks passed")
