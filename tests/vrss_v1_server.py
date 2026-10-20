"""Local browser fixture: production owner API/storage/UI, deterministic external RSS only."""
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="vrss-browser-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import initialize_database
    from app.services import vrss, homeserver_app_prebuilt
    initialize_database()
    homeserver_app_prebuilt.ensure_defaults()
    from vrss_v1_hosting import configure_hosting
    _, hosted_site, _, hosted_route = configure_hosting()
    remote_post_id='11111111-1111-4111-8111-111111111111'
    remote_post='https://bob.example/vrss/v1/posts/'+remote_post_id
    remote_counts={'likes':0}
    def fetch(url):
        if os.environ.get('VRSS_REACTIONS_TEST') and url=='https://bob.example/vrss/v1/rss':
            return ('<rss version="2.0"><channel><title>Bob VRSS</title><description>Posts</description><link>https://bob.example/</link><item><guid>remote-vrss-post</guid><title>Remote VRSS post</title><description>A public post</description><link>'+remote_post+'</link></item></channel></rss>').encode(),url
        return b'''<rss version="2.0"><channel><title>Music news</title><link>https://news.example/</link><description>Music</description><item><guid>news:1</guid><title>New album</title><description>&lt;p&gt;Listen today&lt;/p&gt;&lt;script&gt;window.injected=true&lt;/script&gt;</description><link>https://news.example/album</link><enclosure url="https://media.example/music.mp3" length="10" type="audio/mpeg"/></item></channel></rss>''', url
    vrss.fetch_document = lambda url, *args, **kwargs: {"body": fetch(url)[0], "url": url, "status": 200, "etag": "", "last_modified": ""}
    if os.environ.get('VRSS_SOCIAL_TEST'):
        import time,uuid
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from app.services import vrss_social as social
        remote_key=Ed25519PrivateKey.generate();remote_seq=[0]
        remote_actor={'contract':social.CONTRACT,'id':'https://bob.example/vrss/v1/actor','identity':'urn:uuid:'+str(uuid.uuid4()),'name':'Bob <untrusted>','inbox':'https://bob.example/vrss/v1/inbox','rss':'https://bob.example/vrss/v1/rss','public_key':remote_key.public_key().public_bytes_raw().hex(),'rotations':[],'interactions':{'contract':'vp3.vrss.interactions.v1','likes':bool(os.environ.get('VRSS_REACTIONS_TEST'))}}
        def social_exchange(url,payload=None):
            if payload is None:
                if os.environ.get('VRSS_REACTIONS_TEST') and url==remote_post+'/interactions':return {'contract':'vp3.vrss.interactions.v1','object':remote_post,'actor':remote_actor['id'],'identity':remote_actor['identity'],'likes':remote_counts['likes']}
                assert url==remote_actor['id'];return remote_actor
            assert url==remote_actor['inbox']
            if payload['activity']['type'] in {'Like','Unlike'}:
                remote_counts['likes']=int(payload['activity']['type']=='Like')
                return {'contract':social.CONTRACT,'ok':True,'interaction':{'object':payload['activity']['object'],'actor':remote_actor['id'],'likes':remote_counts['likes']}}
            return {'contract':social.CONTRACT,'ok':True}
        social.exchange=social_exchange
    from app.runtime import app
    if os.environ.get('VRSS_SOCIAL_TEST'):
        from fastapi import Request
        @app.inner_app.post('/api/v1/control/vrss-test-pump')
        def social_pump():social.deliver();return {'ok':True}
        @app.inner_app.post('/api/v1/control/vrss-test-social-event')
        async def social_event(request:Request):
            data=await request.json();kind=data.get('kind','Follow');remote_seq[0]+=1
            with vrss.database() as c:
                p=vrss.profile(c)
                relation=c.execute("SELECT follow_id FROM vrss_social_relations WHERE actor=? AND direction='out'",(remote_actor['id'],)).fetchone()
            activity={'contract':social.CONTRACT,'id':'https://bob.example/vrss/v1/activities/'+str(uuid.uuid4()),'actor':remote_actor['id'],'identity':remote_actor['identity'],'recipient':social.actor_url(p),'type':kind,'follow_id':relation[0] if kind=='Accept' else 'https://bob.example/vrss/v1/follows/'+str(uuid.uuid4()),'seq':remote_seq[0],'issued_at':time.time()}
            if kind in {'Like','Unlike'}:
                with vrss.database() as c:inbound=c.execute("SELECT follow_id FROM vrss_social_relations WHERE actor=? AND direction='in'",(remote_actor['id'],)).fetchone()
                activity.update(contract='vp3.vrss.interactions.v1',follow_id=inbound[0],object=p['public_base']+'/vrss/v1/posts/'+data['post_id'],reaction_id='https://bob.example/vrss/v1/reactions/'+str(uuid.uuid4()))
            packet={'activity':activity,'sent_at':int(time.time())};packet['signature']=remote_key.sign(social.canonical(packet)).hex()
            try:
                result=social.receive(social.canonical(packet));social.deliver_notices();return result
            except vrss.VRSSError as e:
                from fastapi.responses import JSONResponse
                return JSONResponse({'detail':str(e)},status_code=e.status_code)
    @app.inner_app.post('/api/v1/control/vrss-test-new-post')
    def new_post_fixture():
        with vrss.database() as c:
            row=c.execute('SELECT id,url FROM vrss_subscriptions LIMIT 1').fetchone()
            c.execute("UPDATE vrss_subscriptions SET checked_at='',lease_until=0")
        if row:
            blob=fetch(row['url'])[0].replace(b'</channel>',b'<item><guid>new-post-fixture</guid><title>New background post</title></item></channel>')
            return vrss.refresh(row['id'],lambda _: (blob,row['url']))
        return {'new':0}
    from app.security import OWNER_CONTROL_TOKEN
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(json.dumps({"url": "http://127.0.0.1:" + str(sock.getsockname()[1]), "owner": OWNER_CONTROL_TOKEN, "hosting_site_id": hosted_site["site_id"], "route_token": hosted_route["route_token"]}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off")).run(sockets=[sock])
