# Minimal HomeServer Agent Chat

The owner Chat canvas shows the conversation and a compact composer. Existing Knowledge, research, work-mode and Agent Teams controls live in the existing + options dialog. Private Context contains Knowledge tools; Tools contains work mode, research and missions. Agent Brain remains a right-side drawer.

Modules are moved intact, preserving handlers, selected scope, typed fields and exact action reviews. A direct-child observer handles later module insertion without observing message content. Research consent and prepared mission/error reviews open the relevant options tab; ordinary routing leaves it closed. Source tools return prepared text to the composer without submitting it.

Native conversation voice keeps capture paused during automatic routing, hands direct answers to the canonical Chat turn, speaks a review status once, and resumes listening. Stop aborts the pending route and fences late results. No new inference route, permission system, database migration or capture implementation is introduced.

Acceptance uses the complete owner UI with an isolated HomeServer and controlled model/speech fixtures:

1. Only the conversation/composer occupy the default canvas.
2. Knowledge and mission controls are reachable through +.
3. Late module insertion preserves node identity and draft text.
4. Options preserve work-mode and preference state across close/reopen.
5. Keyboard focus, Escape and mobile dialog bounds remain valid.
6. Explicit research exposes its consent review and never sends ordinary Chat.
7. Interim speech fills the composer; delayed routing sends one Chat turn.
8. One reply is spoken and listening resumes; failed routing sends no Chat.
9. Stop prevents a late route from submitting and retains the draft.
10. Four viewport sizes and 500 messages preserve a readable newest message, centered sticky composer and zero horizontal overflow.

Existing automatic/specialist routing and research browser acceptance remain in use. Windows executable, recovery, installer and private-data-preserving upgrade certification are required before release. Fixtures do not certify a live microphone, installed voice hardware or paid provider account.
