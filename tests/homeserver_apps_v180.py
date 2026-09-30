from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))


def package(app_key:str,name:str,version:str="1.0.0",extra:dict[str,bytes]|None=None)->bytes:
    manifest={
        "contract":"vp3.app.package.v1",
        "app_key":app_key,
        "name":name,
        "version":version,
        "runtime":"static",
        "entrypoint":"index.html",
        "sdk_version":"1.0",
        "permissions":[],
        "routes":{"local":True,"private_remote":False,"public":False},
    }
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("vp3-app.json",json.dumps(manifest,separators=(",",":")))
        archive.writestr("index.html","<!doctype html><title>Test</title>")
        for path,data in (extra or {}).items():
            archive.writestr(path,data)
    return buffer.getvalue()


with tempfile.TemporaryDirectory(prefix="homeserver-apps-v180-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import agent_tools, homeserver_app_approvals, homeserver_app_sources, homeserver_apps  # noqa: E402

    initialize_database()

    first=package("import.demo","Import Demo","1.0.0")
    inspected=homeserver_app_sources.inspect_zip(first,"Import Demo.zip")
    assert inspected["source_type"]=="zip"
    assert inspected["status"]=="inspected"
    assert inspected["manifest"]["app_key"]=="import.demo"
    assert len(inspected["package_sha256"])==64
    try:
        homeserver_apps.get("import.demo")
        raise AssertionError("Inspection installed the app before approval")
    except homeserver_apps.HomeServerAppError as exc:
        assert exc.status_code==404

    try:
        homeserver_app_sources.install_source(inspected["source_id"])
        raise AssertionError("Source installed without owner approval")
    except homeserver_app_sources.AppSourceError as exc:
        assert exc.status_code==409

    installed=homeserver_app_sources.install_source(inspected["source_id"],approved=True)
    assert installed["app"]["app_key"]=="import.demo"
    assert installed["app"]["lifecycle_state"]=="running"
    assert installed["app"]["source_type"]=="zip"
    assert installed["release"]["source_id"]==inspected["source_id"]
    assert installed["release"]["source_ref"]=="Import Demo.zip"
    status=homeserver_app_sources.source_status("import.demo")
    assert status["current"]["source_id"]==inspected["source_id"]
    assert status["update_available"] is False

    second=homeserver_app_sources.inspect_zip(package("import.demo","Import Demo","1.1.0"),"update.zip")
    status=homeserver_app_sources.source_status("import.demo")
    assert status["current"]["source_id"]==second["source_id"]
    assert status["update_available"] is True
    updated=homeserver_app_sources.install_source(second["source_id"],approved=True)
    assert updated["app"]["installed_version"]=="1.1.0"
    assert homeserver_app_sources.source_status("import.demo")["update_available"] is False

    # Cached source bytes are re-hashed at install time.
    tampered=homeserver_app_sources.inspect_zip(package("tamper.demo","Tamper Demo"),"tamper.zip")
    with db() as connection:
        row=connection.execute("SELECT cache_path FROM homeserver_app_sources WHERE source_id=?",(tampered["source_id"],)).fetchone()
    Path(row["cache_path"]).write_bytes(b"changed after inspection")
    try:
        homeserver_app_sources.install_source(tampered["source_id"],approved=True)
        raise AssertionError("Tampered source cache installed")
    except homeserver_app_sources.AppSourceError as exc:
        assert exc.status_code==409

    # Protected VP3 system app identities cannot be replaced by imports.
    homeserver_apps.ensure_system_app("vp3.protected","Protected")
    protected=homeserver_app_sources.inspect_zip(package("vp3.protected","Protected","9.9.9"),"protected.zip")
    try:
        homeserver_app_sources.install_source(protected["source_id"],approved=True)
        raise AssertionError("Imported source replaced protected VP3 system app")
    except homeserver_app_sources.AppSourceError as exc:
        assert exc.status_code==409

    # ZIP traversal is rejected during inspection.
    unsafe=io.BytesIO()
    with zipfile.ZipFile(unsafe,"w",zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt","x")
        archive.writestr("vp3-app.json","{}")
    try:
        homeserver_app_sources.inspect_zip(unsafe.getvalue(),"unsafe.zip")
        raise AssertionError("Path traversal ZIP was accepted")
    except homeserver_app_sources.AppSourceError:
        pass

    for bad in (
        "http://github.com/org/app.git",
        "https://user:secret@github.com/org/app.git",
        "https://localhost/org/app.git",
        "https://127.0.0.1/org/app.git",
        "https://10.0.0.4/org/app.git",
        "https://example.com/org/app.git",
    ):
        try:
            homeserver_app_sources._safe_public_url(bad)
            raise AssertionError(f"Unsafe Git URL accepted: {bad}")
        except homeserver_app_sources.AppSourceError:
            pass

    # Git import records the requested ref and immutable resolved SHA while
    # never persisting URL credentials/query tokens.
    git_package=package("git.demo","Git Demo","2.0.0")
    exact_sha="a"*40
    original_run_git=homeserver_app_sources._run_git

    def fake_run_git(args:list[str],cwd:Path)->str:
        if args and args[0]=="rev-parse":
            return exact_sha
        if args and args[0]=="archive":
            output=next(item.split("=",1)[1] for item in args if item.startswith("--output="))
            Path(output).write_bytes(git_package)
        return ""

    homeserver_app_sources._run_git=fake_run_git
    try:
        git_source=homeserver_app_sources.inspect_git(
            "https://github.com/example/demo.git?token=must-not-persist",
            "main",
        )
        assert git_source["source_ref"]=="https://github.com/example/demo.git"
        assert git_source["source_revision"]==exact_sha
        assert git_source["validation"]["git_requested_ref"]=="main"

        # Agent source reads and consequential Git/source actions are owner-only.
        policy=agent_tools.save_policy(True,3,True)
        assert policy["allow_write_proposals"] is True
        schemas=agent_tools.model_tool_schemas(set(),owner=True,allow_write_proposals=True,source_app_key="owner")
        names={item["function"]["name"] for item in schemas}
        assert "homeserver_app_source_status" in names
        assert "homeserver_app_git_inspect_request" in names
        assert "homeserver_app_source_install_request" in names
        assert "homeserver_app_source_detach_request" in names

        proposal=agent_tools.execute_model_tool(
            "owner",
            "homeserver_app_git_inspect_request",
            {"repo_url":"https://github.com/example/demo.git","ref":"main"},
            set(),
            owner=True,
        )
        request_id=proposal["result"]["request_id"]
        assert proposal["result"]["status"]=="pending"
        approved=homeserver_app_approvals.approve(request_id)
        assert approved["status"]=="executed"
    finally:
        homeserver_app_sources._run_git=original_run_git

    detached=homeserver_app_sources.detach("import.demo",confirmed=True)
    assert detached["current"] is None
    preserved=homeserver_apps.get("import.demo")
    assert preserved["lifecycle_state"]=="running"
    assert preserved["source_type"]=="user_created"

    history=homeserver_app_sources.source_history("import.demo")
    assert history["count"]>=2
    assert any(row["status"]=="detached" for row in history["sources"])

    cap=homeserver_app_sources.public_capability()
    assert cap["zip_import"] is True
    assert cap["git_import"] is True
    assert cap["git_public_hosts"]==["bitbucket.org","github.com","gitlab.com"]
    assert cap["exact_git_sha"] is True
    assert cap["owner_approval_required"] is True
    assert cap["protected_system_overwrite"] is False

print("HomeServer Apps V1 Section 9 ZIP Git import and source management: PASS")
