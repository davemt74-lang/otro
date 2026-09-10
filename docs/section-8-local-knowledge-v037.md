# HomeServer Local Knowledge & Files v0.37

Section 8 extends the existing watched-folder knowledge engine without changing its scanner or copying original files into cloud services.

## Local-first contract

- A folder is indexed only after the HomeServer owner explicitly adds it as a watched source.
- Original files are read-only inputs. Removing a watched source never deletes or edits those files.
- The existing incremental scanner continues to use file metadata and SHA-256 content hashes to avoid unnecessary re-reading/re-chunking.
- HomeServer private data folders, symlinks that escape an approved root, unsupported file types, and excluded paths remain blocked.
- Collection assignment is metadata only and does not trigger a file rescan.

## Collections

Every indexed item resolves to one collection. Unassigned material resolves to the built-in `general` collection. Owners can create named collections and assign watched sources or individual knowledge items to them.

A paired app may be restricted to one or more collection keys. An empty collection scope preserves historical behavior and means the app may search any collection allowed by its existing `knowledge.search` permission and knowledge-kind scope.

## Citation-safe paired search

Remote `knowledge.search` is upgraded to the v0.37 endpoint. Results contain only bounded snippets and citation metadata; they do not include full document content or absolute local filesystem paths.

Each citation includes a stable item/chunk reference plus a content-version fingerprint. An unchanged scan preserves the citation identifier. If the source content changes, the knowledge item remains stable while the citation version changes, making stale references detectable.

Example citation URI:

`homeserver://knowledge/42?chunk=0&version=0123456789abcdef`

## Privacy boundary

Collection search operates against HomeServer's local SQLite/FTS index. It does not open original files, call a cloud provider, or transmit folders by itself. A paired wrapper receives only results explicitly returned through the authenticated `knowledge.search` operation and only from collections allowed for that app.
