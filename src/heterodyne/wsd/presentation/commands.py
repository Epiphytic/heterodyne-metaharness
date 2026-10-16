"""Finite supported command spellings shared by parsing and literal screening."""

READ_COMMANDS = ("/workstreams", "/status", "/approvals", "/queue")
MUTATIONS = frozenset(("/approve", "/deny", "/pause", "/resume", "/stop", "/harness", "/trust-group"))
