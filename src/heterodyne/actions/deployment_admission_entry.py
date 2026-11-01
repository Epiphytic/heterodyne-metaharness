"""Fixed no-effect admission probe for the source-qualified native host parent.

This entry does not implement a deployment runner. It proves native handoff and
root durable admission, then settles only its genuinely never-issued job and
reports failed-no-effect. That is a native HOLD, never successful installation.
No mutable selectors or receipt-file arguments exist. Only the reviewed fixed
parent invocation with original descriptor custody may enter this composition.
"""

# pyright: reportPrivateUsage=false

import os
import sqlite3
import sys
from typing import Literal

from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.contracts import Record
from heterodyne.actions.deployment_admission_journal import _AdmissionJournal
from heterodyne.actions.deployment_admission_peer import _ParentChannel


class _Result(Record, frozen=True):
    schema: Literal['heterodyne-owned-deployment-admission-result.v1']
    nonce_hex: str
    status: Literal['failed-no-effect', 'unknown']


def _reply(channel: _ParentChannel, status: Literal['failed-no-effect', 'unknown']) -> None:
    channel.check()
    raw = encode_record(_Result('heterodyne-owned-deployment-admission-result.v1',
                                channel.nonce.hex(), status))
    if channel._conn.send(raw) != len(raw):
        raise InvalidAction('admission-result-short-write')


def _entry() -> int:  # pyright: ignore[reportUnusedFunction] - fixed verified native prologue only
    if len(sys.argv) != 1:
        return 2
    channel: _ParentChannel | None = None
    journal: _AdmissionJournal | None = None
    admitted = False
    packet: str | None = None
    try:
        channel = _ParentChannel()
        journal = _AdmissionJournal()
        packet = journal.admit(channel)
        admitted = True
        journal.fail_unissued(channel, packet)
        _reply(channel, 'failed-no-effect')
        return 0
    except (InvalidAction, OSError, ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
        # A lost reply, duplicate consumption, or uncertain durable commit can
        # never be retried as a fresh approval. Parent independently retains HOLD.
        if channel is not None and admitted:
            try:
                if journal is not None and packet is not None:
                    journal.fail_unissued(channel, packet)
            except (InvalidAction, OSError, sqlite3.Error):
                pass
            try:
                _reply(channel, 'unknown')
            except (InvalidAction, OSError):
                pass
        os.write(2, b'owned-deployment-admission-held\n')
        return 2
    finally:
        if journal is not None:
            journal.close()
        if channel is not None:
            channel.close()
