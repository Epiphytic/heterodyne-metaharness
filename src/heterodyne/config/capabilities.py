"""Per-adapter account and usage capabilities that S7 must demonstrate (ADR 0001 §4.4 D9)."""

from collections.abc import Mapping
from dataclasses import dataclass


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


# Empty: S7 demonstrated nothing on a real account. AU-6 and AU-7 add entries from accepted findings.
CAPABILITIES: Mapping[str, Capabilities] = {}
NONE = Capabilities()

# Login file set per adapter, relative to its login directory (S7 (1), client side shown). Used only for
# credential identities and alias checks; it enables nothing.
LOGIN_FILES: Mapping[str, tuple[str, ...]] = {"claude-code": (".credentials.json",), "codex": ("auth.json",)}
# The implicit `default` account's login directory. CLAUDE_CONFIG_DIR and CODEX_HOME are deliberately not
# read: the environment can't select a login (D1).
DEFAULT_LOGIN_DIRS: Mapping[str, str] = {"claude-code": "~/.claude", "codex": "~/.codex"}
