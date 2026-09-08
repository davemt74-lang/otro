from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


with tempfile.TemporaryDirectory(prefix="homeserver-collaboration-eligibility-v030-") as data_dir:
    os.environ["HOMESERVER_DATA_DIR"] = data_dir

    from app.database import db, initialize_database  # noqa: E402
    from app.services import app_collaboration  # noqa: E402

    initialize_database()

    with db() as connection:
        consumer_id = int(
            connection.execute(
                "INSERT INTO paired_apps(app_key, name, token_hash) VALUES ('consumer', 'Consumer', 'consumer-token')"
            ).lastrowid
        )
        connection.execute(
            "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'memory.read', 1)",
            (consumer_id,),
        )

        source_ids: list[int] = []
        for index in range(1, 7):
            source_id = int(
                connection.execute(
                    "INSERT INTO paired_apps(app_key, name, token_hash) VALUES (?, ?, ?)",
                    (f"source-{index}", f"Source {index}", f"source-token-{index}"),
                ).lastrowid
            )
            source_ids.append(source_id)
            if index >= 5:
                connection.execute(
                    "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'memory.read', 1)",
                    (source_id,),
                )
            connection.execute(
                """
                INSERT INTO app_collaboration_grants(
                    consumer_app_id, source_app_id, memory_allowed, knowledge_allowed, enabled
                ) VALUES (?, ?, 1, 0, 1)
                """,
                (consumer_id, source_id),
            )

    # Four earlier but ineligible grants must not crowd later eligible sources
    # out of the four-source collaboration ceiling.
    eligible = app_collaboration.eligible_grants(
        "app:consumer",
        {"memory.read"},
        allow_memory=True,
        allow_knowledge=False,
    )
    assert [item["source_app_key"] for item in eligible] == ["source-5", "source-6"]

    # Once every source becomes eligible, the ceiling applies to the eligible
    # result set deterministically rather than to raw grant rows.
    with db() as connection:
        for source_id in source_ids[:4]:
            connection.execute(
                "INSERT INTO app_permissions(paired_app_id, permission, allowed) VALUES (?, 'memory.read', 1)",
                (source_id,),
            )

    capped = app_collaboration.eligible_grants(
        "app:consumer",
        {"memory.read"},
        allow_memory=True,
        allow_knowledge=False,
    )
    assert len(capped) == app_collaboration.MAX_COLLABORATION_SOURCES
    assert [item["source_app_key"] for item in capped] == ["source-1", "source-2", "source-3", "source-4"]

print("HomeServer v0.30 collaboration eligibility cap regression passed")
