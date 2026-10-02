"""The `claude-code` adapter's interactive launch shape (ADR 0001 §4.2; Claude Code 2.1.x flags)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast


def interactive_argv(binary: str, profile: Mapping[str, Any], *, session_id: str, resume: bool,
                     settings_file: Path, name: str) -> list[str]:
    """Launch (or resume) an interactive session with a fixed ID, hooks from `settings_file`, and
    permission prompts bypassed. The profile's `args` are appended verbatim."""
    argv = [binary, "--resume" if resume else "--session-id", session_id]
    model = profile.get("model")
    if isinstance(model, str) and model:
        argv += ["--model", model]
    argv += ["--permission-mode", "bypassPermissions", "--settings", str(settings_file), "--name", name]
    args = profile.get("args", [])
    if isinstance(args, list):
        argv += [str(a) for a in cast(list[Any], args)]
    return argv
