# v0.48 validation checklist

- Dedicated v0.48 regression passes on Windows and Ubuntu.
- First-turn delegation binds to the canonical conversation.
- Worker context is Agent-specific and parent memory does not leak.
- Paired-wrapper Agent grants are enforced at queue and execution time.
- Permission snapshots can only shrink at execution time.
- Nested delegation is unavailable to worker inference.
- Secondary Agent deletion leaves historical workflow rows readable and non-runnable when detached.
- v0.25 stateless delegation, v0.47 routing, v0.46 personas, and canonical context regressions remain green.
- Packaged Windows restart preserves v0.48 policy and queued workflow state.
- Full HomeServer EXE, installer, private-data upgrade, recovery, distribution, and artifact checks pass before merge.
