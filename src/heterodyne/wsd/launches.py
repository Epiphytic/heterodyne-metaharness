"""Launch entries and launch receipts (ADR 0001 r14 §3.3, §4.4 D2; AU-3 design §2.2, §2.3).

Every launch through the guard gets one entry, keyed (session key, generation): journaled first, then
appended to the bead's `metadata.wsd_launches` with read-back, and only then launched. An entry is never
removed or changed, except that each of the set-once fields (the dispatch mark, the native ID, the
reported model and the outcome) may be set once while it is empty. Entries and generation sequences
rebuild from the bead copy alone.

A receipt is what `AgentRuntime.launch` returned or raised for one dispatched generation. It is
journal-only: a session can write bead metadata, so a bead copy would be forgeable, and nothing ever
rebuilds one. Only a receipt sets the outcome of a dispatched entry; tags, hooks and transcripts never do.
"""

from dataclasses import dataclass
from typing import Literal

import msgspec

LAUNCHES_KEY = "wsd_launches"
SET_ONCE = ("dispatched_at", "native_id", "model_reported", "outcome")
LAUNCHED = "launched"
ABANDONED = "abandoned"
Outcome = Literal["launched", "abandoned"]


class LaunchEntry(msgspec.Struct, frozen=True, omit_defaults=True, forbid_unknown_fields=True):
    """One launch, as journaled and as copied to the bead. In the bead copy the set-once fields are
    omitted while empty, never written as null."""
    session_key: str
    generation: int
    ws: str
    bead: str
    role: str
    profile: str
    account: str
    credential_key: str
    model_passed: str
    adopted: bool
    journaled_at: str
    dispatched_at: str | None = None
    native_id: str | None = None
    model_reported: str | None = None
    outcome: Outcome | None = None

    @property
    def ident(self) -> tuple[str, int]:
        return self.session_key, self.generation


def encode_entry(entry: LaunchEntry) -> bytes:
    return msgspec.json.encode(entry, order="sorted")


def mergeable(current: LaunchEntry, wanted: LaunchEntry) -> bool:
    """Whether `wanted` only sets set-once fields that are empty in `current`."""
    for name in LaunchEntry.__struct_fields__:
        have, want = getattr(current, name), getattr(wanted, name)
        if have == want:
            continue
        if name not in SET_ONCE or have is not None:
            return False
    return True


class LaunchesUnreadable(Exception):
    """The bead's `wsd_launches` is present but is not a list of launch entries: never guessed at."""


@dataclass(frozen=True)
class BeadLaunches:
    """The bead copy as read: every entry with its bytes exactly as stored, so a rewrite keeps the others
    byte-for-byte."""
    raw: tuple[bytes, ...]
    entries: tuple[LaunchEntry, ...]

    def find(self, ident: tuple[str, int]) -> int | None:
        for i, entry in enumerate(self.entries):
            if entry.ident == ident:
                return i
        return None

    def with_entry(self, entry: LaunchEntry) -> str:
        """The array with `entry` appended, or put in place of the entry it updates."""
        raw = list(self.raw)
        at = self.find(entry.ident)
        if at is None:
            raw.append(encode_entry(entry))
        else:
            raw[at] = encode_entry(entry)
        return (b"[" + b",".join(raw) + b"]").decode()


_RAW_LIST = msgspec.json.Decoder(list[msgspec.Raw])
_ENTRY = msgspec.json.Decoder(LaunchEntry)


def decode_launches(value: object) -> BeadLaunches:
    """The bead copy from the metadata value; absent is an empty list. Raises LaunchesUnreadable."""
    if value is None:
        return BeadLaunches((), ())
    if not isinstance(value, str):
        raise LaunchesUnreadable("wsd_launches is not a JSON string")
    try:
        raws = _RAW_LIST.decode(value)
        entries = tuple(_ENTRY.decode(r) for r in raws)
    except (msgspec.DecodeError, msgspec.ValidationError):
        raise LaunchesUnreadable("wsd_launches does not parse as launch entries") from None
    idents = [e.ident for e in entries]
    if len(set(idents)) != len(idents):
        raise LaunchesUnreadable("wsd_launches has two entries for one generation")
    return BeadLaunches(tuple(bytes(r) for r in raws), entries)


@dataclass(frozen=True)
class Receipt:
    session_key: str
    generation: int
    kind: Literal["started", "refused"]
    refusal: Literal["failed", "unavailable"] | None = None
    tmux_session: str | None = None
    tmux_pane: str | None = None
    pane_pid: int | None = None
    native_id: str | None = None
    error: str | None = None
