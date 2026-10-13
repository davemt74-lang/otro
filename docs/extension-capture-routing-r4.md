# R4 — Extension annotations in Agent Chat and shared Knowledge

Highlight text on a web page, then choose **VP3 → Send highlighted annotation to Agent Chat**. The extension opens its existing Agent Chat view with a reviewed capture composer. Choose the existing conversation above it (or New chat), add a note and tags, and optionally select an existing Research project and Knowledge folder. Sending stores the capture and posts it to canonical Cloud Agent Chat. It does not invoke a model, run research, publish a social post, or grant new permissions. The existing Ask/Research/Team annotation interfaces remain available.

Existing Research projects are the project system currently exposed on Chat; this release does not introduce a separate Chat project database. Assignment keeps the canonical Research membership/visibility rules. The source capture is immutable in `browser_shares_v2010`; `browser_capture_deliveries_r4` stores only delivery references, tags and the exact-request identity. One InnoDB transaction commits the capture, source version, optional project association, Chat message and optional Knowledge item. Identical retries, including concurrent retries and a restarted Chrome service worker, reuse the same delivery. Changed payloads cannot reuse an existing request identity. Failed destinations roll back the whole capture.

Knowledge saves use the existing `personal_knowledge_store`, canonical user folders and indexing. Cloud remains authoritative. Existing account-bound workspace synchronization transports the Knowledge record, folder association and Chat capture references to HomeServer. The native Chat annotation picker reads those replicas, checks its current owner/lead, pairing, Chat grant, privacy settings and synchronization setting, and rechecks permission before inserting a capture into the composer. It creates no duplicate native Knowledge item or execution job. The existing native Brain context builder retrieves the synchronized Knowledge through its authorized workspace search.

Synchronization is asynchronous and must already be enabled for the same paired account. The native picker labels its last synchronization time. Pairing recovery remains on hold. This phase does not alter local-only Knowledge, publish personal Knowledge to other users, or certify an owner's live installation or physical hardware.

## Acceptance gates

1. Actual Cloud capture/source/project/Chat/Knowledge functions and additive schema under MySQL 8 (PHP 8.1/8.3); local MariaDB compatibility.
2. Folder/project/principal/device/capability fences, exact sequential and parallel retries, deleted-message fencing, destination-failure rollback and a final-authority recheck before commit.
3. Actual Chrome message listener, installation-subfolder authentication, stable request identity after service-worker restart and no new Chrome capture store.
4. Real mobile extension composer and native picker: safe text/links, correct destinations, no automatic model/send/save, exact failed-send retry and revoked-result clearing.
5. Actual Cloud export through the production native replica reader and Brain context builder; original Knowledge folder/tags preserved, no duplicate native rows/jobs, owner/pairing/privacy fences and retained workspace/Knowledge/research/Chrome regressions.
6. Exact candidate and merged-source CI, Windows packaged browser/runtime/installer and silent-upgrade private-data preservation, immutable tree provenance and verified deployment archives.

Identity grants, transport faults and revocation timing in local tests use controlled fixtures. Local tests exercise production database writes, source/project linkage, export/snapshot application, context retrieval and real browser rendering. The final automated acceptance score awaits candidate/main CI, Windows certification and merge.
