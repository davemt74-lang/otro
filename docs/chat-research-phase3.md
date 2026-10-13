# R2 — Question-to-research planning

Cloud and HomeServer Chat reuse the existing public-web search helper to propose an exact Chrome source plan from a question. On the Chat canvas, first review and approve the exact query exported to DuckDuckGo. Discovery is bounded to the first five alphabetically sorted websites already approved in the selected Chrome research runtime. The server checks the reviewed website scope and owner/conversation/lead before export and rechecks Chat/runtime/domain authority after transport. HomeServer keeps its existing private-conversation, pairing and app-scope checks before and after its Cloud proxy request.

Search-ranked links are candidates, not evidence or verified primary sources. The plan rejects unsafe, action, query-string, credential, private-address and unapproved website URLs; it keeps one result per approved website, at most five. Titles and candidate rationale appear as plain text. An unavailable or empty provider returns an actionable fallback: clarify the question, approve additional research websites in the existing delegation controls, or provide explicit HTTPS URLs. No source website is read before the exact returned plan is separately approved.

R1 remains canonical for source-plan review, Chrome website permissions, dedicated inactive tabs, exclusive expiring leases, exact URL guards, source receipts, corroboration/conflicts/gaps, interrupted-work review and confirmed draft saves. Lost discovery acknowledgements reuse the frozen plan; competing result rankings reuse the first stored plan without changing its review hash. Completed evidence gaps can populate a follow-up question, which requires a new explicit search and source-plan review. Clicking a gap never starts a search or reruns completed research.

The Chrome bundle is 22.12.0 with stable identity. Native schema remains 79; no new evidence ledger or search-history store is introduced. Connection recovery remains on hold.

## 10/10 gates

1. Canvas search consent exposes the exact external query and bounded approved website scope; cancelled or stale reviews cannot export.
2. Production search parsing and bounded HTTPS transport reject redirects, oversized responses and unsafe candidate targets; results remain unverified until Chrome reads them.
3. Owner, saved conversation, lead, private context, pairing and current website authority are enforced before/after transport; revoked results are hidden.
4. Frozen request identity survives lost responses and competing rankings; every Chrome read still follows R1 exact approval and receipt guards.
5. Cloud/native real browser acceptance covers separate approvals, safe candidate text, mobile, revoked-result clearing and non-executing gap follow-ups. Retained R1 worker, queue and paired mission suites pass.
6. All immutable candidate and post-merge CI pass; Windows executable/browser/installer and silent-upgrade private-data preservation pass; tested and merged trees are identical; deployment ZIPs and source/binary digests are verified.

The score applies to these bounded automated gates. Deterministic provider, inference, navigation and transport fixtures are identified in tests. A public provider smoke check reports actual availability separately; search-provider anti-abuse or network failures cannot be treated as certified search results. Owner live pairing, physical hardware, custom plugins and live model output remain outside this certification.
