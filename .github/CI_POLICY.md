# CI Policy

This repository uses a small automatic PR gate so feature work stays fast and obsolete jobs do not accumulate.

## Automatic pull-request checks

Only these workflows run automatically on pull requests:

- HomeServer CI — broad repository regression coverage.
- The current VP3 OS feature workflow — targeted cross-platform regression coverage for the active phase.

Both automatic workflows use GitHub Actions concurrency with cancel-in-progress enabled so a newer commit supersedes older queued/running checks for the same pull request.

## Historical phase workflows

Historical phase-specific workflows remain available through workflow_dispatch for targeted manual regression testing. They do not run automatically on every pull request.

## Main branch

HomeServer CI runs after merges to main. The current VP3 OS feature workflow runs on main only when files in its owned surface change.

When a new VP3 OS phase begins, promote its dedicated workflow to the current automatic feature gate and demote the previous phase workflow to manual-only.
