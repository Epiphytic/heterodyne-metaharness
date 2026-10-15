"""wsd's notice client (plan 5i part 2, Task 3): FYI notices to the operators through admind's `ask.sock`.

A notice is journaled by its `ref` and posted at most once. Before the post, the row becomes `sending` in
its own committed transaction, so a wsd that dies anywhere inside the post window leaves it `sending`;
`recover` makes it `uncertain` at the next start, and it is never posted again. Only an attempt known to
have written nothing (no socket, or a retryable refusal) leaves it `owed` for `retry_owed`. `send` never
raises on transport.

A title or body holding a secret or a public identifier is never stored or posted: its row is journaled
`withheld` with no text, and a fixed notice (`withheld:<ref>`) naming only the kind goes out in its place.
"""

import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from heterodyne.config.secret_scan import sensitive_kind
from heterodyne.wsd import relay
from heterodyne.wsd.checkpoints import Checkpoint, nothing
from heterodyne.wsd.journal import Journal, NoticeRow
from heterodyne.wsd.states import BeadState

# Part 1 T6 owns this (`workstream.NoticeKind`); a copy until part 1 is on this branch, then imported.
NoticeKind = Literal["stuck", "waiting", "closed", "review"]

REF_MAX = 128
TITLE_MAX = 200                 # admind's notices.MAX_TITLE
BODY_MAX_CHARS = 3500           # admind's notices.MAX_BODY_CHARS
BODY_MAX_LINES = 40             # admind's notices.MAX_BODY_LINES
TRUNCATED = "(truncated)"
RETRY_PER_CALL = 3              # so one pickup never spends admind's hourly notice cap
WITHHELD = "withheld:"
WITHHELD_TITLE = "wsd notice for {subject} withheld: it contained {kind}."
EXPIRED = "expired"
IN_FLIGHT_EVENT = "notice_in_flight"
DIAG_NOTICE_IN_FLIGHT = "notice {ref} was in flight when wsd stopped; it is not posted again"
_TITLE_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")

PostNotice = Callable[..., str]


def _iso(at: int) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _lines(text: str) -> int:
    return text.rstrip("\n").count("\n") + 1


def fit(body: str) -> str:
    """`body` within admind's bounds; cut ones end with TRUNCATED on a line of its own."""
    if len(body) <= BODY_MAX_CHARS and _lines(body) <= BODY_MAX_LINES:
        return body
    kept = "\n".join(body.split("\n")[:BODY_MAX_LINES - 1])
    return kept[:BODY_MAX_CHARS - len(TRUNCATED) - 1] + "\n" + TRUNCATED


def check_title(title: str) -> None:
    """A title is one of wsd's templates, so a bad one is a bug: ValueError."""
    if not 1 <= len(title) <= TITLE_MAX or not title.strip() or _TITLE_CONTROLS.search(title):
        raise ValueError(f"a notice title must be one line of 1-{TITLE_MAX} characters")


class Notices:
    def __init__(self, journal: Journal, sock: Path, *, clock: Callable[[], int], backoff: float = 60.0,
                 backoff_max: float = 1800.0, owed_hours: int = 24, timeout: float = 30.0,
                 cp: Checkpoint = nothing, post: PostNotice = relay.post_notice) -> None:
        self.journal = journal
        self.sock = sock
        self.clock = clock
        self.backoff = backoff
        self.backoff_max = backoff_max
        self.owed_hours = owed_hours
        self.timeout = timeout
        self.cp = cp
        self.post = post

    def send(self, ws: str, bead: str | None, kind: NoticeKind, title: str, body: str, ref: str) -> None:
        """Journal the notice by `ref` and make one attempt; a ref already journaled, in any state, is
        left alone."""
        if not 1 <= len(ref) <= REF_MAX:
            raise ValueError(f"a notice ref must be 1-{REF_MAX} characters")
        now = self.clock()
        at = _iso(now)
        found = sensitive_kind(title) or sensitive_kind(body)
        if found is not None:
            fixed = WITHHELD_TITLE.format(subject=bead or ws, kind=found)
            check_title(fixed)
            with self.journal.transaction():
                if not self.journal.notice_add(ref, ws, bead, kind, "", "", "withheld", None, at, why=found):
                    return
                self.journal.notice_add(WITHHELD + ref, ws, bead, kind, fixed, "", "owed", now, at)
            self.cp("notice.owed")
            self._attempt(WITHHELD + ref, now)
            return
        check_title(title)
        with self.journal.transaction():
            if not self.journal.notice_add(ref, ws, bead, kind, title, fit(body), "owed", now, at):
                return
        self.cp("notice.owed")
        self._attempt(ref, now)

    def retry_owed(self, now: int) -> None:
        """At each pickup: expire the owed notices of closed beads that are too old, then retry at most
        RETRY_PER_CALL of the due ones, oldest first."""
        for row in self.journal.notices_in("owed"):
            if self._expired(row, now):
                with self.journal.transaction():
                    self.journal.notice_set(row.ref, _iso(now), state="refused", why=EXPIRED)
        for row in self.journal.notices_due(now, RETRY_PER_CALL):
            self._attempt(row.ref, now)

    def recover(self) -> None:
        """Once at start, before the first pickup: a `sending` row was cut off inside the post window, so
        it may have been posted. It becomes `uncertain` and is never posted again."""
        at = _iso(self.clock())
        for row in self.journal.notices_in("sending"):
            with self.journal.transaction():
                self.journal.notice_set(row.ref, at, state="uncertain")
                self.journal.emit_once(row.ws, row.bead, IN_FLIGHT_EVENT,
                                       DIAG_NOTICE_IN_FLIGHT.format(ref=row.ref), row.ref)

    def _expired(self, row: NoticeRow, now: int) -> bool:
        if row.bead is None or now - _epoch(row.created_at) <= self.owed_hours * 3600:
            return False
        state = self.journal.state(row.ws, row.bead)
        return state is not None and state.state is BeadState.CLOSED

    def _attempt(self, ref: str, now: int) -> None:
        with self.journal.transaction():
            row = self.journal.notice(ref)
            if row is None or row.state != "owed":
                return
            attempts = row.attempts + 1
            self.journal.notice_set(ref, _iso(now), state="sending", attempts=attempts)
        self.cp("notice.sending")
        outcome: dict[str, str | int | None]
        try:
            notice_id = self.post(self.sock, poster=relay.poster_for(row.ws), title=row.title, body=row.body,
                                  timeout=self.timeout)
        except relay.NoticeUnavailable:
            outcome = {"state": "owed", "next_at": self._next(now, attempts)}
        except relay.NoticeRefused as exc:
            outcome = ({"state": "owed", "next_at": self._next(now, attempts), "why": exc.code}
                       if exc.retryable else {"state": "refused", "why": exc.code})
        except Exception:   # NoticeUncertain, or anything else once the attempt began: it may have posted
            outcome = {"state": "uncertain"}
        else:
            outcome = {"state": "posted", "notice_id": notice_id}
        with self.journal.transaction():
            self.journal.notice_set(ref, _iso(now), **outcome)
            self.cp("notice.outcome")

    def _next(self, now: int, attempts: int) -> int:
        return now + math.ceil(min(self.backoff * 2 ** (attempts - 1), self.backoff_max))
