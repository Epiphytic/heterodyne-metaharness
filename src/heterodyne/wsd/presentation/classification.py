"""Finite ingress classification. Results describe routing, never authorize it."""

import unicodedata

from heterodyne.wsd.ids import SLUG

from .commands import MUTATIONS, READ_COMMANDS
from .dto import (
    Classification,
    ClassificationInput,
    ClassificationResult,
    Invalid,
    ReactionAdded,
    ReactionRemoved,
    Text,
)

# Unicode 15.0 White_Space property, independent of host Unicode tables.
_WHITE = frozenset(
    "\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def _command(body: str) -> Classification:
    if any((c in _WHITE and c != " ") or unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in body):
        return Classification("InvalidCommand", "multiline-or-control")
    tokens = body.strip(" ").split(" ")
    tokens = [token for token in tokens if token]
    name, args = tokens[0], tokens[1:]
    if name in MUTATIONS:
        return Classification("UnsupportedIn6A", "mutation")
    if name not in READ_COMMANDS:
        return Classification("InvalidCommand", "unknown-command")
    maximum = 1 if name in ("/status", "/queue") else 0
    if len(args) > maximum or any(SLUG.fullmatch(arg) is None for arg in args):
        return Classification("InvalidCommand", "arguments")
    query = {
        "/workstreams": "Workstreams",
        "/status": "Status",
        "/approvals": "Approvals",
        "/queue": "Queue",
    }[name]
    if args:
        query = "BeadStatus" if name == "/status" else "QueueFor"
    return Classification("ReadQuery", query, args[0] if args else None)


def classify(value: ClassificationInput) -> ClassificationResult:
    """Validate the header before considering an untrusted or deliberately malformed body."""
    if (
        type(value) is not ClassificationInput
        or type(value.version) is not int
        or value.version != 1
        or value.surface not in ("workstream", "control")
        or value.ingress not in ("unverified", "rejected", "replayed", "verified")
        or type(value.suspended) is not bool
        or value.placement not in ("root", "thread")
        or value.target
        not in ("none", "stale", "unknown", "current-task", "current-approval", "current-question")
        or (value.placement == "root" and value.target != "none")
    ):
        return Invalid("schema")
    if value.ingress != "verified":
        return Classification("Ignored", value.ingress)
    event = value.event
    if type(event) not in (Text, ReactionAdded, ReactionRemoved):
        return Invalid("payload")
    text = event.body if isinstance(event, Text) else event.glyph
    if type(text) is not str:
        return Invalid("payload")
    try:
        if len(text.encode("utf-8")) > 262144:
            return Invalid("payload")
    except UnicodeError:
        return Invalid("payload")
    if isinstance(event, ReactionRemoved):
        return Classification("Ignored", "reaction-removal-unproven")
    if isinstance(event, ReactionAdded):
        if value.target == "current-approval":
            return (
                Classification("Ignored", "trust-suspended")
                if value.suspended
                else Classification("DeferredDecision", "native-reaction")
            )
        return Classification(
            "Ignored",
            "question-reaction-unsupported" if value.target == "current-question" else "unhandled-reaction",
        )
    candidate = text.lstrip("".join(sorted(_WHITE)))
    if candidate.startswith("/"):
        return _command(text)
    if value.suspended and value.target == "current-approval":
        return Classification("Ignored", "trust-suspended")
    if value.placement == "root":
        return (
            Classification("DeferredIntake", "workstream-text")
            if value.surface == "workstream"
            else Classification("Ignored", "control-noncommand")
        )
    if value.target.startswith("current-"):
        return Classification("DeferredSteering", "thread-text")
    return Classification("UnresolvedTarget", "missing" if value.target == "none" else value.target)
