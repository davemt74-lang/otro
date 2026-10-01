from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-library-agent-v354-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-agent-src-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    source=Path(media_dir)
    first=source/"Agent Copy A.jpg"
    second=source/"Agent Copy B.jpg"
    first.write_bytes(b"same-agent-media")
    second.write_bytes(b"same-agent-media")
    originals={p.name:p.read_bytes() for p in (first,second)}

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_agent, homeserver_app_control
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        for key in ("vp3.media-server","vp3.media-library"):
            r=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{key}/install")
            assert r.status_code==200,r.text

        assert client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        }).status_code==200
        assert client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Agent Media","source_kind":"local_folder"
        }).status_code==200
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text

        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"limit":20}).json()
        ids={row["name"]:row["media_id"] for row in library["items"]}
        first_id=ids[first.name]

        # Create a real recent change while deliberately leaving some metadata debt.
        updated=client.put(f"/api/v1/control/homeserver-apps/media-library/items/{first_id}",json={
            "patch":{"title":"Agent Reviewed Copy","tags":["agent-review"]},
            "actor":"user:test",
            "source":"section21e.acceptance",
            "reason":"Seed Agent Brain recent change"
        })
        assert updated.status_code==200,updated.text

        # Explicit duplicate scan creates cleanup attention without deleting anything.
        dup=client.post("/api/v1/control/homeserver-apps/media-library/duplicates/scan",params={"limit":50})
        assert dup.status_code==200,dup.text
        assert dup.json()["exact_groups"]>=1

        debt=client.get("/api/v1/control/homeserver-apps/media-library/needs-metadata",params={"limit":20})
        assert debt.status_code==200,debt.text
        assert debt.json()["count"]>=1
        assert all("media_id" in row and "missing" in row for row in debt.json()["items"])
        assert media_dir not in debt.text

        changes=client.get("/api/v1/control/homeserver-apps/media-library/recent-changes",params={"limit":10})
        assert changes.status_code==200,changes.text
        assert changes.json()["count"]>=1
        assert changes.json()["changes"][0]["source"]=="section21e.acceptance"
        assert media_dir not in changes.text

        brain=client.get("/api/v1/control/homeserver-apps/media-library/brain-context",params={"limit":8})
        assert brain.status_code==200,brain.text
        attention={row["type"]:row for row in brain.json()["attention"]}
        assert "metadata_debt" in attention
        assert "duplicate_review" in attention
        assert brain.json()["summary"]["cleanup_needs_review"]>=1
        assert brain.json()["recent_changes"]
        assert media_dir not in brain.text

        brief=client.get("/api/v1/control/homeserver-apps/media-library/agent-brief",params={"limit":8})
        assert brief.status_code==200,brief.text
        payload=brief.json()
        assert payload["contract"]=="vp3.media-library.agent-brief.v1"
        suggestion_types={row["type"] for row in payload["suggested_next_steps"]}
        assert "metadata_enrichment" in suggestion_types
        assert "duplicate_review" in suggestion_types
        assert payload["governance"]["homeserver_execution_authority"] is True
        assert payload["governance"]["universal_agent_control"] is True
        assert payload["governance"]["consequential_actions_require_confirmation"] is True
        assert payload["governance"]["automatic_source_deletion"] is False
        assert media_dir not in brief.text

        # Agent Chat discovers the same manifest through the generic Apps Agent layer.
        actions=homeserver_app_agent.app_actions({"app_key":"vp3.media-library"})
        by_key={row["key"]:row for row in actions["actions"]}
        assert actions["complete_control"] is True
        assert actions["compatible"] is True
        assert by_key["library.agent-brief"]["risk"]=="read"
        assert by_key["library.needs-metadata"]["risk"]=="read"
        assert by_key["library.recent-changes"]["risk"]=="read"
        assert by_key["library.artwork.generate"]["risk"]=="consequential"
        assert by_key["library.artwork.generate"]["requires_confirmation"] is True

        # Generic Agent Chat read path can invoke the media brief directly.
        agent_read=homeserver_app_agent.invoke_read({
            "app_key":"vp3.media-library",
            "action":"library.agent-brief",
            "arguments":{"limit":8},
        })
        assert agent_read["contract"]=="vp3.app.agent-action-result.v1"
        assert agent_read["app_key"]=="vp3.media-library"
        assert agent_read["action"]=="library.agent-brief"
        assert agent_read["risk"]=="read"
        assert agent_read["result"]["contract"]=="vp3.media-library.agent-brief.v1"
        assert any(row["type"]=="duplicate_review" for row in agent_read["result"]["suggested_next_steps"])

        # Consequential action cannot be smuggled through the read-only Agent path.
        try:
            homeserver_app_agent.invoke_read({
                "app_key":"vp3.media-library",
                "action":"library.artwork.generate",
                "arguments":{"target_type":"media","target_id":first_id,"role":"poster"},
            })
            raise AssertionError("Consequential artwork action entered Agent read path")
        except homeserver_app_agent.AppAgentError as exc:
            assert exc.status_code==409

        # Universal app-control endpoint exposes the same brief.
        generic=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.agent-brief","arguments":{"limit":8}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["contract"]=="vp3.media-library.agent-brief.v1"

        cap=client.get("/api/v1/control/homeserver-apps/media-library/capability").json()
        assert cap["agent_brain_context"] is True
        assert cap["agent_metadata_debt"] is True
        assert cap["agent_recent_changes"] is True
        assert cap["agent_cleanup_attention"] is True
        assert cap["agent_artwork_attention"] is True
        assert cap["agent_media_brief"] is True
        assert cap["governed_next_steps"] is True
        assert cap["universal_agent_control"] is True

        for p in (first,second):
            assert p.read_bytes()==originals[p.name]

print("HomeServer Section 21E Agent Chat & Agent Brain Media Intelligence: PASS")
