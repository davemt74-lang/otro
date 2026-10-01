Tracky integration subset — sourced from davemt74-lang/Tracky main merge c641d9188b8b087ad09b7cd1fc9ae26b956f0fb5 (PR #59).
Only canonical Tracky participant storage, consent-gated enrollment policy, participant core, and pinned face model config are mirrored under the HomeServer UI origin. No separate Tracky webserver/install is required.
These browser assets are not a replacement for the authoritative OTRO perception provider, encrypted server biometric vault, or signed Cloud enrollment receipts. Client-reported completion remains unverified.
Tracky model-config.js retrieves pinned @vladmandic/human 3.3.6 code and model files over HTTPS when an owner explicitly starts capture; fully offline/SRI-verified model bundling requires a separate release gate.
