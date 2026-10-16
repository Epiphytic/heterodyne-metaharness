#!/usr/bin/env python3
"""Prepare a private migration rehearsal and pinned installer command; never deploy."""

import argparse
import hashlib
import inspect
import json
import os
import pwd
import shlex
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import quote

from heterodyne.admind.store import Store


def git(checkout: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "--no-replace-objects", "-C", str(checkout), "-c", "core.hooksPath=/dev/null", *args],
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )


def snapshot(source: Path, destination: Path) -> None:
    """Include committed WAL contents through SQLite, never a filesystem copy."""
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    src = sqlite3.connect(f"file:{quote(str(source.resolve()))}?mode=ro", uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("snapshot integrity check failed")
    finally:
        dst.close()
        src.close()


def preserved(before: sqlite3.Connection, after: sqlite3.Connection) -> None:
    """Compare original columns privately, permitting only pending-row guard holds."""
    tables = before.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for (table,) in tables:
        if table.startswith("sqlite_"):
            continue
        name = '"' + table.replace('"', '""') + '"'
        columns = [r[1] for r in before.execute(f"PRAGMA table_info({name})")]
        if table == "outbox":
            columns = [c for c in columns if c != "status"]
        selection = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
        old = before.execute(f"SELECT {selection} FROM {name}").fetchall()  # noqa: S608
        new = after.execute(f"SELECT {selection} FROM {name}").fetchall()  # noqa: S608
        if table == "kv":
            new = [r for r in new if r[0] in {r[0] for r in old}]
        if sorted(map(repr, old)) != sorted(map(repr, new)):
            raise ValueError("migration changed pre-existing history or identity")
    old_status = dict(before.execute("SELECT seq,status FROM outbox"))
    new_status = dict(after.execute("SELECT seq,status FROM outbox"))
    if old_status.keys() != new_status.keys() or any(
        new_status[seq] != status and not (status == "pending" and new_status[seq] == "quarantined")
        for seq, status in old_status.items()
    ):
        raise ValueError("migration changed a protected delivery status")


def prepare(checkout: Path, commit: str, database: Path, output: Path, authority: Path) -> dict[str, object]:
    """No service, policy, queue, live migration, rearm or outbound operation exists here."""
    checkout, output = checkout.resolve(), output.absolute()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ValueError("reviewed commit must be a full SHA-1 revision")
    if git(checkout, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("checkout does not match reviewed revision")
    if git(checkout, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("reviewed checkout must be clean")
    if not Path(inspect.getfile(Store)).resolve().is_relative_to(checkout / "src"):
        raise ValueError("migration code was not imported from the reviewed checkout")
    if output.resolve().is_relative_to(checkout) or database.is_symlink() or not database.is_file():
        raise ValueError("require an existing regular database and output outside the checkout")
    authority_bytes = authority.read_bytes()
    if not authority_bytes.strip():
        raise ValueError("operator authority evidence is empty")
    output.mkdir(mode=0o700)  # Exclusive, deliberately not resumable over an existing rehearsal.
    archive = git(checkout, "archive", "--format=tar", commit, "--", "deploy",
                  "src/heterodyne/harness/resources/store_core.py")
    installer = git(checkout, "show", f"{commit}:deploy/install.sh")
    (output / "release.tar").write_bytes(archive)
    snapshot(database, output / "before.db")
    snapshot(output / "before.db", output / "rehearsal.db")
    migrated = Store(output / "rehearsal.db")
    original = sqlite3.connect(output / "before.db")
    try:
        preserved(original, migrated.db)
        result: dict[str, object] = {
            "reviewed_commit": commit,
            "authority_evidence_sha256": hashlib.sha256(authority_bytes).hexdigest(),
            "archive_sha256": hashlib.sha256(archive).hexdigest(),
            "installer_sha256": hashlib.sha256(installer).hexdigest(),
            "before_status_counts": dict(original.execute(
                "SELECT status,count(*) FROM outbox GROUP BY status")),
            "after_status_counts": dict(migrated.db.execute(
                "SELECT status,count(*) FROM outbox GROUP BY status")),
            "pending_groups_by_ownership": dict(migrated.db.execute(
                "SELECT ownership_kind,count(DISTINCT delivery_group) FROM outbox "
                "WHERE status='pending' GROUP BY ownership_kind")),
            "quarantined_groups": migrated.db.execute(
                "SELECT count(DISTINCT delivery_group) FROM outbox WHERE status='quarantined'").fetchone()[0],
            "delayed_groups": migrated.db.execute(
                "SELECT count(DISTINCT delivery_group) FROM outbox WHERE status='delayed'").fetchone()[0],
            "preserved_original_rows_and_identity": True,
            "deployed": False,
        }
    finally:
        original.close()
        migrated.close()
    # This is the project's existing pinned installer protocol. Emitted text is never executed here.
    command = (
        "#!/usr/bin/env bash\nset -euo pipefail\numask 077\n"
        'b=$(mktemp /run/hz-install-boot.XXXXXX)\ntrap \'rm -f "$b"\' EXIT\n'
        f"cat {shlex.quote(str(checkout / 'deploy/install.sh'))} >\"$b\"\n"
        f"echo '{result['installer_sha256']}  '\"$b\" | sha256sum --check --strict --quiet\n"
        f"bash \"$b\" --checkout {shlex.quote(str(checkout))} --commit {commit} "
        f"--expect-sha256 {result['archive_sha256']} "
        f"--service-user {shlex.quote(pwd.getpwuid(os.getuid()).pw_name)}\n"
    )
    (output / "installer-command.sh").write_text(command)
    (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--reviewed-commit", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authority-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.checkout, args.reviewed_commit, args.database, args.output,
                             args.authority_file), indent=2))


if __name__ == "__main__":
    main()
