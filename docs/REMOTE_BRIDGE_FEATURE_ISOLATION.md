# HTTPS bridge feature failure isolation

The HTTPS bridge previously synchronized feature data before completing accepted
Cloud commands. A SQLite fault in the feature synchronization table could stop
the worker with `OperationalError: remote bridge worker crashed`. The SQLite
recovery loop then retained the accepted exchange and repeatedly retried that
feature failure without sending another heartbeat.

Accepted commands now complete before supplementary synchronization. Known
SQLite and feature transport failures produce a separate `feature_sync_error`
warning with bounded retries. The base connection remains active, command
receipts reach the next poll, and the warning clears after feature recovery.
Unavailable capability discovery continues to fail closed. Pairing, session
replacement checks, command authorization, and core database recovery retain
their existing behavior. Unexpected programming failures still fail visibly.

`tests/remote_bridge_feature_isolation.py` runs the real local API and bridge
worker against a local HTTP relay and a real SQLite database. It removes and
restores a feature table, then returns HTTP 503 for real pending feature data.
Both scenarios verify continuing heartbeats, authenticated ping responses,
receipt delivery, recovery, and unchanged pairing and session credentials.
The test runs in PR checks and in the Windows build workflow.

This reproduces a confirmed connection failure path. It does not establish the
specific SQLite fault on an installed machine without that machine's diagnostics.
