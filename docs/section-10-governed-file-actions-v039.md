# Section 10 — Governed Local File Actions (v0.39)

Section 10 extends the read-only Section 9 file boundary with approval-gated mutations of already tracked local files.

## Scope

v0.39 supports two write actions against existing opaque HomeServer file references:

- `files.update` — replace the UTF-8 contents of an existing tracked text file
- `files.delete` — delete an existing tracked file

Creating new arbitrary paths and moving/renaming files are intentionally deferred until HomeServer has a separate owner-defined writable destination capability.

## Safety contract

- paired apps require both `files.write` and `tools.execute`
- Agent file-write tools are proposal-only and always use the local HomeServer owner approval queue; there is no Agent automatic-execution path for file mutations
- paired apps cannot approve their own `files.update` or `files.delete` requests, even when they hold `approvals.review`
- no paired API or Agent tool accepts an absolute or relative filesystem path
- mutations accept only the opaque versioned file reference issued by Section 9
- collection scope, knowledge-kind scope, active-app status, permissions, tool-name scope, and action policy are rechecked when the owner approves the request
- the on-disk file must still match the indexed content hash before HomeServer mutates it; otherwise the request fails stale/changed
- source roots are resolved from HomeServer-owned configuration, never from caller input
- symlinks, paths outside the configured source root, internal HomeServer data paths, disabled sources, unsupported extensions, and missing files fail closed
- updates are bounded by the existing Knowledge upload/indexing size limit and are written atomically in the same directory
- successful mutations immediately reconcile the Knowledge index; update rollback restores both disk and index state when reconciliation fails
- tool/action audit metadata stores lengths and reference lengths, not file contents, opaque references, or local filesystem paths
- action requests remain owner-visible, expire under the existing approval lifecycle, and may be denied without side effects

## Execution policy

`files.update` and `files.delete` are approval-only write tools. Their inherited paired-app execution policy is `approval_required`; the only explicit alternate mode is `sensitive_high_impact`, which blocks paired execution. `safe_automatic` is not a valid policy for governed file mutations. Policy resolution also rejects stale or manually inserted automatic overrides by falling back to `approval_required`.

Local HomeServer owner control can still execute a file mutation directly because the owner is the trust authority. Paired wrappers and model/Agent paths cannot bypass owner approval.

## Compatibility

Section 9 `files.list` and `files.read` remain unchanged. Section 8 collection semantics remain authoritative, including direct item assignment overriding watched-source defaults. Schema migration 19 widens the existing action-request constraint to store `files.update` and `files.delete` proposals while preserving existing memory and task requests.