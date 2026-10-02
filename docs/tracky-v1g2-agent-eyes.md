# Tracky 1G2 — Agent Eyes supervised live observations

**Production ownership:** VP3 HomeServer, not the standalone Tracky development script. The existing `tracky_native_managed_session` worker remains the only supervised native-camera loop. Agent Eyes uses it with a distinct `owner_surface`, while both modes use the canonical Tracky provider and existing exclusive native camera driver lock.

**Default:** off. Owner must first perform the installed HomeServer camera test, local privacy check and owner acceptance. Agent Eyes also requires fresh consent and an owner-selected local camera per session. It samples locally 1–12 times at minimum five-second intervals, has a two-minute absolute limit and a 15-second owner-presence heartbeat. No unattended startup or automatic resumption. Closing the Tracky view or hiding its initiating tab requests immediate stop; the server lease expires independently if that request cannot arrive.

**Privacy and safety:** face-region detector is not recognition. No recordings or templates persist; synthetic tests are not installed-device hardware certification. All observations pass through the existing `tracky.active_perception` ledger; Agent Chat sees passive operational status and counts only. HomeServer never advertises this temporary local owner-bound provider as remotely available to Cloud. It does not give Cloud control of camera capture.

**Agent integration:** existing Agent Chat onboarding summary includes `agent_eyes` with current operation, last observation time, presence gate, provenance through canonical request ledger and next owner action. Read-only status cannot issue a capture or extend a lease. Agent Brain's broader contextual recognition, multi-camera fusion and durable policy for unattended perception are explicitly deferred to governed 1G3, not impersonated by this section.

**Test gates:** owner authentication/CSRF, strict start validation, privacy rejection, installed-review binding, exclusive provider, shared native driver, canonical observation ledger, passive Agent status, non-biometric output, UI visible-tab owner lease and no cross-session stop. Hardware certification must run on the installed Windows HomeServer after release.

## 1G2B1: supervised resource budgets and watchdog

The original installed-device two-minute/12-observation/15-second visible-owner lease stays in force. An explicit local owner can choose a 60- or 120-second limit and a 4/8/12 CPU-second budget. CPU accounting uses the observation worker's thread CPU time; it is not system-wide process accounting. Status and the owner dashboard expose current session budget usage. The independent watchdog checks owner lease, privacy, current-model acceptance, elapsed time, CPU usage and stalled observation attempts; it only revokes, never launches or resumes capture. A stalled uninterruptible OS camera call can remain occupied, so the shared camera capture lock still blocks a fresh session until the driver returns.

This section is *not* longer-running or unattended production certification. Extended sessions beyond two minutes, background operation, recording and identification remain disabled until physical installed-device acceptance and a distinct owner-approved policy. Focused synthetic automated tests are not physical proof.
