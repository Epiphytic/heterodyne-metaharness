"""Finite source profiles, collection ownership, freshness and warning coalescing."""

from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, cast

from . import dto as d
from .safety import Span, generated
from .validation import fail, instant

_ORDER = ("configuration", "beads", "wsd-journal", "account-usage")
_PAIR = tuple[str, str]
_BASE = (("configuration", "configuration"), ("wsd-journal", "state"))
_PROFILES: dict[str, tuple[tuple[_PAIR, ...], tuple[_PAIR, ...]]] = {
    "task": ((("wsd-journal", "state"),), ()),
    "workstreams": (
        _BASE,
        (
            ("wsd-journal", "running"),
            ("beads", "approvals"),
            ("beads", "candidates"),
            ("account-usage", "usage"),
        ),
    ),
    "workstream-status": (
        _BASE,
        tuple(("wsd-journal", name) for name in ("running", "ready", "parked", "held", "deferred"))
        + (("beads", "approvals"), ("account-usage", "usage")),
    ),
    "control-status": (
        _BASE,
        (("beads", "approvals"), ("wsd-journal", "needs-human"), ("account-usage", "usage")),
    ),
    "bead-status": (
        (("wsd-journal", "state"),),
        tuple(("wsd-journal", name) for name in ("recent-events", "decisions", "transcripts", "launches")),
    ),
    "approvals": ((("beads", "approvals"),), ()),
    "queue": ((("beads", "candidates"), ("wsd-journal", "pause")), ()),
    "approval": (
        (("configuration", "configuration"), ("beads", "approvals"), ("wsd-journal", "decisions")),
        (),
    ),
    "question": (
        (("configuration", "configuration"), ("wsd-journal", "needs-human"), ("wsd-journal", "decisions")),
        (),
    ),
    "fyi": (_BASE, ()),
}
_NOTICE: dict[type[Any], tuple[_PAIR, ...]] = {
    d.AllBlocked: (("configuration", "configuration"), ("beads", "state"), ("beads", "approvals")),
    d.AllAccountsExhausted: (("configuration", "configuration"), ("account-usage", "usage")),
    d.AccountChanged: (("configuration", "configuration"), ("account-usage", "usage")),
    d.DeliveryUnconfirmed: (
        ("configuration", "configuration"),
        ("wsd-journal", "state"),
        ("wsd-journal", "decisions"),
    ),
    d.Reminder: (("configuration", "configuration"), ("wsd-journal", "state"), ("wsd-journal", "decisions")),
    d.Digest: (
        ("configuration", "configuration"),
        ("beads", "closed-yesterday"),
        ("beads", "open-approval-labels"),
        ("wsd-journal", "needs-human"),
        ("wsd-journal", "idle-workstreams"),
        ("account-usage", "accounts-hit-limit"),
    ),
    d.TrustSuspended: _BASE,
    d.CannotApprove: (),
    d.Terminal: (),
}
_PAYLOAD = {
    "task": d.Task,
    "workstreams": d.Workstreams,
    "workstream-status": d.WorkstreamStatus,
    "control-status": d.ControlStatus,
    "bead-status": d.BeadStatus,
    "approvals": d.Approvals,
    "queue": d.Queue,
    "approval": d.Approval,
    "question": d.Question,
    "fyi": d.Fyi,
}


def scope(view: d.PresentationView) -> str | None:
    if isinstance(view.origin, d.QueryOrigin):
        return view.origin.workstream
    return None


def profile(view: d.Available) -> tuple[tuple[_PAIR, ...], tuple[_PAIR, ...]]:
    return (_NOTICE[type(view.payload)], ()) if view.kind == "notice" else _PROFILES[view.kind]


def rows_in(value: Any) -> tuple[d.Rows[Any], ...]:
    if isinstance(value, d.Rows):
        return (cast(d.Rows[Any], value),)
    if is_dataclass(value):
        return tuple(row for field in fields(value) for row in rows_in(getattr(value, field.name)))
    if isinstance(value, tuple):
        return tuple(row for item in cast(tuple[Any, ...], value) for row in rows_in(item))
    return ()


