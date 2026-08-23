"""Shared Postgres connection config.

Local dev connects via a Unix socket with peer/trust auth
("dbname=protocol_drift_dev" -- no host/user/password needed). CI has no
such socket: it runs Postgres as a service container reachable only over
TCP with a password, so PROTOCOL_DRIFT_DSN overrides the default there.
Leave it unset locally.
"""

import os

DEFAULT_DSN = os.environ.get("PROTOCOL_DRIFT_DSN", "dbname=protocol_drift_dev")
