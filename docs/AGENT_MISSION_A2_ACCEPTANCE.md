# Section A2 — Supervised Mission Lifecycle & Recovery

**Scope:** Add a governed, read-only mission continuation system without replacing existing v0.48-v0.58 delegation workflows.

## Acceptance gates

1. **Correctness:** Running missions pause to a review state, interrupted work is not automatically replayed.
2. **Authorization:** Both paired-app and owner controls retain their existing gateway/authentication behavior; source isolation governs event access.
3. **Recovery:** Explicit resume may requeue only interrupted work; successful task output stays immutable.
4. **Retry limits:** Retry is allowed only for failed/interrupted tasks and at most three inference attempts per task.
5. **DAG safety:** Failed prerequisites block descendants; requeuing a prerequisite must release previously blocked descendants in dependency order.
6. **Concurrency:** Existing four-worker parallel ceiling and atomic task claims remain intact; A1 concurrency tests stay green.
7. **Idempotency:** Repeated creation keys return the same mission only for the same objective, parent and conversation; mismatches return HTTP 409.
8. **Observability:** Event inspection is chronological, cursor-based, bounded and source-isolated.
9. **Upgrade safety:** Additive routes/services, no destructive data migration or changes to legacy team execution.
10. **Regression/release:** A1 and A2 focused suites plus the complete PR workflow matrix green on the final head; merged-main SHA checked.

**A2 does not include** autonomous replanning, nested agents, browser or tool actions, cancellation of an already-in-flight provider request, or Cloud UI integration. Those require separate later sections and must not be claimed as complete.

## Verification policy

A score of 10/10 for an acceptance gate requires positive test or code-review evidence. Green CI alone does not imply the system is fully production certified. Do not merge this section unless every applicable gate above is satisfied and the final PR checks are green.
