from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

service = (ROOT / "app/services/knowledge_collections.py").read_text(encoding="utf-8")
policy = (ROOT / "app/services/knowledge_collection_policy.py").read_text(encoding="utf-8")
api = (ROOT / "app/knowledge_collections_api.py").read_text(encoding="utf-8")
main = (ROOT / "app/main.py").read_text(encoding="utf-8")
remote = (ROOT / "app/services/knowledge_collections_remote.py").read_text(encoding="utf-8")
bridge = (ROOT / "app/bridge.py").read_text(encoding="utf-8")
schema = (ROOT / "database/knowledge_collections.sql").read_text(encoding="utf-8")
database = (ROOT / "app/database.py").read_text(encoding="utf-8")

required_service = [
    "search_for_app",
    "app_collection_scope",
    "set_app_collection_scope",
    "knowledge_collection_sources",
    "knowledge_collection_items",
    "app_knowledge_collection_scopes",
    "homeserver://knowledge/",
    '"absolute_paths_exposed": False',
    '"full_documents_returned": False',
    '"local_only_index": True',
]
for marker in required_service:
    assert marker in service, f"missing v0.37 service contract marker: {marker}"

# The paired-app search result must never expose the historical full item fields.
result_block = service[service.index("def search_for_app"):]
assert '"source_path"' not in result_block, "paired knowledge search must not expose source_path"
assert '"content"' not in result_block, "paired knowledge search must not return full document content"
assert "Path(" not in result_block, "paired knowledge search must not resolve local filesystem paths"
assert "read_bytes" not in result_block and "read_text" not in result_block, "collection search must query the local index, not raw files"

# Collection and legacy kind scopes are intersected by one policy layer. A
# collection cannot be deleted while any source/item/app assignment depends on
# it, preventing a scoped app from silently becoming unrestricted.
for marker in (
    "filter_items_for_app",
    "app_scopes.knowledge_kind_allowed",
    "knowledge_collections.search_for_app",
    "delete_collection_if_unused",
    "app_knowledge_collection_scopes",
):
    assert marker in policy, f"missing collection policy marker: {marker}"
assert "def scoped_knowledge_search" in api
assert "knowledge_collection_policy.scoped_search" in api
assert "knowledge_collection_policy.delete_collection_if_unused" in api
legacy_block = main[main.index('@app.get("/api/v1/knowledge")'):main.index('@app.get("/api/v1/memory")')]
assert "scoped_knowledge_search" in legacy_block
assert "list_knowledge" not in legacy_block

assert 'op != "knowledge.search"' in remote
assert '"/api/v1/knowledge/search-v037"' in remote
assert "return original(operation, payload, bearer_token)" in remote
assert "_knowledge_collections_v037_installed" in remote
assert "knowledge_collections_remote" in bridge
assert "install_knowledge_collection_remote_operations()" in bridge
assert '"knowledge.collections.v1"' in bridge
assert '"knowledge.citations.v1"' in bridge
assert '"knowledge.search.scoped.v037"' in bridge

for table in (
    "knowledge_collections",
    "knowledge_collection_sources",
    "knowledge_collection_items",
    "app_knowledge_collection_scopes",
):
    assert f"CREATE TABLE IF NOT EXISTS {table}" in schema
assert "FOREIGN KEY (source_id) REFERENCES knowledge_sources(id) ON DELETE CASCADE" in schema
assert "FOREIGN KEY (knowledge_item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE" in schema
assert "FOREIGN KEY (paired_app_id) REFERENCES paired_apps(id) ON DELETE CASCADE" in schema
assert "KNOWLEDGE_COLLECTIONS_SCHEMA_PATH" in database
assert "_ensure_schema_extensions()" in database

print("HomeServer knowledge collections v0.37 contract passed")
