"""Exact retained-conversation qualification without lifecycle effects.

Only the already owning Product runtime calls this. A successful observation is not authority to
end or launch, and cannot stand in for Hermes' native admission or recovery evidence. Install
verification may retain its normal integrity alerts/taints; credential checks take their normal
short-lived lock but never refresh a login. Other records' WIP is never settled here.
"""

from typing import TYPE_CHECKING

from heterodyne.wsd.runtime import LaunchFailed, LaunchSpec

if TYPE_CHECKING:
    from heterodyne.sandbox.runtime import SessionRecord


def validate_identity(spec: LaunchSpec, rec: "SessionRecord") -> None:
    recorded = (rec.key, rec.ws, rec.bead, rec.role, rec.profile, rec.native_id,
                rec.worktree, rec.repo, rec.install_name, rec.install_digest)
    requested = (spec.session_key, spec.ws, spec.bead, spec.role, spec.profile, spec.native_id,
                 str(spec.worktree), str(spec.repo), spec.install, spec.install_digest)
    if (requested != recorded or not all(recorded) or not rec.ran or rec.started_at <= 0
            or spec.generation != rec.generation + 1 or not spec.resume or spec.handoff
            or rec.phase not in ("running", "ended")):
        raise LaunchFailed("resume preflight requires the exact retained next-generation identity")
