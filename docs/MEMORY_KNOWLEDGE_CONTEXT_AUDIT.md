# Memory and Knowledge context audit — 17 October 2026

This section hardens existing retrieval and selection ownership. It preserves the minimal Chat canvas and uses the existing Brain drawer, scope settings, canonical source revisions and memory tables.

## Confirmed gaps fixed

- An older Knowledge options request could clear the newer conversation's scope, attachments and Brain sources after navigation, on success or error. An older Apply context request could also unlock or reset newer controls. Only the operation that owns the current generation may update or clear that UI.
- Send could read the previous/default context while an explicit Knowledge load or scope change was pending. Selection reads now wait for that operation; the canonical chat owner retains the editable draft on rejection. No automatic send or retry is added.
- HomeServer retrieval ignored stored memory expiry dates. Canonical and legacy memory selectors now exclude expired or malformed non-empty dates before ranking/limits, including shared memories. Null/blank expiry remains permanent. Records are retained for inspection; no schema migration or purge occurs.

## Acceptance evidence

The actual R6 browser fixture covers Cloud, native and extension canvases: late success, late failure, delayed scope apply, navigation without an event, explicit pending-operation gating, retained new attachments and composer text, current-access revocation, safe citations and Brain receipts. HomeServer also runs this fixture directly against its shipped UI script. Cloud/extension/native scripts are byte-identical.

The real SQLite memory fixture verifies selected-Agent versus foreign-Agent and shared memories, expired/invalid/future/permanent dates, current corrections/deletions and the canonical prompt. Existing context-engine, canonical runtime parity, memory continuity and retained Knowledge privacy tests remain gates. The existing R6 SQL/native tests verify selected project/folder IDs, changed-source revisions and revoked authority after inference, no opt-out query export, and rollback without partial history.

These checks reuse the existing server authorization and sync protocols. Default legacy Cloud-only Knowledge scope and canonical R6 conversation scope retain their established meanings. Historical messages and citations are not rewritten; native memory corrections/deletions affect subsequent retrievals. This section does not promise retroactive redaction of a prompt already sent to a provider.

Commands: `node tests/project-knowledge-r6-ui.mjs` (Cloud); `node tests/project_knowledge_context_ui.mjs`, `python tests/memory_context_freshness.py`, `python tests/context_engine.py`, `python tests/canonical_context_runtime_v430.py`, `python tests/project_knowledge_r6.py`, `python tests/knowledge_source_retention_privacy.py`, `python tests/agent_brain_memory_continuity_v245.py` (HomeServer). Complete Chat browser acceptance remains a release gate in both repositories.
