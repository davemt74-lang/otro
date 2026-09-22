# VP3 OS v1.3 — Hardware Experience Runtime

VP3 OS v1.3 makes the shared VP3 OS runtime behave like a purpose-built product across VP3 Node, Desk, Studio, Team Node, Pocket, and custom hardware without splitting the platform into separate product codebases.

## Architecture

VP3 OS core remains authoritative for Agent, Meeting, Ambient, Automation, Updates, Fleet, privacy, and approval boundaries.

Hardware Experience Runtime sits above the normalized hardware adapter and below the owner-facing device experience:

VP3 OS core -> Hardware Experience Runtime -> product profile -> visual/display/control experience

The runtime does not create a second physical action engine and does not execute room-device commands.

## Product profiles

VP3 Node uses the personal_voice experience with push-to-talk voice, status language, privacy, and ambient presence.

VP3 Desk uses the desk_companion experience with voice, display cards, meeting controls, status language, privacy, and presence-aware display wake behavior.

VP3 Studio uses the creator_console experience with creator audio, meeting/recording state, optional display support, and optional control dial.

VP3 Team Node uses the shared_room experience with meeting controls, shared notifications, room presence, status language, and optional display/audio.

VP3 Pocket uses the portable_companion experience with voice, portable power, display cards, privacy, presence awareness, and optional control dial.

Custom hardware uses the generic experience and never claims hardware that has not been reported by an adapter.

## Unified visual state

The runtime projects one bounded visual state from existing services. Priority is privacy, software update, physical meeting, Physical Agent voice turn, hardware degradation, ambient/presence, then idle.

Visual states map onto the existing allowlisted hardware status-light modes: off, idle, listening, thinking, speaking, privacy, error, and updating.

Existing Agent and Meeting runtimes remain authoritative for their work. Hardware Experience only reconciles the product-facing visual projection.

## Physical event bus

The existing hardware adapter remains the normalized physical event source. v1.3 subscribes to that local event bus and writes a bounded metadata-only experience ledger.

Supported experience inputs include agent button, privacy switch, presence sensor, wake word, voice activity, and optional control dial.

Control dial clockwise/counterclockwise events on supported profiles adjust the local persisted experience volume preference in 5 percent steps. No room-device, automation, or cloud action is created.

## Physical control policy

Agent button behavior is now policy-backed. The default remains push_to_talk with cancel/legacy-meeting hold behavior so existing hardware remains backward compatible.

The owner can disable the Agent button or change hold behavior to meeting_toggle, privacy_hint, or none. Physical Agent and Physical Meeting read the same Hardware Experience policy instead of maintaining conflicting button maps.

Meeting toggle still calls the existing Physical Meeting Runtime, which retains its microphone/privacy/readiness checks.

## Display cards

v1.3 provides a small local card surface for agent, meeting, notification, room_mode, timer, system, and update states.

Cards contain bounded presentation metadata only and are filtered by the active product profile. Products without display modes expose no active display cards.

Display cards do not contain raw conversations, recordings, Memory content, Knowledge documents, credentials, or arbitrary file paths.

## Preferences

Persistent local preferences include experience enabled state, brightness, volume, LED intensity, screen timeout, wake behavior, Agent button action, hold action, display detail, and quiet visuals.

Brightness, volume, and LED values are device-experience preferences. A hardware controller may consume them only through an explicitly supported local adapter capability; unsupported hardware simply retains the preference without pretending it was applied.

## Degraded behavior

The runtime derives missing/not-ready required hardware from the product profile and reports voice/display/status-light availability separately.

Privacy remains authoritative. A physically engaged privacy switch suppresses voice availability and takes precedence over other visual states.

Optional hardware may degrade a supported experience without taking down HomeServer. Required hardware failure causes product-experience certification to fail.

## Experience certification

Each product profile has a release-time certification journey. Certification checks every required component, observed optional components, physical privacy behavior, visual-state projection, and Hardware Experience settings availability.

Required failures produce failed certification. Observed-but-not-ready optional hardware produces degraded certification. A complete required profile passes.

## Fleet integration

v1.3 extends bounded fleet telemetry with hardware_experience_version and experience_profile so a fleet can distinguish the product experience running on each appliance.

These fields add no private content and do not grant fleet control over physical hardware.

## Release gates

v1.3 must pass schema 29 upgrade/idempotence, v1.0-v1.2 historical regressions, all five product-profile certifications, owner API tests, display-card filtering, physical event/control-dial tests, fleet experience metadata compatibility, JavaScript syntax, backup/security/system reliability, Windows installer contract, broad HomeServer packaging, post-merge Ubuntu/Windows validation, post-merge HomeServer CI, and merged-main artifact verification.

No next VP3 OS phase begins until the exact v1.3 PR head is green, merged, the exact merged main commit is green, and the final deploy package is verified.
