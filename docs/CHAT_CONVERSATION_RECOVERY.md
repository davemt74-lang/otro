# Native Chat conversation recovery

The normal Agent Chat canvas stays minimal. Stop appears for active text inference as well as microphone capture. Failed, stopped, restoring and offline turns show only a short dismissible recovery line. Ten bounded diagnostic status entries live in memory inside the existing right-side Brain drawer; transcripts and provider response bodies are excluded.

## Recovery contract

- Keep failed/interrupted messages as editable drafts. Never automatically replay a request or tool action. A stopped provider can still complete on the server: Check history reads the canonical conversation before an explicit resend.
- Tab session storage contains at most eight 32,000-character drafts, expires them after 24 hours, and separates existing conversations and new-chat Agent scopes. New unsent specialist drafts restore their selected Agent only after validating it against the owner’s available Agents. Canonical history remains in HomeServer’s existing database.
- A 30-day local selected-conversation hint restores only through the owner-authorized API, including its bound Agent. A newer conversation cannot silently replace the selected one. An unavailable initial history prevents sending until the user selects a conversation or chooses New chat.
- Conversation loads and Agent bindings have revision fences. An older binding response cannot change the current Agent. No send/tool route runs during initial restoration or while offline.
- Stop aborts inference, capture, transcription and output through their existing controllers. The composer unlocks immediately. Late responses are rejected; 120-second inference timeout retains the draft.
- Offline/reconnect never resends. Owner-session loss clears local recovery state, stops voice and clears the private canvas. Deletion clears the conversation’s draft.
- Listening follows recognition onstart, preserves unsent drafts, and pauses after three consecutive speech-service failures. A disconnected local microphone stops capture. Successful speech resumes after its owned reply.

## Validation

`tests/chat_workspace_browser.mjs` runs the complete owner UI against an isolated real HomeServer and controlled model/speech services. Recovery coverage includes provider failure, conversation-specific drafts, selected conversation/Agent across refresh and actual runtime restart using the same data directory, racing binding responses, unsent specialist selection across refresh, offline submit/reconnect, in-flight Stop, explicit history checks and owner-session expiry without replay. The existing nine capture-ownership behavioral cases cover pending permission, old recognition finals, cancellation, dictation transcription abort and stale leases. The complete UI acceptance also covers narrow/mobile layouts and a 500-message conversation.

These checks certify state and transport behavior. Real microphone permission, native speech services, configured provider credentials, speaker playback and echo conditions still require a smoke test on the installed machine. No database migration or duplicate conversation store is introduced.
