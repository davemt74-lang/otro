from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-video-editor-v290-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_agent, homeserver_app_manager, homeserver_app_prebuilt, homeserver_video_editor
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200

        catalog=client.get("/api/v1/control/homeserver-apps/catalog/prebuilt")
        assert catalog.status_code==200,catalog.text
        by_key={row["key"]:row for row in catalog.json()["packages"]}
        assert "vp3.video-editor" in by_key
        video=by_key["vp3.video-editor"]
        assert video["product_type"]=="vp3_optional_app"
        assert "hosted_subdomain" in video["deployment_modes"]

        install=client.post("/api/v1/control/homeserver-apps/catalog/prebuilt/vp3.video-editor/install")
        assert install.status_code==200,install.text
        assert install.json()["app"]["app_key"]=="vp3.video-editor"

        cap=client.get("/api/v1/control/homeserver-apps/video-editor/capability")
        assert cap.status_code==200,cap.text
        assert cap.json()["agent_complete_control"] is True
        assert cap.json()["media_server_source_library"] is True
        assert cap.json()["non_destructive_source_media"] is True

        created=client.post("/api/v1/control/homeserver-apps/video-editor/projects",json={
            "name":"Section 15 Demo","width":1920,"height":1080,"fps":30
        })
        assert created.status_code==200,created.text
        project=created.json()
        pid=project["project"]["project_id"]
        assert len(project["tracks"])==2

        listed=client.get("/api/v1/control/homeserver-apps/video-editor/projects")
        assert listed.status_code==200
        assert listed.json()["count"]==1

        actions=client.get("/api/v1/control/homeserver-apps/video-editor/agent-actions")
        assert actions.status_code==200
        action_map={row["key"]:row for row in actions.json()["actions"]}
        assert action_map["video.project.create"]["requires_confirmation"] is False
        assert action_map["video.clip.remove"]["requires_confirmation"] is True
        assert action_map["video.render.queue"]["requires_confirmation"] is True

        agent_create=client.post("/api/v1/control/homeserver-apps/video-editor/agent-invoke",json={
            "action":"video.project.create","arguments":{"name":"Agent Project"}
        })
        assert agent_create.status_code==200,agent_create.text

        blocked=client.post("/api/v1/control/homeserver-apps/video-editor/agent-invoke",json={
            "action":"video.render.queue","arguments":{"project_id":pid},"confirmed":False
        })
        assert blocked.status_code==409

        manager=homeserver_app_manager.inventory()
        item=next(row for row in manager["items"] if row["app_key"]=="vp3.video-editor")
        assert item["agent_control"]["complete"] is True
        assert item["video_editor"]["source_media_owned_by_editor"] is False
        assert manager["homeserver_agent_complete_control"] is True

        acap=homeserver_app_agent.public_capability()
        assert acap["complete_control_over_installed_apps"] is True
        assert acap["manifest_driven_app_actions"] is True
        assert acap["system_app_lifecycle_control"] is True
        assert acap["permission_control"] is True

        manifest=homeserver_app_agent.app_actions({"app_key":"vp3.video-editor"})
        assert manifest["complete_control"] is True
        assert any(row["key"]=="video.project.create" for row in manifest["actions"])

        read=homeserver_app_agent.invoke_read({
            "app_key":"vp3.video-editor","action":"video.projects.list","arguments":{"limit":20}
        })
        assert read["result"]["count"]>=2

        # Agent can stop and restart a protected system app through the same canonical lifecycle.
        stopped=homeserver_app_agent.execute_action("apps.stop",{"app_key":"vp3.video-editor"})
        assert stopped["app"]["lifecycle_state"]=="stopped"
        started=homeserver_app_agent.execute_action("apps.start",{"app_key":"vp3.video-editor"})
        assert started["app"]["lifecycle_state"]=="running"

        status=homeserver_video_editor.status()
        assert status["projects"]>=2
        assert status["source_media_owned_by_editor"] is False
        assert status["render_execution"]=="homeserver_local_worker"

print("HomeServer Section 15 Video Editor + complete App Agent control: PASS")
