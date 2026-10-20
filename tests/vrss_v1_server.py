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
    def fetch(url):
        return b'''<rss version="2.0"><channel><title>Music news</title><link>https://news.example/</link><description>Music</description><item><guid>news:1</guid><title>New album</title><description>&lt;p&gt;Listen today&lt;/p&gt;&lt;script&gt;window.injected=true&lt;/script&gt;</description><link>https://news.example/album</link><enclosure url="https://media.example/music.mp3" length="10" type="audio/mpeg"/></item></channel></rss>''', url
    vrss.fetch_document = lambda url, *args, **kwargs: {"body": fetch(url)[0], "url": url, "status": 200, "etag": "", "last_modified": ""}
    from app.runtime import app
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
