# Section 9 — Governed Local File Access (v0.38)

Section 9 extends the Section 8 local Knowledge collection boundary into a bounded read-only file capability for paired applications and Agent tools.

## Goals

- expose only files already tracked by owner-approved Knowledge Sources
- require a dedicated `files.read` paired-app permission
- inherit Section 8 collection scope so a wrapper cannot discover or read files outside its allowed collections
- keep existing `knowledge_kinds` restrictions as an additional intersection when a tracked file has an indexed Knowledge item
- return opaque HomeServer file references plus safe relative-path/source-label metadata
- never return or accept absolute filesystem paths through paired-app APIs
- never accept caller-supplied filesystem paths
- read indexed text from HomeServer's local Knowledge store rather than reopening arbitrary local files
- bound file discovery and content reads
- expose the same boundary through local HTTP, Remote Bridge operations, capability registry, and read-only Agent tools
- keep writes, moves, deletes, uploads, and arbitrary filesystem access out of v0.38

## Paired-app surface

- `GET /api/v1/files` — scoped file discovery
- `GET /api/v1/files/{file_ref}` — bounded indexed-text read by opaque reference
- Remote Bridge: `files.list`, `files.read`
- Agent tools: `files.list`, `files.read`

## Privacy and security contract

A file is visible only when all applicable checks pass:

1. caller is an active paired app;
2. caller has `files.read`;
3. the file is tracked by an owner-approved Knowledge Source;
4. the source/item resolves into a collection allowed for that app;
5. any applicable Knowledge kind scope also allows the indexed item;
6. the file has a current indexed Knowledge item for content reads.

Responses may include collection name/key, owner-defined source label, relative path, file size/status, content version, and bounded indexed text. They must not include source root paths, absolute paths, local filesystem URLs, credentials, or unrestricted document bodies.

## Compatibility

Section 9 is additive. Existing `knowledge.search`, watched-folder sync, citation-safe search, collection scope, Agent context, cross-wrapper collaboration, and Remote Bridge behavior remain unchanged.