def _origin(view: d.PresentationView) -> None:
    origin = view.origin
    if isinstance(origin, d.EventOrigin):
        if view.kind not in ("task", "approval", "question", "fyi", "notice"):
            fail()
        return
    expected = {
        "Workstreams": "workstreams",
        "Approvals": "approvals",
        "BeadStatus": "bead-status",
        "Queue": "queue",
        "QueueFor": "queue",
        "Status": "control-status" if origin.workstream is None else "workstream-status",
    }[origin.query]
    if expected != view.kind:
        fail()
    if (
        (origin.query == "BeadStatus") != (origin.bead is not None)
        or (origin.query == "QueueFor" and origin.workstream is None)
        or (origin.query not in ("Status", "QueueFor") and origin.workstream is not None)
    ):
        fail()
    if isinstance(view, d.Available):
        payload = view.payload
        if isinstance(payload, d.BeadStatus) and payload.bead != origin.bead:
            fail()
        if isinstance(payload, d.WorkstreamStatus) and payload.name != origin.workstream:
            fail()


def validate_view(view: d.PresentationView) -> None:
    _origin(view)
    if isinstance(view, d.Missing):
        return
    if view.kind == "notice":
        if type(view.payload) not in _NOTICE:
            fail()
    elif type(view.payload) is not _PAYLOAD[view.kind]:
        fail()
    if len(view.sections) > 1024:
        fail("bounds")
    statuses = {status.key: status for status in view.sections}
    if len(statuses) != len(view.sections):
        fail("duplicate-key")
    required, optional = profile(view)
    allowed = set(required + optional)
    for status in view.sections:
        pair = (status.key.source, status.key.collection)
        if (
            pair not in allowed
            or status.key.workstream != scope(view)
            or status.required != (pair in required)
        ):
            fail()
    field_pairs = {
        "workstreams": ("wsd-journal", "state"),
        "running": ("wsd-journal", "running"),
        "ready": ("wsd-journal", "ready"),
        "parked": ("wsd-journal", "parked"),
        "held": ("wsd-journal", "held"),
        "deferred": ("wsd-journal", "deferred"),
        "approvals": ("beads", "approvals"),
        "needs_human": ("wsd-journal", "needs-human"),
        "usage": ("account-usage", "usage"),
        "recent_events": ("wsd-journal", "recent-events"),
        "decisions": ("wsd-journal", "decisions"),
        "transcript_labels": ("wsd-journal", "transcripts"),
        "launches": ("wsd-journal", "launches"),
        "candidates": ("beads", "candidates"),
        "closed_yesterday": ("beads", "closed-yesterday"),
        "open_approval_labels": ("beads", "open-approval-labels"),
        "idle_workstreams": ("wsd-journal", "idle-workstreams"),
        "accounts_that_hit_a_limit": ("account-usage", "accounts-hit-limit"),
    }
    for field in fields(view.payload):
        value = getattr(view.payload, field.name)
        if isinstance(value, d.Rows) and (value.key.source, value.key.collection) != field_pairs[field.name]:
            fail()
    if view.kind in ("approval", "question") and any(
        s.count is not None or s.order != "none" for s in view.sections
    ):
        fail()
    # Required missing sections are visible source failure, never malformed data.
    for row in rows_in(view.payload):
        status = statuses.get(row.key)
        if row.key.workstream != scope(view):
            fail()
        if status is None:
            if (row.key.source, row.key.collection) in required:
                continue
            fail()
        if status.count is None:
            if status.availability != "unavailable" or row.items:
                fail()
        elif status.count.shown != len(row.items):
            fail()
        if row.items and all(isinstance(item, d.ApprovalSummary) for item in row.items):
            keys = [(item.created_at, bytes.fromhex(item.opaque_order_key)) for item in row.items]
            opaque = [item.opaque_order_key for item in row.items]
            if keys != sorted(keys) or len(set(opaque)) != len(opaque):
                fail("duplicate-key")
        if row.items and all(isinstance(item, d.Candidate) for item in row.items):
            if [item.ordinal for item in row.items] != list(range(len(row.items))):
                fail()
            segments = [item.segment == "native-ready" for item in row.items]
            if segments != sorted(segments):
                fail()
        identities = [getattr(item, "bead", getattr(item, "name", None)) for item in row.items]
        identities = [item for item in identities if item is not None]
        if len(identities) != len(set(identities)):
            fail("duplicate-key")
    payload = view.payload
    if isinstance(payload, d.BeadStatus):
        events = payload.recent_events.items
        sequences = [event.sequence for event in events]
        if len(events) > 5:
            fail("bounds")
        if sequences != sorted(set(sequences)):
            fail("duplicate-key")


