# CI Policy

This repository uses a small automatic PR gate so feature and release work stays fast and obsolete jobs do not accumulate.

## Automatic pull-request checks

Only these workflows run automatically on pull requests:

- HomeServer CI — broad repository regression, Windows packaging, installer, upgrade, backup/recovery, and distribution coverage.
- The current VP3 OS release workflow — targeted cross-platform validation for the active VP3 OS phase.

Both automatic workflows use GitHub Actions concurrency with cancel-in-progress enabled so a newer commit supersedes older queued/running checks for the same pull request.

## Historical phase workflows

Historical phase-specific workflows remain available through workflow_dispatch for targeted manual regression testing. They do not run automatically on every pull request.

## Phase discipline

No new VP3 OS phase starts until the current phase is:

1. fully implemented and audited;
2. green on the exact pull-request head;
3. merged;
4. green on the exact merged main commit;
5. packaged with the final deploy artifact available.

Do not push speculative hardening commits while an older candidate is still being validated unless a concrete failure requires a fix. Consolidate code review and hardening before opening the release-candidate pull request whenever practical.

## Main branch

HomeServer CI runs after merges to main. The current VP3 OS release workflow runs on main only when files in its owned surface change.

When a new VP3 OS phase begins, promote its dedicated workflow to the current automatic release gate and demote the previous phase workflow to manual-only.
