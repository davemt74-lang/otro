# CI-G1: Staged CI without weakening release acceptance

## Policy
- Draft PRs: run focused component tests immediately, including member consent,
  Stonefellow 84B7 music, and other path-filtered feature suites.
- Ready-for-review PRs: run full HomeServer PR Core, v2.4 Linux/Windows
  release acceptance, v2.4 data continuity, Apps V1 and native workspace
  certification. Each subsequent commit on a non-draft PR re-runs these.
- `main`: HomeServer CI with installer/Windows packaging, full v2.4
  release acceptance and data continuity still run after every merge.
- Legacy v2.3 release smoke: only runs on its affected runtime and installer
  files; `workflow_dispatch` remains available for explicit audit.
- Agent A5C mission suite: its broad `app/**` PR match is narrowed to mission
  and browser runtime source. That suite remains active when those files change.

## Release discipline
Do not merge a draft. Only mark the PR ready when a candidate is reviewed;
then require the exact-head checks to be green before merging. An intentionally
skipped draft job is NOT evidence of certified completion. Never interrupt
existing `main` release certification or restart long suites merely to
obtain a duplicate result.

## Proof
`python3 tests/ci_g1_release_policy.py` checks all five draft gates,
ready-for-review events, exact-path target coverage, untouched full main
CI and preserved Stonefellow Linux/Windows security tests.

## Further optimizations
Measure results before changing more workflows. Dependency caching and
checksum-based artifact reuse are candidates, but must not reuse test results
from a different source tree or skip Windows recovery/installer certification.
