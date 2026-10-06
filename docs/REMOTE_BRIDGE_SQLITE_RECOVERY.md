# Remote bridge SQLite recovery

The bridge's outer worker guard stopped permanently after a SQLite
OperationalError and displayed only “OperationalError: remote bridge worker
crashed.” Attempting to record the disconnect could raise the same database
error again. The saved pairing remained intact while its heartbeat stopped.

SQLite OperationalErrors now retain the saved session and retry with an
interruptible 1–30 second exponential backoff. Status and bridge events report
SQLite's primary error code with owner guidance for busy/locked, read-only,
inaccessible, full, I/O and schema/query failures. No raw SQL, path or exception
text is published. Non-database programming errors still stop the worker.

Accepted HTTPS exchanges and unacknowledged result receipts remain in memory
across connection attempts under the same session token. A metadata database
failure before action dispatch resumes the accepted exchange rather than losing
its Cloud request. A database failure during action execution produces a 503
failure receipt; the action is not automatically replayed. Replacement sessions
clear old-session requests and receipts. This does not add durable receipt
recovery after a process exit.

The regression obtains real SQLite BUSY, READONLY, CANTOPEN, FULL and query-error
codes. It drives the actual worker retry/HTTPS loops with a synthetic relay,
checks exactly-once dispatch during pre-dispatch interruption, receipt retention
through a lost response, no automatic failed-action replay, bounded Stop-aware
backoff, credential preservation and session replacement.

The user's specific installed-database failure has not been identified from the
old generic notice. The update makes that cause visible and can recover once a
temporary lock/access interruption clears. A persistent schema, permission or
storage fault still requires repairing that underlying condition. No database
migration or Cloud change is introduced.
