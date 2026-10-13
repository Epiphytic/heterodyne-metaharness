"""Plan 4b Task 1: the runtime's signal seam and the trusted-read seam, before anything produces them."""

from pathlib import Path
from typing import cast

from fakes.fake_runtime import FakeRuntime

from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd.runtime import (
    KEY_CHANGED,
    LOGIN_STALE,
    AgentRuntime,
    LaunchSpec,
    LimitSeen,
    NoRuntime,
    UsageSeen,
)
from heterodyne.wsd.usage import NoUsageReader, Observation, TrustedRead

KEY = "ck1-" + "a" * 32


def test_a_spec_pins_no_key_and_no_handoff_by_default(tmp_path: Path) -> None:
    spec = LaunchSpec("alpha", "btq-1", "coder", "p-one", "sk", "label", tmp_path, False)
    assert spec.credential_key == "" and spec.handoff is False


def test_the_refusal_texts_are_fixed_and_distinct() -> None:
    assert LOGIN_STALE == "the login expires within the session's maximum lifetime; refresh it on the host"
    assert KEY_CHANGED != LOGIN_STALE and "ck1-" not in KEY_CHANGED


def test_no_runtime_has_no_signals() -> None:
    assert NoRuntime().signals("alpha") == []


def test_the_sandbox_runtime_has_the_seam() -> None:
    assert "signals" in vars(SandboxRuntime)               # until Task 7, an empty drain; pyright checks
    runtime: AgentRuntime = cast(SandboxRuntime, None)     # that the class satisfies the protocol
    assert runtime is None


def test_the_fake_runtime_drains_each_workstream_once() -> None:
    rt = FakeRuntime()
    usage = UsageSeen("sk", 1, (Observation("w5h", "5h", 50.0, None, "codex.app-server"),))
    rt.report("alpha", usage, LimitSeen("sk", 1, None))
    rt.report("beta", LimitSeen("sk-b", 2, 1_800_000_000))
    assert rt.signals("alpha") == [usage, LimitSeen("sk", 1, None)]
    assert rt.signals("alpha") == []
    assert rt.signals("beta") == [LimitSeen("sk-b", 2, 1_800_000_000)]
    assert rt.drains == ["alpha", "alpha", "beta"]


def test_no_usage_reader_never_confirms() -> None:
    assert NoUsageReader().read("p-one", "default", KEY) is None
    assert TrustedRead((), limited=True, reset_hint=None).limited
