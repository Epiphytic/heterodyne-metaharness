"""Strict, exact-byte-safe Plan9 codec. Refusals never imply a live clock or approval."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, cast, get_args, overload

import msgspec

from . import models as m

MAX_BYTES = 8 * 1024 * 1024
MAX_VALUES = 200_000


class Hold(ValueError):
    """A fixed evidence/proof failure; never authorization to repair live state."""


class InvalidEvidence(ValueError):
    """Malformed or noncanonical evidence, with a fixed diagnostic code."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence("duplicate_key")
        result[key] = value
    return result


def _no_number(_: str) -> object:
    raise InvalidEvidence("noninteger_number")


def load(raw: bytes) -> object:
    if len(raw) > MAX_BYTES:
        raise InvalidEvidence("evidence_too_large")
    try:
        value: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_no_number, parse_constant=_no_number
        )
        _budget(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, InvalidEvidence):
            raise
        raise InvalidEvidence("invalid_json") from None


def _budget(value: object) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_VALUES or depth > 64:
            raise InvalidEvidence("evidence_too_complex")
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise InvalidEvidence("invalid_unicode") from None
        elif isinstance(item, dict):
            for key, child in cast(dict[object, object], item).items():
                if not isinstance(key, str):
                    raise InvalidEvidence("nonstring_key")
                pending.extend(((key, depth + 1), (child, depth + 1)))
        elif isinstance(item, (tuple, list)):
            pending.extend((child, depth + 1) for child in cast(list[object], item))
        elif item is not None and type(item) not in (int, bool):
            raise InvalidEvidence("non_json_value")


def canonical(value: object) -> bytes:
    built = msgspec.to_builtins(value)
    _budget(built)
    try:
        return json.dumps(
            built, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise InvalidEvidence("non_json_value") from None


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def utc_ns(value: str) -> int:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
            raise ValueError
        delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
    except ValueError:
        raise InvalidEvidence("invalid_utc") from None


def native_utc(value: str) -> str:
    try:
        match = re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.(?P<fraction>[0-9]+))?(?:Z|[+-][0-9]{2}:[0-9]{2})",
            value,
        )
        if match is None:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        normalized = parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, OverflowError, UnicodeError):
        raise InvalidEvidence("invalid_native_time") from None
    # Calendar and offset are valid; never return datetime's truncated fraction.
    if len(match.group("fraction") or "") > 6:
        raise Hold("unsupported_native_precision")
    return normalized


def _semantic(value: object, annotation: object = None) -> None:
    aliases = (annotation, *get_args(annotation))
    if value is None:
        return
    if m.NS in aliases and (not isinstance(value, str) or int(value) > 18446744073709551615):
        raise InvalidEvidence("nanosecond_overflow")
    if m.UTC in aliases:
        utc_ns(str(value))
    if isinstance(value, msgspec.Struct):
        for field in msgspec.structs.fields(type(value)):
            _semantic(getattr(value, field.name), field.type)
    elif isinstance(value, (list, tuple)):
        for item in cast(tuple[object, ...], value):
            _semantic(item)


@overload
def decode[T](raw: bytes, expected: type[T]) -> T: ...


@overload
def decode(raw: bytes, expected: object) -> Any: ...


def decode(raw: bytes, expected: Any) -> Any:
    try:
        value = msgspec.convert(load(raw), type=expected, strict=True)
        _semantic(value, expected)
        return value
    except (msgspec.ValidationError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, InvalidEvidence):
            raise
        raise InvalidEvidence("invalid_schema") from None


def validate[T](value: T) -> T:
    return decode(canonical(value), type(value))
