"""Reading an install's `defaults/` and content files, and writing the merged result (plan 4c E4, E9, E10).
Every read is by fd from the install root without following a link, and bounded. The launcher's keys are
dropped from what an install gives and then written over it, so its values always win (§7)."""

import errno
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_FILE = 1 << 20              # one defaults or content file; at the limit is accepted
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def open_dir_at(dirfd: int | None, path: str | Path) -> int:
    """`path` (one component when `dirfd` is given) as a directory, never through a link. Raises OSError."""
    return os.open(path, DIR_FLAGS) if dirfd is None else os.open(path, DIR_FLAGS, dir_fd=dirfd)


def open_under(root: Path, parts: Sequence[str]) -> int | None:
    """<root>/<parts...> as a directory, each component opened from the one before without following a
    link; None if a component is missing. Raises OSError for anything else (a link, a file)."""
    fd = open_dir_at(None, root)
    try:
        for part in parts:
            try:
                inner = open_dir_at(fd, part)
            except FileNotFoundError:
                os.close(fd)
                return None
            os.close(fd)
            fd = inner
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_bounded_at(dirfd: int, name: str) -> str | None:
    """The regular file `name` in `dirfd` as strict UTF-8, or None if missing. Raises OSError for a link,
    a FIFO, anything that isn't a regular file, or a file over MAX_FILE; ValueError for bad UTF-8."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
            raise OSError(errno.EINVAL, "not a regular file the adapter reads")
        data = fh.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise OSError(errno.EFBIG, "too large")
    return data.decode("utf-8")


def has_entry(dirfd: int, name: str) -> bool:
    """`name` exists in `dirfd`, whatever it is (a link is not followed)."""
    try:
        os.lstat(name, dir_fd=dirfd)
    except FileNotFoundError:
        return False
    return True


def without(tree: Mapping[str, Any], owned: frozenset[str]) -> dict[str, Any]:
    """A deep copy of `tree` with every dotted key of `owned` removed."""
    out = copy_tree(tree)
    for key in owned:
        *parents, leaf = key.split(".")
        here: Any = out
        for p in parents:
            here = cast(dict[str, Any], here).get(p) if isinstance(here, dict) else None
        if isinstance(here, dict):
            cast(dict[str, Any], here).pop(leaf, None)
    return out


def copy_tree(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: copy_tree(v) for k, v in cast(Mapping[str, Any], value).items()}
    if isinstance(value, list):
        return [copy_tree(v) for v in cast(list[Any], value)]
    return value


def merge(base: dict[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """`top` deep-merged over `base`, in place: tables merge, and anything else in `top` replaces."""
    for key, value in top.items():
        here = base.get(key)
        if isinstance(value, Mapping) and isinstance(here, dict):
            merge(cast(dict[str, Any], here), cast(Mapping[str, Any], value))
        else:
            base[key] = copy_tree(value)
    return base


# --- a TOML writer for the values tomllib reads, without dates and times ---

def _key(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else _string(key)


def _string(value: str) -> str:
    # JSON string escapes are TOML basic-string escapes; TOML also wants DEL escaped.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return ("inf" if value > 0 else "-inf") if math.isinf(value) else repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in cast(list[Any], value)) + "]"
    if isinstance(value, Mapping):
        items = cast(Mapping[str, Any], value).items()
        return "{" + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in items) + "}"
    raise ValueError("a value TOML output doesn't take")


def toml_text(tree: Mapping[str, Any]) -> str:
    """`tree` as TOML: each table's plain keys, then its sub-tables under a dotted header, in order.
    Raises ValueError for a value it can't write (a date or time)."""
    lines: list[str] = []

    def table(here: Mapping[str, Any], header: tuple[str, ...]) -> None:
        subs = [(k, cast(Mapping[str, Any], v)) for k, v in here.items() if isinstance(v, Mapping)]
        plain = [(k, v) for k, v in here.items() if not isinstance(v, Mapping)]
        if header and (plain or not subs):
            lines.append("[" + ".".join(_key(h) for h in header) + "]")
        lines.extend(f"{_key(k)} = {_value(v)}" for k, v in plain)
        for k, v in subs:
            table(v, (*header, k))

    table(tree, ())
    return "\n".join(lines) + "\n"
