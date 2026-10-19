"""Links on question, merge and notice cards (cxmkf K20, K22l). Pure.

A link is a label and a GitHub URL in one of a few pinned forms: a PR, a run or an issue by number, or a
blob, tree, commit or compare pinned to 40-hex commits. The daemon checks the form only and never fetches
the URL. A card shows each link on its own line, `<label>: <url>`, the URL last, so client linkifiers find
its end; a link line is never cut across chunks.
"""

import re

import msgspec

from heterodyne.admind.redact import redact

MAX_LINKS = 8
MAX_LABEL = 40
MAX_URL = 400
LINKS_HEAD = "Links:"

_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")     # as asks._TITLE_CONTROLS
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"(?!\.{1,2}/)[A-Za-z0-9._-]{1,100}"
_SHA = r"[0-9a-f]{40}"
_SEGMENT = r"(?!\.{1,2}(?:/|\Z))(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+"
_PATH = rf"{_SEGMENT}(?:/{_SEGMENT})*"
_FORMS = (rf"pull/[1-9][0-9]{{0,9}}(?:/files|/commits/{_SHA})?", rf"blob/{_SHA}/{_PATH}",
          rf"tree/{_SHA}/{_PATH}", rf"commit/{_SHA}", rf"compare/{_SHA}\.\.\.{_SHA}",
          r"actions/runs/[1-9][0-9]{0,19}", r"issues/[1-9][0-9]{0,9}")
PINNED = re.compile(rf"https://github\.com/{_OWNER}/{_REPO}/(?:{'|'.join(_FORMS)})")

PINNED_BLOB = re.compile(rf"https://github\.com/{_OWNER}/{_REPO}/blob/{_SHA}/{_PATH}")

FORMAT = "--link must be a GitHub URL pinned to a 40-hex commit, or a PR, run or issue"
LABEL = f"a --link label must be one line of 1-{MAX_LABEL} characters"
TOO_LONG = f"a --link URL is over {MAX_URL} characters"
TOO_MANY = f"at most {MAX_LINKS} --link and --ref in all"
REDACTED = "a --link would be redacted; remove it"


class LinkSpec(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    label: str
    url: str


def line(link: LinkSpec) -> str:
    return f"{link.label}: {link.url}"


def why(link: LinkSpec) -> str | None:
    """The K9 code a link is refused for (`link-label`, `link-too-long`, `link-format`, `link-redacted`), or
    None. Never built from the link's text."""
    if not 1 <= len(link.label) <= MAX_LABEL or not link.label.strip() or _CONTROLS.search(link.label):
        return "link-label"
    if len(link.url) > MAX_URL:
        return "link-too-long"
    if not PINNED.fullmatch(link.url):
        return "link-format"
    if redact(line(link)) != line(link):
        return "link-redacted"
    return None


MESSAGES = {"link-label": LABEL, "link-too-long": TOO_LONG, "link-format": FORMAT, "link-redacted": REDACTED,
            "too-many-links": TOO_MANY}


def check(links: list[LinkSpec]) -> str | None:
    """The first K9 code `links` are refused for (`too-many-links` or a link's own), or None."""
    if len(links) > MAX_LINKS:
        return "too-many-links"
    return next((w for w in map(why, links) if w is not None), None)


def block(links: list[LinkSpec]) -> list[str]:
    """The `Links:` block's lines, or none."""
    return [LINKS_HEAD, *map(line, links)] if links else []
