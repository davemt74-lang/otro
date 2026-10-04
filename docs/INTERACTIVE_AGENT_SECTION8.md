# Interactive Agent review — Section 8: Agent Chat, Agent Brain and Knowledge integration

## Goal

Connect the listening, transcription, participant, meeting, transfer and retention work from Sections 2–7 to the canonical Agent context paths without creating a second memory system or a new permission bypass.

## Cloud architecture

Cloud Agent Chat already has server-authoritative participant context, reviewed Meeting Memory, Knowledge retrieval and the shared Agent surface-context contract. Section 8 adds server-resolved transcription state to that same surface contract.

The browser cannot author transcript state. The server resolves the current conversation-bound transcript from `artist_transcript_sessions_v172` and exposes only bounded state metadata: session ID, title, active/draft state, duration, segment count, Knowledge promotion state, last activity, source class and whether a HomeServer import is an independent Cloud copy. Raw transcript words are not placed into surface context.

Participant recognition remains a separate server-resolved source. Transcription state explicitly has no speaker-identity authority.

## HomeServer architecture

Owner Agent Chat now uses the existing canonical context assembler to retrieve two additional Knowledge-governed source classes:

- completed local transcription excerpts, with speaker identity explicitly marked unverified;
- finalized meeting intelligence cards, using reviewed summaries rather than raw meeting transcript payloads.

These sources are included only for local owner conversations when the existing Knowledge context setting is enabled. Paired applications cannot retrieve private local transcription sessions through this path; they continue to require the existing paired-app Knowledge scopes and explicit sharing/backup flows.

Interactive context participates in the canonical character budget, provenance ledger, retrieval history and Agent Brain source display. Transcript IDs may be string IDs; provenance preserves them as resource keys rather than coercing or dropping them.

## Acceptance criteria

1. Browser-supplied Cloud transcript state cannot become Agent context.
2. Cloud resolves only the signed-in user's conversation-bound active/draft transcript.
3. Cloud surface context never contains raw transcript text.
4. Discarded or unrelated Cloud transcripts are absent immediately.
5. HomeServer-imported Cloud transcripts are marked as independent copies and expose Knowledge-promotion state.
6. Transcript state cannot authenticate or verify a speaker; participant identity remains separately governed.
7. Completed HomeServer transcripts can support owner Agent Chat when Knowledge context is enabled.
8. Active HomeServer transcripts are excluded from retrieval.
9. Deleting a local transcript removes it from subsequent Agent context.
10. Finalized HomeServer meeting cards are retrievable as reviewed summaries without raw transcript payloads.
11. Paired apps cannot use the owner interactive-context path.
12. Interactive sources are bounded, provenance-bearing, visible in context history, and included in the existing canonical context budget.

## Validation

Cloud: `php tests/agent_context_section8.php` plus retained participant, meeting, transfer and transcription checks.

HomeServer: `python tests/interactive_agent_context_section8.py` plus retained canonical context, Knowledge, transcription, meeting, privacy and release checks.

A 10/10 Section 8 score requires focused tests, retained checks, green PR gates, both repositories merged, and release packages verified.

## Deliberate limits

Section 8 does not add shared-microphone recognition, overlapping-speaker diarization, continuous camera/voice fusion or new recording capabilities. Those remain Section 9. It also does not make unpromoted Cloud transcript text searchable Knowledge; promotion remains explicit. Independent Cloud/HomeServer copies still retain the deletion boundary established in Section 7.
