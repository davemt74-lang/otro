# Connection lifecycle audit

Scope: VP3 Cloud, HomeServer and the existing Browser Companion; no duplicate bridge or browser runtime.

Initial checklist score: 5/10. Final score requires the listed regression gates, PR merge and package verification. Installed account/device acceptance remains a separate check.

| Criterion | Repair or retained behavior | Evidence |
| --- | --- | --- |
| Fresh Cloud authority | Poll revalidates token hash, device and active status under the account lock | PHP lifecycle on SQLite and concurrent MySQL |
| Atomic disconnect/removal | Account lock covers relay revocation and connection row changes; removal rereads the live connection | Existing HTTPS contract and lifecycle gate |
| Browser credential isolation | Old requests and disconnects cannot erase replacement tokens; requests bind the original site | Browser lifecycle behavioral test |
| Browser authorization | Exact Chrome callback origin/path and random state; duplicate flows blocked; disconnect cancels unfinished flow | Browser lifecycle behavioral test and native Chrome callback checks |
| Saved pairing recovery | Journal, lost-acknowledgement recovery and saved sessions retained | Existing pairing regression |
| Command execution | Consume each accepted command before dispatch; interrupted actions yield failure receipts without replay | HomeServer lifecycle and SQLite recovery |
| Worker recovery | Unexpected exceptions retry with interruptible bounded backoff | HomeServer lifecycle |
| Feature isolation | Optional synchronization exceptions cannot stop the heartbeat | Lifecycle and actual API/relay feature isolation |
| Running build identity | HomeServer compares actual executable SHA256, including same-version replacements | HomeServer lifecycle and Windows packaged takeover |
| Regression and packaging | Existing browser, bridge and release contracts, canonical deploy package, exact hashes | PR/main checks and release notes |

A network interruption during an action can leave its outcome unknown. The failure receipt asks the owner to verify it before retrying. The bridge deliberately does not repeat non-idempotent commands automatically.
