"""Deterministic identities (ADR 0001 §4.1, §4.3). Every key here is recomputed, never stored as truth,
so `wsd` finds its own claims and sessions again after a restart or a lost journal."""

import re
import uuid

# The namespace of every wsd uuid5. Changing it orphans every claim and session wsd holds: never change it.
NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd")
SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # btq's routing and session slug rule
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
ROLE_LABEL = "role:"


class BadName(ValueError):
    """A workstream, bead, role or profile name that is not a valid slug."""


def slug(value: str, what: str) -> str:
    if not SLUG.fullmatch(value):
        raise BadName(f"{what} is not a valid slug")
    return value


def ws_session(ws: str) -> str:
    """The workstream-session btq worker: it lists ready work and owns the shared pause flag."""
    return str(uuid.uuid5(NS, slug(ws, "workstream")))


def bead_session(ws: str, bead: str) -> str:
    """The per-bead btq worker that claims, owns and parks one bead."""
    return str(uuid.uuid5(NS, f"{slug(ws, 'workstream')}:{slug(bead, 'bead')}"))


def role_session(bead: str, role: str, profile: str) -> str:
    """The logical agent session key for (bead, role, profile) (§4.1)."""
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return str(uuid.uuid5(NS, f"{slug(bead, 'bead')}:{slug(role, 'role')}:{profile}"))


def profile_for(labels: tuple[str, ...], role: str, default: str) -> str:
    """The profile for `role` on a bead: a single `role:<role>=<profile>` label overrides the configured
    default (§4.1, §15 layer 4). Two different overrides for the same role are ambiguous: refused."""
    prefix = f"{ROLE_LABEL}{role}="
    chosen = {label[len(prefix):] for label in labels if label.startswith(prefix)}
    if len(chosen) > 1:
        raise BadName(f"bead has conflicting role:{role} labels")
    profile = chosen.pop() if chosen else default
    if not PROFILE.fullmatch(profile):
        raise BadName("profile is not a valid name")
    return profile
