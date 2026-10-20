# KB5B — Private Food Monster Knowledge Sync and Revision Index

**Repositories:** original `davemt74-lang/gelato` KB5A manifest (merged PR #380),
and `davemt74-lang/otro` HomeServer. The current example deployment is
`https://gelato.vp3.me`, but links work with any validated HTTPS Food Monster
domain/subdomain and require individual restaurant-owner approval.

## Owner experience

Open **Agent Chat → + → Tools → Food Monster connections**. Each connected
restaurant has a **Sync knowledge** control. Sync scans only the approved menu
and/or recipe categories, then displays document counts, last complete sync
and an explicit **not live-verified** freshness label. The live KB4A
Agent Chat read remains authoritative for current production questions.

The owner-local API provides:

- `POST /api/v1/control/food-monster/connections/{id}/sync`: read and commit
  complete paginated manifests for each approved category. A network failure
  stops the attempt; partial pages are never used for deletion.
- `GET /api/v1/control/food-monster/connections/{id}/sync-status`: counts,
  last full source sweep, error and completion generation by category.
- `POST /api/v1/control/food-monster/connections/{id}/cache/search`:
  owner-only FTS lookup of the **unverified cached snapshot** with source
  installation ID, source record ID, revision and last successful sync.
  It never claims a cached result is current.

There is no new dashboard, Slack app, central cloud database or external
agent endpoint. The synchronization is **manual/on-demand in KB5B**; a
governed automatic scheduler can be added in a later phase after load and
recovery testing. The cache is not inserted into generic HomeServer Memory.

## Database and isolation

`database/food_monster_knowledge_cache.sql` adds two connection-keyed
tables and a **separate FTS5 table**, with source ID, category, revision,
normalized content and synchronization generations. The index is not
`knowledge_items` or `knowledge_chunks`, which are accessible to the
general HomeServer knowledge subsystem. Only the explicit owner-local
Food Monster API can read this cache.

The schema is included through the existing idempotent database extension
loader, not a numbered migration that might collide with other products.
No access token, pairing code or restaurant database password is stored
in these tables. Documents are stored in local HomeServer SQLite; at-rest
encryption of the general database is **not** newly supplied by this phase.

## Synchronization safety

- Authenticate the protected, independently approved credential and
  verify the installation's immutable source instance UUID and
  `knowledge.manifest` capability.
- Limit manifest requests to pages of 20, at most 2,000 documents per
  category and bounded source document sizes. Fail closed on duplicate
  source IDs, invalid SHA revisions, wrong category/source provenance,
  malformed pages, non-advancing cursors or source identity mismatch.
- Collect **every page** of a category before applying its changes in a
  single SQLite transaction. Upserts replace changed revisions, preserve
  stable local IDs, and update the FTS index with triggers.
- One complete scan missing a previously indexed record marks it
  **pending removal**. Only a *second completed* scan missing the same
  record destroys it. An interrupted/failed scan does not advance the
  missing counter, and cannot erase previously indexed records.
- This forward-ID source API is **not an atomic database snapshot**.
  Two complete observations reduce, but do not eliminate, race risk from
  concurrent source edits. A stable source snapshot-epoch protocol would
  strengthen that guarantee in a future phase.
- Reducing locally approved scopes deletes documents and sync-state rows
  from now-forbidden categories. Authentication failure from the source
  purges ALL documents for that connection and removes its credential.
- Disconnecting an entire restaurant destroys its indexed documents,
  FTS rows and synchronization state; other restaurants remain intact.
- When a source is unavailable, the prior snapshot may remain available
  **only** through the explicitly labeled cached search. It is never
  silently used as live source evidence.
- Previously generated Agent Chat messages containing sourced text are
  part of the conversation history and are not retroactively deleted by
  source revocation. Chat history management remains separate.

## Certification

Deterministic, isolated real-SQLite tests cover multi-page records,
independent restaurant boundaries, changed revisions, FTS update,
two-complete-sweep tombstones, incomplete scan recovery, bogus source
evidence, source-side credential revocation and cache purge, scope
reduction, and owner-local API routes. Existing KB3, KB4A and KB4C
contracts run in the dedicated KB5B workflow, followed by the full
HomeServer PR workflow gates. No public hosted installation was contacted
for live field certification.
