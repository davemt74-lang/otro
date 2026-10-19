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
    def fetch(url):
        return b'''<rss version="2.0"><channel><title>Music news</title><link>https://news.example/</link><description>Music</description><item><guid>news:1</guid><title>New album</title><description>&lt;p&gt;Listen today&lt;/p&gt;&lt;script&gt;window.injected=true&lt;/script&gt;</description><link>https://news.example/album</link><enclosure url="https://media.example/music.mp3" length="10" type="audio/mpeg"/></item></channel></rss>''', url
    vrss.fetch_feed = fetch
    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(json.dumps({"url": "http://127.0.0.1:" + str(sock.getsockname()[1]), "owner": OWNER_CONTROL_TOKEN}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off")).run(sockets=[sock])
