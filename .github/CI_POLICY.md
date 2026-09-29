# CI Policy

This repository uses layered validation so feature work stays fast without weakening release gates.

## Hosting V2 pull-request checks

Ordinary HomeServer Hosting V2 section branches use the naming convention:

- `feature/hosting-v2*-sectionN`

For Sections 2–4 and 6–9, Hosting-only PRs use **HomeServer Hosting Section Gate** as the required engineering gate. It runs on both Ubuntu and Windows and:

- compiles the canonical Hosting runtime/control surfaces;
- validates `ui/system.js`;
- automatically discovers and executes every `tests/hosting_*.py` regression through `tests/run_hosting_suite.py`.

This means new Hosting section tests are picked up automatically without editing workflow YAML or restarting unrelated workflows.

## Full Hosting release checkpoints

The full HomeServer release matrix remains required for:

- Hosting V2 Section 1;
- Hosting V2 Section 5;
- Hosting V2 Section 10;
- any Hosting change intentionally made outside the ordinary `feature/hosting-v2*` section branch convention;
- pushes to `main`.

At those checkpoints, the broad HomeServer CI, v2.3 release acceptance, v2.4 data continuity, v2.4 release acceptance, and Hardware Experience matrices remain active.

Sections 5 and 10 are detected from the branch suffixes `-section5` and `-section10`.

## VP3 OS release workflow

The **current VP3 OS release workflow** remains authoritative for its owned release surface. The Hosting V2 acceleration layer does not demote, replace, or bypass that workflow when VP3 OS files are changed.

## Non-Hosting pull requests

Non-Hosting and mixed-surface pull requests retain their existing workflow behavior. The Hosting optimization must not be used to bypass validation for unrelated HomeServer, Agent, Tracky, federation, hardware, installer, or release surfaces.

## Concurrency

Automatic workflows use GitHub Actions concurrency with cancel-in-progress enabled where appropriate so a newer commit supersedes obsolete queued/running checks for the same pull request.

## VP3 OS phase discipline

No new VP3 OS phase starts until the current VP3 OS release workflow is green on its required exact head, merged, and validated according to the active release contract.

## Section discipline

No new Hosting section starts until the current section is:

1. fully implemented and audited;
2. scored 10/10 against its section contract;
3. green on the exact pull-request head;
4. merged.

At full release checkpoints, the complete release matrix must also be green before merge.

## Main branch

Pushes to `main` continue to run the existing broad safety and release workflows. The targeted Hosting gate is a pull-request acceleration layer, not a replacement for main-branch release validation.
