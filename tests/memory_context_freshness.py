"""Real SQLite retrieval: Agent isolation, expiry, corrections and deletion."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="memory-context-freshness-") as data:
    os.environ["HOMESERVER_DATA_DIR"] = data
    from app.database import db, initialize_database
    from app.services import brain, context_engine, canonical_context

    initialize_database()
    with db() as c:
        agent = int(c.execute("SELECT id FROM agents WHERE is_primary=1").fetchone()[0])
        other = int(c.execute("INSERT INTO agents(name,instructions) VALUES('Other','Other Agent')").lastrowid)
        for owner, key, expiry in [
            (agent, "permanent", None), (None, "shared", None),
            (agent, "blank", ""), (agent, "future", "2999-01-01T00:00:00+00:00"),
            (agent, "expired", "2000-01-01T00:00:00+00:00"),
            (None, "expired-shared", "2000-01-01 00:00:00"),
            (agent, "invalid", "not-a-date"), (other, "foreign", None),
        ]:
            c.execute("INSERT INTO agent_memory(agent_id,memory_key,content,importance,expires_at) VALUES(?,?,?,1,?)",
                      (owner, key, "supplier " + key, expiry))
    for select in [lambda: context_engine._memory_candidates(agent, "supplier"), lambda: brain._memory_context(agent, 20)]:
        assert {r["memory_key"] for r in select()} == {"permanent", "shared", "blank", "future"}
    with db() as c:
        c.execute("UPDATE agent_memory SET content='corrected supplier value' WHERE memory_key='permanent'")
        c.execute("DELETE FROM agent_memory WHERE memory_key='shared'")
    rows = context_engine._memory_candidates(agent, "supplier")
    assert "shared" not in {r["memory_key"] for r in rows}
    assert next(r for r in rows if r["memory_key"] == "permanent")["content"] == "corrected supplier value"
    # The canonical prompt builder uses the same current, scoped retrieval.
    scope = {"memory_key_prefixes": [], "knowledge_kinds": []}
    bundle = canonical_context._base_context(agent, "supplier", settings={"include_memory": True,
        "include_knowledge": False, "include_contacts": False, "max_context_chars": 12000},
        scope=scope, source_app_key="app:fixture", owner=False)
    prompt = context_engine.system_prompt({"name": "Agent"}, bundle)
    for forbidden in ["supplier expired", "supplier invalid", "supplier foreign", "supplier shared"]:
        assert forbidden not in prompt
    assert "corrected supplier value" in prompt
    print("PASS: canonical and legacy memory retrieval isolate Agents, exclude expired/invalid memories and read corrections/deletions without a stale cache")
