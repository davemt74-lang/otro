# Independent HomeServer component packages

Initial installation remains one complete `HomeServerSetup.exe`. It installs the
application, managed browser and media runtime, checking space first. Users do
not assemble multiple installers.

This phase adds a smaller core-only update for an existing compatible installation.
`HomeServer-Core-Update-Windows` contains the core installer, release metadata,
component catalog, measured core space report and checksums. The controlled
updater retains owner approval, pre-update backup, shutdown, health verification
and binary rollback. Both the updater and the installer check the exact required
browser/media hashes before replacing the application. Missing or changed runtime
files require the complete installer. Core updates preserve installed runtimes
and private data. The installer checks space again immediately before installation.

`COMPONENTS.json` uses `vp3.homeserver.components.v1` with Windows x64 platform,
versions, file sizes, SHA-256 inventories and content fingerprints. The core lists
the browser/media fingerprints it requires. Runtime identities remain stable when
only application code changes. Built-in Python services remain part of the compiled
executable and require a core rebuild. Existing independently packaged apps keep
using their current app update center.

Release packaging also produces `HomeServer-Browser-Runtime` and
`HomeServer-Media-Runtime`, each with a `vp3.homeserver.component-package.v1`
manifest and a payload directory. These are versioned distribution inputs; this
phase does not introduce an automatic runtime downloader or runtime installer.
A later small online installer can fetch just the components needed. Total first
installation disk use is not reduced by splitting the downloads.

Build sequence: measure the complete staged payload with
`scripts/installer_space_budget.py`, prepare the catalog/core requirements with
`scripts/build_component_packages.py`, compile the full installer and then compile
with `/DCORE_UPDATE=1`. After release hashes are written, use the builder's
`--assemble` option to verify certified files and produce component directories.
CI uploads those directories directly as separate ZIP artifacts.

Validation covers identity reuse, safe paths, package boundaries, tampering,
version compatibility and downgrade rejection. A focused Windows workflow compiles
both installers and checks empty/missing/corrupt dependency blocking, paths containing
spaces, application replacement, unchanged runtime hashes and private data. Full
Windows CI additionally exercises the real compiled core update and installed
application/browser startup. Certification is required before merging or release.
