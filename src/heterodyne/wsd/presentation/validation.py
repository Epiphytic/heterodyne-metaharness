"""Closed schema and canonical size validation, including direct DTO construction."""

import json
import re
import types
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any, Literal, NoReturn, TypeVar, Union, cast, get_args, get_origin, get_type_hints

from heterodyne.wsd.ids import SLUG

from . import dto as d

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX12 = re.compile(r"[0-9a-f]{12}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
_ORDER = re.compile(r"(?:[0-9a-f]{2}){1,128}\Z")
_CHOICE = re.compile(r"[a-z][a-z0-9-]{0,15}\Z")


class BadInput(ValueError):
    """Fixed error code only: never retain hostile input in diagnostic text."""


def fail(code: str = "schema") -> NoReturn:
    raise BadInput(code)


def instant(value: datetime) -> str:
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}.{value.microsecond:06d}Z"
    )


def primitive(value: Any) -> Any:
    if isinstance(value, datetime):
        return instant(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [primitive(item) for item in cast(tuple[Any, ...], value)]
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


@cache
def _hints(expected: type) -> dict[str, Any]:
    return get_type_hints(expected)


def _shape(value: Any, expected: Any, substitutions: dict[Any, Any] | None = None) -> None:
    substitutions = {} if substitutions is None else substitutions
    expected = substitutions.get(expected, expected)
    origin, args = get_origin(expected), get_args(expected)
    if origin in (Union, types.UnionType):
        # Closed variants have exact runtime types. Select the matching variant
        # before descending so a nested bounds/payload error is not erased by
        # subsequent alternatives that merely have the wrong outer shape.
        for candidate in args:
            candidate_type = get_origin(candidate) or candidate
            literal_match = candidate_type is Literal and any(
                type(value) is type(item) and value == item for item in get_args(candidate)
            )
            if literal_match or isinstance(candidate_type, type) and type(value) is candidate_type:
                _shape(value, candidate, substitutions)
                return
        fail()
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            fail()
    elif origin is tuple:
        if type(value) is not tuple:
            fail()
        sequence = cast(tuple[Any, ...], value)
        if len(sequence) > 1024:
            fail("bounds")
        if len(args) == 2 and args[1] is Ellipsis:
            for item in sequence:
                _shape(item, args[0], substitutions)
        else:
            if len(sequence) != len(args):
                fail()
            for item, schema in zip(sequence, args, strict=True):
                _shape(item, schema, substitutions)
    elif origin is d.Rows:
        _shape(value, d.Rows, {d.T: args[0]})
    elif isinstance(expected, TypeVar):
        fail()
    elif expected is datetime:
        if type(value) is not datetime or value.tzinfo is not UTC:
            fail("timestamp")
    elif expected in (str, int, bool, type(None)):
        if type(value) is not expected:
            fail()
        if expected is str:
            try:
                size = len(value.encode("utf-8"))
            except UnicodeError:
                fail("payload")
            if len(value) > 4096 or size > 16384:
                fail("bounds")
    elif isinstance(expected, type) and is_dataclass(expected):
        if type(value) is not expected:
            fail()
        if expected is d.Rows:
            if type(value.items) is not tuple:
                fail()
            if len(value.items) > 100:
                fail("bounds")
        hints = _hints(expected)
        for field in fields(expected):
            _shape(getattr(value, field.name), hints[field.name], substitutions)
    else:
        fail()


def _unique(values: tuple[Any, ...]) -> None:
    if len(values) != len(set(values)):
        fail("duplicate-key")


def _label(value: str) -> None:
    if not value or len(value) > 80 or value.strip() != value or "\n" in value:
        fail("bounds")


def _slug(value: str | None) -> None:
    if value is not None and SLUG.fullmatch(value) is None:
        fail()


def _reason(state: str, reason: d.Reason) -> None:
    if state == "held" and not isinstance(reason, d.Held):
        fail()
    if state == "deferred" and not isinstance(reason, (d.Quota, d.AccountChanged, d.Unknown)):
        fail()
    if reason is None and state in ("held", "deferred"):
        fail()


def _repo(value: d.Repo) -> None:
    if (
        any(_COMPONENT.fullmatch(part) is None or part in (".", "..") for part in (value.owner, value.name))
        or re.fullmatch(r"https://[A-Za-z0-9.-]+", value.origin) is None
        or ".." in value.origin
        or value.origin.endswith(".")
        or value.origin.endswith("-")
    ):
        fail()
    if value.forge == "github" and value.origin != "https://github.com":
        fail()


def _semantic(value: Any) -> None:
    if isinstance(value, (d.Available, d.Missing)):
        if value.version != 1:
            fail()
    if isinstance(value, d.Count) and not 0 <= value.shown <= value.total:
        fail("bounds")
    if isinstance(value, d.SectionKey):
        _slug(value.workstream)
    if isinstance(value, d.SectionStatus):
        if value.availability == "unavailable" and (
            value.count is not None or value.completeness != "unavailable"
        ):
            fail()
    if isinstance(value, d.RenderPolicy):
        if not 200 <= value.max_chars <= 4000:
            fail("bounds")
        for mappings in (value.session_labels, value.transcript_labels):
            _unique(tuple(item.key for item in mappings))
        _unique(value.forges)
        if any(not form for form in value.login_dir_forms):
            fail()
    if isinstance(value, d.DisplayLabel):
        _label(value.label)
    if isinstance(value, (d.Task, d.BeadStatus, d.ApprovalSummary, d.NeedsHuman, d.Candidate, d.Approval)):
        _slug(value.bead)
    if isinstance(value, (d.Task, d.WorkstreamRow)):
        _reason(value.state, value.reason)
    if isinstance(value, d.BeadContext):
        _slug(value.short_bead)
    if isinstance(value, (d.AllBlocked, d.AllAccountsExhausted)):
        _slug(value.workstream)
    if isinstance(value, d.Rows):
        items = cast(d.Rows[Any], value).items
        if items and type(items[0]) is str:
            _unique(items)
            for label in items:
                _label(label)
    if isinstance(value, d.WorkstreamRow):
        _slug(value.name)
        _slug(value.current_bead)
        if len(value.role_headroom) > 100:
            fail("bounds")
    if isinstance(value, d.ApprovalSummary) and _ORDER.fullmatch(value.opaque_order_key) is None:
        fail()
    if isinstance(value, d.Candidate):
        _unique(value.blocker_labels)
    if isinstance(value, d.ChoiceQuestion):
        if not 2 <= len(value.choices) <= 6:
            fail("bounds")
        labels = tuple(choice.label for choice in value.choices)
        _unique(labels)
        if value.recommended_label is not None and value.recommended_label not in labels:
            fail()
    if isinstance(value, d.Choice) and (_CHOICE.fullmatch(value.label) is None or not value.description):
        fail()
    if isinstance(value, (d.Approval, d.Question, d.BeadContext)) and HEX12.fullmatch(value.digest12) is None:
        fail()
    if isinstance(value, d.OperatorDoc) and HEX12.fullmatch(value.content_digest12) is None:
        fail()
    if isinstance(value, d.EffectText) and (not value.spans or len(value.spans) > 100):
        fail("bounds")
    if isinstance(value, d.PublicHash):
        length = 40 if value.algorithm == "sha1" else 64
        if re.fullmatch("[0-9a-f]{" + str(length) + "}", value.text) is None:
            fail()
    if isinstance(value, d.Repo):
        _repo(value)
    if isinstance(value, (d.File, d.Commit)) and HEX40.fullmatch(value.commit) is None:
        fail()
    if isinstance(value, (d.PullRequest, d.Range)) and HEX40.fullmatch(value.head) is None:
        fail()
    if isinstance(value, d.Range) and HEX40.fullmatch(value.base) is None:
        fail()
    if isinstance(value, d.PullRequest) and value.number <= 0:
        fail()
    if isinstance(value, d.File):
        if any(_COMPONENT.fullmatch(part) is None or part in (".", "..") for part in value.path.split("/")):
            fail()
        if (
            value.line_end is not None
            and value.line_start is None
            or value.line_start is not None
            and value.line_start <= 0
            or value.line_end is not None
            and value.line_end < (value.line_start or 0)
        ):
            fail()
    # Numeric facts are nonnegative; percentages have an independent upper bound.
    label_fields = (
        "agent",
        "role",
        "branch",
        "account",
        "account_label",
        "launch_label",
        "actor_label",
        "session_label",
        "card_safe_label",
    )
    numeric_value = cast(Any, value)
    for name in label_fields:
        item = getattr(numeric_value, name, None)
        if item is not None:
            _label(item)
    if is_dataclass(numeric_value):
        for field in fields(numeric_value):
            item = getattr(numeric_value, field.name)
            if type(item) is int and item < 0:
                fail("bounds")
            if field.name.endswith("_percent") and item is not None and not 0 <= item <= 100:
                fail("bounds")


def _semantics(value: Any) -> None:
    """Semantic regex checks start only after closed shape and canonical byte bounds."""
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _semantics(getattr(value, field.name))
        _semantic(value)
    elif isinstance(value, tuple):
        for item in cast(tuple[Any, ...], value):
            _semantics(item)


def validate(value: Any, expected: Any) -> None:
    _shape(value, expected)
    try:
        size = len(canonical_bytes(value))
    except (UnicodeError, ValueError, RecursionError):
        fail("payload")
    if size > 262144:
        fail("bounds")
    _semantics(value)
