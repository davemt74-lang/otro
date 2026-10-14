# R9 — Conversational Knowledge Actions

Ordinary owner Agent Chat in Cloud, HomeServer and Chrome can draft a task, an existing CRM contact update, or an existing personal Knowledge document update from current authorized Knowledge. For example: **Use these highlights to create a follow-up task and update Supplier contact.** Each of up to three proposals appears alongside the assistant reply with editable fields and source citations. Choose **Review exact change**, inspect the immutable before/after fields, then separately confirm **Apply this exact change**. Every proposal has its own decision. Drafting and review preparation do not change tasks, contacts or documents.

R9 reuses the R8 source authorities, action table, canonical writers, transactional exact approvals and native workspace delivery receipts. No new Knowledge/task store or schema migration is introduced. Original captures, Research and refresh notes remain protected evidence. Native schema remains 80. Chrome advances to 22.19.0 with the same identity key and 25-file deployment whitelist.

## Scope and clarification

Only direct requests mentioning Knowledge, highlights, annotations, sources, evidence, notes, findings or research and a supported destination enter this bounded draft path. Questions, quoted examples, hypothetical discussions and negations retain ordinary Chat. Current conversation project/folder scope and explicit R6 source attachments remain canonical; the router does not switch scope. Requests referring to “this project” or “this folder” require that selected context. Existing contact/document destinations must have a unique exact name in the request; unnamed and duplicate-name destinations require clarification or selection through the existing manual review panel. Only requested and currently permitted operation types are presented to the draft model. The model sees at most 20 source candidates, returns one to six exact source indices per proposal, and cannot introduce IDs, tools or unsupported fields. No external browsing runs.

## Continuity and authority

The ordinary send pipelines persist the response and bounded draft pointers/fields in their existing Chat history. Each Chat turn has distinct draft identities. Reloading a reply revalidates current source receipts, scope, destination and permissions before rendering editable fields. A stable review UUID derived from conversation/draft identity restores an existing R8 review, including owner-edited fields and applied/dismissed state, rather than creating another task after reload. Changed evidence or destination blocks old drafts; fresh drafting is required. Editing fields never alters the typed composer or sends another Chat turn.

Cloud rechecks current account/lead/conversation authority around inference and before assistant persistence; Chrome additionally rechecks its current authenticated session and capabilities. Native uses the existing bounded paired proxy with current privacy, destination permissions and session replacement checks, then revalidates drafts before assistant persistence. Private, Knowledge-off and read-only owner turns do not invoke the conversational draft model or generic action tools. Ordinary questions keep the existing inference/tool path. Drafting uses the existing configured Cloud model and usage ledger; Cloud and native execution displays report the actual provider usage and Cloud token debit. Native mirrors the usage event without charging again.

## Acceptance gates

1. Direct action requests separated from discussion, examples, negation and ordinary Chat.
2. Current selected scope/source attachment authority and clarification for unselected project/folder context.
3. Exact unique named destination resolution and current permitted operation filtering.
4. Strict bounded draft-model output, source indices and field validation; no model tools or writes.
5. Independent editable multi-action drafts on Cloud, native and Chrome mobile canvases; safe text/citations and untouched composer.
6. Frozen full source receipt and destination revision checks during inference, restoration and review preparation.
7. Existing separate exact R8 review/apply/dismiss, canonical transactional writes and duplicate-free retry.
8. Existing Chat history, stable per-turn/draft review identity, restored edited reviews and current-authority clearing.
9. Native privacy/read-only isolation, pairing/session/destination revocation, no partial assistant persistence and truthful provider usage.
10. Retained R1–R8 regressions, immutable cross-system CI pins, Windows packaged browser/restart/recovery/installer/private-data upgrade certification, identical tested/merged source trees and verified deployment packages.

Controlled identity, model and external transport fixtures are explicit in acceptance. CI certification does not verify the owner’s live deployed accounts or hardware. Connection recovery remains on hold.
