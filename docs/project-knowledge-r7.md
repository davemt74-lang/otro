# R7 — Reviewed Knowledge Refresh

Cloud, HomeServer and Chrome expose **Review Knowledge freshness** on the existing Agent Chat canvas. Open a saved conversation and choose its existing Conversation Knowledge scope first. Review ages use source evidence dates and a configurable 1–365 day reminder interval; age is not proof of correctness or current availability.

## Deliberate review flow

1. Check source ages within the current project/folder scope. No browser read or model turn runs here.
2. Choose an existing approved Chrome research reader, Research project and personal Knowledge folder. Project/folder destinations must match the selected context.
3. Prepare and review an exact plan of up to five safe public HTTPS source URLs. Old private notes, quotes and draft text do not become external search queries. Existing server website grants and Chrome origin permission remain separate.
4. Confirm the exact reread. Existing R1 research requests, expiring Chrome leases and guarded receipt-backed browser reads perform the work. Use the existing Chrome research reader controls to run or resume it.
5. Check progress and compare the original evidence with newly observed findings. Observed, inferred and conflicting claims retain pinned source/version/date citations. Different observations do not prove a real-world change.
6. Review the exact proposed Knowledge content, project and folder, then explicitly apply. A private Research draft and a separate canonical personal Knowledge note are saved in one transaction. Original highlights and prior report history remain available.
7. Check progress to view pending or received HomeServer revision receipts. An independently edited note becomes superseded; an old receipt never confirms the new revision.

## Authorities and storage

`api/project-knowledge-r7.php` handles list, prepare, get, start, apply and cancel. The native `/api/v1/control/workspace-sync/project-refresh` route forwards bounded pointer-only requests under current pairing and conversation authority. Chrome uses the `project_knowledge_r7` command with an explicit payload allowlist. Apply additionally requires `knowledge.write` in Chrome and current personal Knowledge management permission in Cloud.

The `chat_knowledge_refresh_r7` table stores workflow pointers, source/scope/target revisions, destination IDs and applied receipt hashes. Captures, Research, source versions and canonical Knowledge remain the evidence authorities. Stable `knowledge-refresh-r7:<kind>:<id>` keys identify the separate refresh note; exact retries do not create a second note or report. Independent note edits require a new preparation.

Current source bytes and URL plans, conversation scope, project/folder access, runtime/site grants and Knowledge revision checks fence stale plans. The R7 guard is called by the existing reader authority check before browser capture and analysis. Preparation and application lock the owner and relevant canonical rows, recheck authority before committing, and roll back all partial work on failure. Cancel can stop a stale unapplied intent without rereading evidence.

Native Knowledge stays in existing read-only Cloud workspace replicas. Native Chat retrieves the same canonical note; it does not manufacture another local Knowledge item. Current account/session-bound Knowledge snapshots supply hash-only `knowledge_refresh_receipts_r7` records in the existing outbound Chat dataset. Receipt output requires current Chat and Knowledge permissions. Brain drawers and the Chat review panel project those receipts without changing the typed draft or sending a turn.

## Deployment

Deploy the matching Cloud package and run `/upgrade.php` to create the R7 workflow-pointer table. Native schema remains 80; no parallel native migration is required. Install the matched HomeServer Windows package. Chrome 22.17.0 adds the shared review panel while preserving the extension identity key. Connection recovery remains outside this release.

## Acceptance gates

The R7 suites cover real InnoDB prepare/apply rollback and retry identity, exact cited previews and canonical note bodies, preserved original captures, pending/received/superseded receipts, deterministic date boundaries, real SQLite replicas and native Chat context, paired request/response authority and privacy fences, the actual Chrome message dispatcher, and three actual Chromium mobile canvases. Existing R1–R6 acceptance and Windows packaging, restart/recovery, silent upgrade and private-data preservation remain required. Test fixtures control identity grants, model outputs and page content; they do not certify the health of a user's installed environment.
