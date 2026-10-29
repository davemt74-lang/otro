# TV Watching v1.348

Integrated directly with Agent Eyes, Brain and Chat. Disabled by default. Run Cloud `upgrade.php` after deploying this version. HomeServer needs the matching Cloud release and its existing authenticated HTTPS pairing.

## Controls and recognition

Open Agent Eyes → TV Watching and history. Enable TV Watching, explicitly allow cropped-screen sharing, and save. Saving opens no camera. Start the existing Eyes camera when ready. There is no second camera or microphone owner.

The optional Human object detector supplies TV boxes. Up to four screens are tracked; faces within detected or manually selected screen bounds are excluded from participant matching and enrollment. If automatic detection fails, the participant camera remains available and Select TV area offers a manual rectangle. This depends on the screen bounds and does not remove TV dialogue from room audio.

Routine recognition attempts at most once a minute across the screens. It sends two JPEG crops about 850 ms apart, at most 960 × 720 pixels each, through the existing configured OpenAI/Claude account or system inference route. Existing quotas apply. Failed attempts back off; the server counts failures toward 60 attempts/hour and allows one active request per account. Identify TV now requests an earlier sample while retaining the server budget.

The actual multimodal provider request extracts program clues. The existing public web-search service searches those text clues. Agreement with a returned page title supports a **likely** title, never scene verification or proof of personal viewing. Unsupported guesses remain **unknown**. This version has no reverse-image matching service, audio fingerprinting, perspective correction or episode database. Episode text can be supplied by visible/model evidence or an owner correction; exact episode recognition is not guaranteed.

Typed watch-TV commands reuse these controls after sharing consent. Spoken start commands require a current learned-voice match; controls remain available when uncertain. Stop TV Watching remains available without a voice match. Voice matches grant no account authority.

## Timestamped history and Brain

Cloud is the canonical, account-isolated history store. HomeServer proxies the same records through its current pairing, Agent Chat and memory-read permissions, Cloud access and memory scope. No offline cache or duplicate local viewing-profile database is created. History reads require a working pairing, but no active camera.

Sessions store UTC first/last observation times, title status, optional episode, sources, sample count and estimated observed seconds. Timestamps mean when the server received the crops. Duration joins samples no more than 90 seconds apart and does not establish continuous viewing or completion. Stop, lost screens, content changes and observation gaps close sessions at the last observed sample with an estimated-end label.

Confirm/correct the title and separately choose “I personally watched this session.” A detected screen does not prove the owner watched. Patterns use confirmed personal sessions within the most recent 50 observations and are labelled patterns, not preferences. Explicit preferences are separately entered and independently removable. The TV service does not infer sensitive personal traits.

Cloud Chat reads current canonical history with Eyes off and supplies it to Brain for relevant TV/recommendation turns. Relative dates use the saved IANA timezone. Last night is explicitly labelled 6 pm–6 am. HomeServer owner Chat exposes `tv.history` and `tv.interests` through the `tv.watching` skill and existing model tool registry. Structured title and UTC time filters query complete history; each call returns at most 50 records.

## Lifecycle and data handling

Capture requires separate opt-in to send crops to the configured model provider under its data policy. Public text-clue queries go to the existing search service. VP3 does not persist raw TV snapshots in history, Brain or browser storage. Textual extracted clues and source links can be saved with an observation. This is not fully local recognition. The optional object model is downloaded by the existing browser runtime, not bundled into the installer.

Recognition and history commit are separate phases. Stop, disabling, page hiding/leaving, screen loss, account changes, old permission epochs, expired runs, stale revisions and deleted sessions fence late results. A late Enable cannot undo a newer Disable. Retries cannot inflate viewing time. Settings changes invalidate prior capture runs.

Deletion tombstones canonical session content. Subsequent history and Brain reads exclude it, and patterns are recomputed without a separate interest cache. Explicit preferences remain until separately forgotten. Recognition receipts can retain their textual candidate for at most the proposal cleanup window (one day, removed on the next analysis); they are never Brain/history records. Existing chat transcripts are not rewritten when history is deleted. Deletion cannot be undone by a stale edit or retry.

## Acceptance evidence and limits

TV server tests use production PHP with SQLite. The Chromium suite sends actual cropped JPEGs to that service and validates crop pixels, camera-off history, corrections, deletion, cancellation, account separation, manual fallback and voice gates. Vision and web responses are controlled fixtures. Provider payload tests exercise actual multimodal request builders and unchanged ordinary text Chat. HomeServer tests use its real pairing DB, owner tool registry and API with mocked external transport.

These checks validate integration and lifecycle behavior, not real-world title/episode accuracy. No live paid-provider room benchmark has been run. A room trial should compare known programs, menus, ads, multiple screens, glare, oblique angles and small screens against independently recorded answers. Keep likely/unknown labels until stronger evidence is available.
