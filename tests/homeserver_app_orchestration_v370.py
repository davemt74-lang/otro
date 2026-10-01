from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

with tempfile.TemporaryDirectory(prefix="homeserver-app-orchestration-v370-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"]=data_dir

    from app.database import db, initialize_database
    from app.services import approvals, homeserver_app_prebuilt, homeserver_apps, local_automation

    initialize_database()
    installed=homeserver_app_prebuilt.install("vp3.media-player")
    app=homeserver_apps.get("vp3.media-player")
    assert app["lifecycle_state"]=="running"

    suggest=local_automation.upsert_routine(
        "player-health-suggestion",
        "Player Health Suggestion",
        approval_mode="suggest_only",
        steps=[{
            "step_kind":"app_action",
            "app_key":"vp3.media-player",
            "action_key":"player.status",
            "arguments":{},
        }],
    )
    assert suggest["steps"][0]["step_kind"]=="app_action"
    assert suggest["steps"][0]["app_key"]=="vp3.media-player"
    assert suggest["steps"][0]["action_key"]=="player.status"
    assert suggest["steps"][0]["risk"]=="read"

    suggested=local_automation.run_routine("player-health-suggestion",source_kind="section23:test")
    assert suggested["status"]=="suggested"
    assert len(suggested["suggestion_ids"])==1
    assert suggested["request_ids"]==[]

    pending=local_automation.list_app_suggestions("suggested",20)
    assert len(pending)==1
    assert pending[0]["app_key"]=="vp3.media-player"
    assert pending[0]["action_key"]=="player.status"

    accepted=local_automation.decide_app_suggestion(int(pending[0]["id"]),"accept")
    assert accepted["decision"]=="accept"
    assert accepted["executed"] is False
    assert accepted["owner_approval_required"] is True
    assert accepted["request_id"]

    requests=approvals.list_requests("pending",50)
    app_requests=[row for row in requests if row["id"]==accepted["request_id"]]
    assert len(app_requests)==1
    assert app_requests[0]["action_key"]=="apps.invoke"
    assert app_requests[0]["source_app_key"]=="automation:suggestion"

    governed=local_automation.upsert_routine(
        "player-health-request",
        "Player Health Request",
        approval_mode="ask_every_time",
        steps=[{
            "app_key":"vp3.media-player",
            "action":"player.status",
            "arguments":{},
        }],
    )
    manual=local_automation.run_routine("player-health-request",source_kind="section23:manual")
    assert manual["status"]=="requested"
    assert manual["approval_required"] is True
    assert len(manual["request_ids"])==1

    rule=local_automation.upsert_rule(
        "when-player-event",
        "When Player Event",
        routine_key="player-health-suggestion",
        trigger_kind="app_event",
        trigger={"app_key":"vp3.media-player","event_type":"player.test.event"},
        conditions=[{
            "kind":"app_state",
            "app_key":"vp3.media-player",
            "field":"lifecycle_state",
            "operator":"eq",
            "value":"running",
        }],
        cooldown_seconds=0,
    )
    assert rule["trigger_kind"]=="app_event"
    assert rule["trigger"]["app_key"]=="vp3.media-player"
    assert rule["last_event_id"] in (None,0)

    # Historical events present before rule creation must not replay.
    with db() as connection:
        app_id=connection.execute(
            "SELECT app_id FROM homeserver_apps WHERE app_key='vp3.media-player'"
        ).fetchone()["app_id"]
        connection.execute(
            """INSERT INTO homeserver_app_events(
                 app_id,event_type,actor_type,actor_key,metadata_json
               ) VALUES (?,?,?,?,?)""",
            (app_id,"player.test.event","system","pre-rule",'{"secret":"historical"}'),
        )
    local_automation.upsert_rule(
        "when-player-event-2",
        "When Player Event 2",
        routine_key="player-health-suggestion",
        trigger_kind="app_event",
        trigger={"app_key":"vp3.media-player","event_type":"player.test.event"},
        conditions=[],
        cooldown_seconds=0,
    )
    stale=local_automation.evaluate_rule("when-player-event-2")
    assert stale["fired"] is False
    assert stale["reason"]=="trigger_not_met"

    with db() as connection:
        connection.execute(
            """INSERT INTO homeserver_app_events(
                 app_id,event_type,actor_type,actor_key,metadata_json
               ) VALUES (?,?,?,?,?)""",
            (app_id,"player.test.event","app","vp3.media-player",'{"filesystem_path":"/private/example","token":"do-not-expose"}'),
        )
    fired=local_automation.evaluate_rule("when-player-event")
    assert fired["fired"] is True
    assert fired["status"]=="suggested"
    snapshot=local_automation.list_executions(20)[0]["trigger_snapshot"]
    assert snapshot["app_event"]["app_key"]=="vp3.media-player"
    assert snapshot["app_event"]["event_type"]=="player.test.event"
    assert snapshot["app_event"]["metadata_exposed"] is False
    assert "filesystem_path" not in str(snapshot)
    assert "do-not-expose" not in str(snapshot)

    brain=local_automation.brain_context(20)
    assert brain["contract"]=="vp3.homeserver.app-orchestration.brain-context.v1"
    assert brain["governance"]["homeserver_execution_authority"] is True
    assert brain["governance"]["app_actions_use_universal_control"] is True
    assert brain["governance"]["app_actions_use_owner_approval_ledger"] is True
    assert brain["governance"]["suggest_only_never_executes"] is True
    assert "arguments" not in str(brain["pending_app_suggestions"])
    assert "filesystem_path" not in str(brain)

    cap=local_automation.public_capability()
    assert cap["app_action_steps"] is True
    assert cap["app_event_triggers"] is True
    assert cap["app_state_conditions"] is True
    assert cap["universal_app_control_contract"]=="vp3.app.agent-control.v3"

print("HomeServer Section 23 App Automation & Orchestration: PASS")
