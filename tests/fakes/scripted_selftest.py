"""A stand-in for the OpenShell self-test. Its exec path records and can be made to fail; its agent path
does what the real one needs of the agent (waits for the prompt, submits one line, waits for its Stop),
so Codex fires SessionStart and reports its thread ID, as with the real probe prompt."""

import time
from collections.abc import Callable, Mapping

from heterodyne.sandbox.selftest import ProbeContext, SelfTestFailed

WAIT_SECONDS = 20


def _wait(pred: Callable[[], bool]) -> None:
    end = time.monotonic() + WAIT_SECONDS
    while not pred():
        if time.monotonic() > end:
            raise SelfTestFailed("scripted-agent-path: no reply")
        time.sleep(0.02)


class ScriptedSelfTest:
    def __init__(self) -> None:
        self.exec_fail = ""
        self.agent_fail = ""
        self.exec_runs: list[str] = []
        self.agent_runs: list[str] = []
        self.during_agent: Callable[[], None] | None = None     # e.g. a test clock advanced: a slow start
        self.during_exec: Callable[[], None] | None = None      # runs once the sandbox exists

    def files(self) -> Mapping[str, str]:
        return {"probes.py": "# scripted self-test: no probes\n"}

    def exec_path(self, ctx: ProbeContext) -> None:
        self.exec_runs.append(ctx.spec.name)
        if self.during_exec is not None:
            self.during_exec()
        if self.exec_fail:
            raise SelfTestFailed(self.exec_fail)

    def agent_path(self, ctx: ProbeContext) -> None:
        self.agent_runs.append(ctx.spec.name)
        if self.agent_fail:
            raise SelfTestFailed(self.agent_fail)
        if self.during_agent is not None:
            self.during_agent()
        _wait(lambda: ctx.adapter.prompt_marker in ctx.tmux.capture(ctx.tmux_session, 50))
        before = ctx.turns().stops
        ctx.tmux.paste(ctx.tmux_session, "self-test")
        _wait(lambda: ctx.turns().stops > before)
