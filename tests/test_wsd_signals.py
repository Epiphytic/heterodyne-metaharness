"""Plan 4b Task 1: the runtime's signal seam and the trusted-read seam, before anything produces them."""

from pathlib import Path
from typing import cast

import msgspec
import pytest
from fakes.fake_runtime import FakeRuntime

from heterodyne.sandbox.runtime import SandboxRuntime
from heterodyne.wsd.runtime import (
    KEY_CHANGED,
    LOGIN_STALE,
    AgentRuntime,
    EventBatch,
    LaunchSpec,
    LimitSeen,
    NoRuntime,
    SessionEvent,
    SteerRefused,
    SteerUncertain,
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


def event(kind: str = "stop", n: int = 1, gen: int = 1, key: str = "sk-a", **kw: object) -> SessionEvent:
    base: dict[str, object] = {"event_id": f"{key}:g{gen}:e{n}", "kind": kind, "ws": "alpha", "bead": "btq-1",
                               "session_key": key, "generation": gen, "turn": n, "text": None, "body": {},
                               "close_requested": False}
    return SessionEvent(**(base | kw))  # type: ignore[arg-type]


def test_a_spec_has_no_brief_by_default(tmp_path: Path) -> None:
    spec = LaunchSpec("alpha", "btq-1", "coder", "p-one", "sk", "label", tmp_path, False)
    assert spec.brief == ""


def test_a_session_event_round_trips_through_json_and_knows_its_seq() -> None:
    ev = event("question", n=12, body={"text": "which one?", "choices": ["a", "b"]})
    assert msgspec.json.decode(msgspec.json.encode(ev), type=SessionEvent) == ev
    assert ev.seq == 12


def test_no_runtime_has_no_events_refuses_to_steer_and_takes_a_subscription() -> None:
    rt = NoRuntime()
    rt.subscribe(lambda _ws: None, {"alpha": frozenset({"close"})})
    assert rt.session_events("alpha", {}, 10) == EventBatch()
    with pytest.raises(SteerRefused):
        rt.steer("sk", 1, "hello")


def test_the_fake_runtime_records_steers() -> None:
    rt = FakeRuntime()
    rt.steer("sk-a", 1, "look again")
    assert rt.steers == [("sk-a", 1, "look again")]
    rt.steer_error = SteerUncertain("maybe")
    with pytest.raises(SteerUncertain):
        rt.steer("sk-a", 1, "again")
    assert rt.steers == [("sk-a", 1, "look again")]          # an uncertain steer isn't recorded as sent


def test_the_fake_runtime_serves_its_logs_after_a_cursor() -> None:
    rt = FakeRuntime()
    woken: list[str] = []
    rt.subscribe(woken.append, {"alpha": frozenset({"ask"})})
    rt.event("alpha", event(n=1), event(n=2))
    rt.report("alpha", event(n=2), event(n=3))              # a resent event is logged once
    assert woken == ["alpha"] and rt.subscribed == {"alpha": frozenset({"ask"})}
    batch = rt.session_events("alpha", {"sk-a": (1, 1)}, 10)
    assert [e.event_id for e in batch.events] == ["sk-a:g1:e2", "sk-a:g1:e3"] and batch.ends == {}
    assert rt.session_events("alpha", {}, 2) == EventBatch((event(n=1), event(n=2)), {}, True)
    rt.event("alpha", event(n=1, gen=3))                      # a later generation: 1 and 2 are final
    batch = rt.session_events("alpha", {"sk-a": (1, 3)}, 10)
    assert batch.events == (event(n=1, gen=3),) and batch.ends == {("sk-a", 1): 3, ("sk-a", 2): 0}
    rt.end_log("sk-a", 3)
    assert rt.session_events("alpha", {"sk-a": (3, 1)}, 10).ends == {("sk-a", 3): 1}
    assert rt.session_events("beta", {}, 10) == EventBatch()
