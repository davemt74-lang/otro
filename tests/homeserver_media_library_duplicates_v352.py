from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-media-library-cleanup-v352-") as data_dir, tempfile.TemporaryDirectory(prefix="vp3-media-cleanup-src-") as media_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir
    source=Path(media_dir)
    source.mkdir(parents=True,exist_ok=True)

    exact_a=source/"Exact A.jpg"
    exact_b=source/"Exact B.jpg"
    exact_bytes=b"exact-duplicate-content-12345"
    exact_a.write_bytes(exact_bytes)
    exact_b.write_bytes(exact_bytes)

    candidate_a=source/"Candidate A.jpg"
    candidate_b=source/"Candidate B.jpg"
    candidate_a.write_bytes(b"candidate-content-AAAA")
    candidate_b.write_bytes(b"candidate-content-BBBB")
    assert candidate_a.stat().st_size==candidate_b.stat().st_size

    near_a=source/"Road Trip.jpg"
    near_b=source/"Road Trip (1).jpg"
    near_a.write_bytes(b"near-a")
    near_b.write_bytes(b"near-b-longer")

    original={p.name:p.read_bytes() for p in source.iterdir()}

    from app.runtime import app
    from app.security import OWNER_CONTROL_TOKEN
    from app.services import homeserver_app_control
    from app.services.tasks import scheduler

    with TestClient(app) as client:
        scheduler.stop()
        assert client.post("/__owner/session",headers={"X-HomeServer-Owner":OWNER_CONTROL_TOKEN}).status_code==200
        for key in ("vp3.media-server","vp3.media-library"):
            response=client.post(f"/api/v1/control/homeserver-apps/catalog/prebuilt/{key}/install")
            assert response.status_code==200,response.text

        assert client.put("/api/v1/control/homeserver-apps/vp3.media-server/permissions",json={
            "permission":"files.read","allowed":True
        }).status_code==200
        assert client.post("/api/v1/control/homeserver-apps/media-server/mapped-roots",json={
            "path":str(source),"label":"Cleanup Sources","source_kind":"local_folder"
        }).status_code==200
        scan=client.post("/api/v1/control/homeserver-apps/media-server/scan")
        assert scan.status_code==200,scan.text

        library=client.get("/api/v1/control/homeserver-apps/media-server/library",params={"media_type":"image","limit":100}).json()
        ids={row["name"]:row["media_id"] for row in library["items"]}
        assert set(original).issubset(ids)

        # Create a real metadata conflict inside the exact duplicate pair.
        for name,patch in (
            ("Exact A.jpg",{"title":"Keep This","tags":["keeper"],"rating":5}),
            ("Exact B.jpg",{"title":"Duplicate Copy","tags":["duplicate"],"rating":2}),
        ):
            update=client.put(f"/api/v1/control/homeserver-apps/media-library/items/{ids[name]}",json={
                "patch":patch,"source":"section21c.acceptance"
            })
            assert update.status_code==200,update.text

        before=client.get("/api/v1/control/homeserver-apps/media-library/cleanup/status")
        assert before.status_code==200,before.text
        assert before.json()["scan_required"] is True
        cheap=client.get("/api/v1/control/homeserver-apps/media-library/duplicates",params={"limit":100})
        assert cheap.status_code==200,cheap.text
        assert cheap.json()["count"]==0
        assert cheap.json()["scan_required"] is True

        result=client.post("/api/v1/control/homeserver-apps/media-library/duplicates/scan",params={"limit":100})
        assert result.status_code==200,result.text
        body=result.json()
        assert body["items_scanned"]==6
        assert body["snapshot_persisted"] is True
        assert body["source_files_modified"] is False
        assert body["source_files_deleted"] is False
        assert body["filesystem_paths_exposed"] is False
        assert media_dir not in result.text

        exact_groups=[g for g in body["groups"] if g["kind"]=="exact"]
        assert len(exact_groups)>=1
        exact=next(g for g in exact_groups if set(g["media_ids"])=={ids["Exact A.jpg"],ids["Exact B.jpg"]})
        assert exact["content_hash_verified"] is True
        assert exact["confidence"]=="verified"
        assert "title" in exact["metadata_conflicts"]
        assert "tags" in exact["metadata_conflicts"]
        assert exact["review"]["decision"]=="needs_review"

        size_candidates=[g for g in body["groups"] if g["kind"]=="same_size_candidate"]
        assert any({ids["Candidate A.jpg"],ids["Candidate B.jpg"]}.issubset(set(g["media_ids"])) for g in size_candidates)
        assert all(g["content_hash_verified"] is False and g["confidence"]=="candidate" for g in size_candidates)

        near_candidates=[g for g in body["groups"] if g["kind"]=="near_name_candidate"]
        assert any({ids["Road Trip.jpg"],ids["Road Trip (1).jpg"]}.issubset(set(g["media_ids"])) for g in near_candidates)
        assert all(g["content_hash_verified"] is False for g in near_candidates)

        # Reads now use the persisted scan snapshot and do not hash source files again.
        second=client.get("/api/v1/control/homeserver-apps/media-library/duplicates",params={"kind":"exact","limit":100})
        assert second.status_code==200,second.text
        assert second.json()["snapshot_persisted"] is True
        exact2=next(g for g in second.json()["groups"] if g["group_key"]==exact["group_key"])
        assert exact2["sha256"]==exact["sha256"]

        reviewed=client.put(
            f"/api/v1/control/homeserver-apps/media-library/duplicates/{exact['group_key']}/review",
            json={
                "decision":"resolved",
                "primary_media_id":ids["Exact A.jpg"],
                "note":"Keep richer metadata copy."
            },
        )
        assert reviewed.status_code==200,reviewed.text
        assert reviewed.json()["decision"]=="resolved"
        assert reviewed.json()["source_files_deleted"] is False
        assert reviewed.json()["metadata_deleted"] is False

        reread=client.get("/api/v1/control/homeserver-apps/media-library/duplicates",params={"kind":"exact"})
        exact3=next(g for g in reread.json()["groups"] if g["group_key"]==exact["group_key"])
        assert exact3["review"]["decision"]=="resolved"
        assert exact3["review"]["primary_media_id"]==ids["Exact A.jpg"]

        cleanup=client.get("/api/v1/control/homeserver-apps/media-library/cleanup/status")
        assert cleanup.status_code==200,cleanup.text
        assert cleanup.json()["exact_groups"]>=1
        assert cleanup.json()["candidate_groups"]>=1
        assert cleanup.json()["metadata_conflict_groups"]>=1
        assert cleanup.json()["automatic_source_deletion"] is False

        actions={row["key"]:row for row in homeserver_app_control.manifest("vp3.media-library")["actions"]}
        assert actions["library.duplicates.scan"]["risk"]=="background"
        assert actions["library.duplicates"]["risk"]=="read"
        assert "refresh" not in actions["library.duplicates"]["input_schema"]["properties"]
        assert actions["library.duplicate.review"]["risk"]=="write"
        assert actions["library.cleanup.status"]["risk"]=="read"

        generic=client.post("/api/v1/control/homeserver-apps/vp3.media-library/control/invoke",json={
            "action":"library.cleanup.status","arguments":{}
        })
        assert generic.status_code==200,generic.text
        assert generic.json()["result"]["automatic_source_deletion"] is False

        cap=client.get("/api/v1/control/homeserver-apps/media-library/capability").json()
        assert cap["exact_sha256_duplicates"] is True
        assert cap["likely_duplicate_candidates"] is True
        assert cap["near_duplicate_candidates"] is True
        assert cap["metadata_conflict_detection"] is True
        assert cap["duplicate_review_workflow"] is True
        assert cap["persisted_duplicate_snapshot"] is True
        assert cap["explicit_duplicate_scan"] is True
        assert cap["automatic_source_deletion"] is False

        for path in source.iterdir():
            assert path.read_bytes()==original[path.name]

print("HomeServer Section 21C Duplicate & Cleanup Center: PASS")
