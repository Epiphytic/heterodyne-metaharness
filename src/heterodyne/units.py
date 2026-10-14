"""What the platform's systemd user units share (ADR 0001 §15, §16; design btq-v7sgt D3): the value checks,
and an environment block that pins the locations the installer checked, whatever the user manager's own
environment holds. systemd applies UnsetEnvironment= after Environment= (systemd.exec(5)), so no name is in
both lists."""

import argparse
import hashlib
import re
import sys
from collections.abc import Callable, Mapping

from heterodyne.config.secret_scan import sensitive_kind, show

UNSET = ("BTQ_REPO", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")
REV_KEY = "HETERODYNE_UNIT_REV"
MARKER_PREFIX = "# Rendered by heterodyne's deploy/install.sh for release "
EX_CONFIG = 78
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_UNSAFE = set(" \t\n\"'\\")
_REV_LINE = f"Environment={REV_KEY}="
_REV_PLACEHOLDER = "@REV@"


def _control(ch: str) -> bool:
    return ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F


def check(name: str, value: str) -> None:
    """Refuse a value systemd would split or unquote, or one holding sensitive material, naming only
    `name`."""
    if not value:
        raise ValueError(f"{name} is empty")
    if _UNSAFE & set(value) or any(_control(ch) for ch in value):
        raise ValueError(f"{name} contains whitespace, a quote, a backslash or a control "
                         "character; systemd would split or unquote it")
    kind = sensitive_kind(value)
    if kind:  # the unit must be installable verbatim, so refuse rather than redact
        raise ValueError(f"{name} contains sensitive material ({kind}), which must not be printed; "
                         "use a path without it")


def rev(text: str) -> str:
    """The unit revision: the first 12 hex digits of the render's sha256 without its revision line."""
    body = "".join(line for line in text.splitlines(keepends=True) if not line.startswith(_REV_LINE))
    return hashlib.sha256(body.encode()).hexdigest()[:12]


def render(template: str, python: str, *, path: str, config_dir: str, state_dir: str,
           release: str | None = None) -> str:
    """The unit text: `template` with ExecStart's interpreter and the environment block. `%` is escaped
    for systemd. With `release` (the installer's form) the first line is the installer's marker."""
    env = {"PATH": path, "HETERODYNE_CONFIG_DIR": config_dir, "HETERODYNE_STATE_DIR": state_dir}
    for name, value in {"python": python, **env}.items():
        check(name, value)
    if release is not None:
        if not _COMMIT.fullmatch(release):
            raise ValueError("HETERODYNE_RELEASE is not a full commit ID")
        env["HETERODYNE_RELEASE"] = release
    environment = "".join(f"Environment={k}={v.replace('%', '%%')}\n" for k, v in env.items())
    environment += f"{_REV_LINE}{_REV_PLACEHOLDER}\nUnsetEnvironment={' '.join(UNSET)}\n"
    text = template.format(python=python.replace("%", "%%"), environment=environment)
    if release is not None:
        text = f"{MARKER_PREFIX}{release}; the next install replaces it.\n{text}"
    return text.replace(_REV_PLACEHOLDER, rev(text))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The renderers' flags. The installer gives all four; by hand, the defaults are this interpreter and
    the locations heterodyne resolves from the environment."""
    parser.add_argument("--python", help="the interpreter for ExecStart (default: this one)")
    parser.add_argument("--config-dir", help="HETERODYNE_CONFIG_DIR for the unit")
    parser.add_argument("--state-dir", help="HETERODYNE_STATE_DIR for the unit")
    parser.add_argument("--release", help="the release's commit (the installer's form)")


Renderer = Callable[..., str]


def command(render_unit: Renderer, args: argparse.Namespace, env: Mapping[str, str]) -> int:
    """Print the unit `render_unit(python, env, config_dir=, state_dir=, release=)` renders; 78 with one
    line naming the key when a value can't go in a unit."""
    try:
        text = render_unit(args.python or sys.executable, env, config_dir=args.config_dir,
                           state_dir=args.state_dir, release=args.release)
    except ValueError as exc:
        print(f"cannot render the unit: {show(str(exc), False)}", file=sys.stderr)
        return EX_CONFIG
    print(text, end="")
    return 0
