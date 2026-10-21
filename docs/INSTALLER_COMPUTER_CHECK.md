# Installer computer check

The normal Windows installer opens one computer-check page, then installs after one click. It keeps the existing application identity, installer-owned browser/FFmpeg, upgrade behavior and private data. Shortcut and sign-in startup choices stay on the check page. Silent installations keep their existing task selections and command-line behavior.

The compiled application is x64 and its included browser requires modern Windows; setup declares 64-bit-compatible Windows 10 or newer. This is a disk/compatibility check, not a scan of personal documents or installed third-party applications. It performs no network requests, deletes nothing and never launches the application to discover prerequisites.

## Build measurements

After staging HomeServer.exe, Chromium and FFmpeg, run `python scripts/installer_space_budget.py`. It counts the exact files included by the existing [Files] rules and reads the one-file application's CArchive uncompressed extraction sizes. It writes `installer/SpaceBudget.iss` and the downloadable `INSTALL_SPACE.json`. Missing/incomplete runtimes, symbolic payloads or unreadable archives fail the build. There is no fixed fallback size. Regenerate the include for every staged build.

Budgets deliberately reserve **additional free space** without credit for files that will be overwritten:

| Role | Budget |
| --- | --- |
| Installation | Staged payload + largest payload file + 64 MiB |
| Temporary setup/startup | Uncompressed one-file runtime + largest payload file + 64 MiB |
| Private data | 64 MiB startup headroom |

The largest-file allowances cover conservative staging/upgrade overhead; these are safe budgets, not claims of the exact peak Windows will consume. Existing downloaded ZIPs/setup files already reduce the reported free space and are not counted twice. Future media, AI models, backups and data migrations require additional capacity and are explicitly excluded. This change does not shrink the bundled installer or remove any features.

## Runtime checks

Each role resolves an existing parent and its local Windows volume GUID. This handles mounted folders and avoids treating drive aliases as independent capacity. Unresolvable or inaccessible volumes fail closed. Fixed local volumes are considered for an alternate application location; users can choose another folder themselves. Remote shares are not installation recommendations. Free capacity is not a promise of write permission; normal installer permission and file-lock checks remain authoritative.

Roles on one volume are summed; the lowest observed free-space value is used for that volume. Moving the application does not move Windows TEMP or private data. The check shows the relevant paths and deficits, offers another fixed drive only if **all** roles can fit there, and retains the default/previous application location until the user changes it. Existing app/browser/media files are reported as present; they are still replaced by the certified bundled versions, not silently trusted or skipped.

The page refreshes on request and on location changes. Both NextButtonClick and PrepareToInstall re-read the volumes, including silent installs. Insufficient or unknown capacity cannot be bypassed through the normal disk-space warning dialog. The user's private data directory is never moved or cleaned by setup. Changing application location on upgrade leaves old unrelated files in place; setup does not broadly delete the previous folder.

## Acceptance

The focused Windows workflow compiles both production Pascal and a separate synthetic CI installer. The harness's fake-space parameters and self-tests are enclosed in PREFLIGHT_TEST_BUILD and cannot enter a production build. Its application identity is separate and it has no autorun registration or application launch.

Tests execute native Pascal shared/separate-volume accounting, exact/one-byte boundaries beyond 4 GiB, unknown capacity, real Windows volume queries and read-only parent resolution. Native Windows UI tests check the first screen, disabled/enabled Install states, location/refresh controls, loss of free space after a successful scan, one-page installation into a selected folder and private-data preservation. Full release CI still builds the actual executable, measures its real dependencies and certifies fresh/silent upgrade installation and installed-browser startup before merge. The official installer deployment ZIP includes INSTALL_SPACE.json.
