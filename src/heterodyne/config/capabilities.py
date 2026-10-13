"""Per-adapter account and usage capabilities that S7 must demonstrate (ADR 0001 §4.4 D9)."""

from collections.abc import Mapping
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Capabilities:
    """What S7 demonstrated for one adapter on its pinned CLI version (ADR §4.4 D9)."""
    login_binding: bool = False          # (a)
    cross_account_resume: bool = False   # (b)
    in_session_usage: bool = False       # (c)
    trusted_read: bool = False           # (d)
    limit_signal: bool = False           # (e)
    handoff_relaunch: bool = False       # (f)

    @property
    def can_switch(self) -> bool:
        """D7: moving a session to another account needs (b) or (f)."""
        return self.cross_account_resume or self.handoff_relaunch


@dataclass(frozen=True)
class Evidence:
    """The S7 real-account runs that demonstrated one capability, and the CLI version they ran on (D9)."""
    runs: str            # S7's run ids, e.g. "L1,L2"
    version: str         # must equal the adapter's version pin


# Each entry is enabled by its own commit, after the G2 bead records the operator's acceptance of the
# finding; the commit message names the G2 bead and the S7 commit (plan 4b Task 11 Part B).
CAPABILITIES: Mapping[str, Capabilities] = {}
EVIDENCE: Mapping[str, Mapping[str, Evidence]] = {}
NONE = Capabilities()
FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Capabilities))

PROBLEM_UNKNOWN_ADAPTER = "no pinned adapter"
PROBLEM_NO_EVIDENCE = "enabled without evidence"
PROBLEM_STALE_EVIDENCE = "evidence for a capability that is off"
PROBLEM_WRONG_VERSION = "evidence from another CLI version"
PROBLEM_NEEDS_BINDING = "enabled without (a) login binding"
PROBLEM_NO_SOURCE = "claude-code has no (d) source"
PROBLEM_NO_GENERATOR = "(f) needs the handoff generator"


def table_problems(table: Mapping[str, Capabilities], evidence: Mapping[str, Mapping[str, Evidence]],
                   pins: Mapping[str, str]) -> tuple[str, ...]:
    """The D9 rules every table edit keeps (plan 4b Task 11). Each line names an adapter and a field."""
    found: list[str] = []
    for adapter in sorted(set(table) | set(evidence)):
        caps, shown = table.get(adapter, NONE), evidence.get(adapter, {})
        if adapter not in pins:
            found.append(f"{PROBLEM_UNKNOWN_ADAPTER}: {adapter}")
            continue
        on = {name for name in FIELDS if getattr(caps, name)}
        found += [f"{PROBLEM_NO_EVIDENCE}: {adapter}.{name}" for name in sorted(on - set(shown))]
        found += [f"{PROBLEM_STALE_EVIDENCE}: {adapter}.{name}" for name in sorted(set(shown) - on)]
        found += [f"{PROBLEM_WRONG_VERSION}: {adapter}.{name}" for name in sorted(shown)
                  if shown[name].version != pins[adapter]]
        if on and not caps.login_binding:
            found.append(f"{PROBLEM_NEEDS_BINDING}: {adapter}")
        if adapter == "claude-code" and caps.trusted_read:
            found.append(f"{PROBLEM_NO_SOURCE}: {adapter}.trusted_read")
        if caps.handoff_relaunch:
            found.append(f"{PROBLEM_NO_GENERATOR}: {adapter}.handoff_relaunch")
    return tuple(found)

# Login file set per adapter, relative to its login directory (S7 (1), client side shown). Used only for
# credential identities and alias checks; it enables nothing.
LOGIN_FILES: Mapping[str, tuple[str, ...]] = {"claude-code": (".credentials.json",), "codex": ("auth.json",)}
# The implicit `default` account's login directory. CLAUDE_CONFIG_DIR and CODEX_HOME are deliberately not
# read: the environment can't select a login (D1).
DEFAULT_LOGIN_DIRS: Mapping[str, str] = {"claude-code": "~/.claude", "codex": "~/.codex"}