def source_failure(view: d.Available, protected: bool = False) -> str | None:
    required, _ = profile(view)
    lookup = {(s.key.source, s.key.collection): s for s in view.sections}
    for pair in required:
        status = lookup.get(pair)
        if (
            status is None
            or status.availability == "unavailable"
            or status.completeness == "unavailable"
            or (pair == ("beads", "candidates") and status.order != "owner-queue-order")
            or (view.kind == "approvals" and status.order != "approvals-created-key")
        ):
            return "required-source"
    if protected and any(not fresh(lookup[pair], view.rendered_at) for pair in required):
        return "stale-card"
    return None


def fresh(status: d.SectionStatus, rendered: datetime) -> bool:
    if status.observed_at is None or status.availability != "reachable":
        return False
    delta = rendered - status.observed_at
    return 0 <= delta.total_seconds() <= 30


def _state(status: d.SectionStatus, rendered: datetime) -> str | None:
    if status.availability == "unavailable" or status.completeness == "unavailable":
        return "unavailable"
    if status.availability == "cached-unreachable":
        return "unreachable"
    if status.observed_at is None or status.observed_at > rendered:
        return "time-unverified"
    if not fresh(status, rendered):
        return "stale"
    return "refreshing" if status.refresh == "pending" else None


def warnings(view: d.Available) -> tuple[Span, ...]:
    precedence = ("unavailable", "unreachable", "time-unverified", "stale", "refreshing")
    selected: list[tuple[str, str, datetime | None]] = []
    for source in _ORDER:
        entries = [status for status in view.sections if status.key.source == source]
        states = [state for status in entries if (state := _state(status, view.rendered_at)) is not None]
        if states:
            times = [status.observed_at for status in entries]
            selected.append(
                (
                    source,
                    min(states, key=precedence.index),
                    min(time for time in times if time is not None)
                    if times and all(time is not None and time <= view.rendered_at for time in times)
                    else None,
                )
            )
    if len(selected) == 4:
        times = [entry[2] for entry in selected]
        oldest = (
            min(time for time in times if time is not None)
            if all(time is not None for time in times)
            else None
        )
        return (
            generated(f"⚠️ configuration: {selected[0][1]}; beads: {selected[1][1]}"),
            generated(f"⚠️ wsd-journal: {selected[2][1]}; account-usage: {selected[3][1]}"),
            generated("Warnings as of " + (instant(oldest) if oldest is not None else "unknown")),
        )
    return tuple(
        generated(
            f"⚠️ beads unreachable, data as of {instant(time) if time else 'unknown'}"
            if source == "beads" and state == "unreachable"
            else f"⚠️ {source}: {state}; as of {instant(time) if time else 'unknown'}"
        )
        for source, state, time in selected
    )


def footer(view: d.Available) -> Span:
    required, _ = profile(view)
    entries = [s for s in view.sections if (s.key.source, s.key.collection) in required]
    times = [s.observed_at for s in entries]
    valid = times and all(time is not None and time <= view.rendered_at for time in times)
    oldest = min(time for time in times if time is not None) if valid else None
    return generated(
        f"Observed {instant(oldest) if oldest else 'unknown'}; rendered {instant(view.rendered_at)}"
    )
