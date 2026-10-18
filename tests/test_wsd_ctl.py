"""Plan 4c Task 10: the control socket's `harness` op. `refusal` answers "" for a request the handler can
serve and a reason for any other; the wire form decodes strictly."""

import msgspec
import pytest

from heterodyne.wsd import ctl

INSTALL = "0.160.0-0123456789ab"


def harness(action: ctl.HarnessAction, **kw: str) -> ctl.CtlRequest:
    return ctl.CtlRequest("harness", harness=ctl.HarnessRequest(action, **kw))


@pytest.mark.parametrize("req", [
    harness("list"),
    harness("list", adapter="codex"),
    harness("install", adapter="codex"),
    harness("install", adapter="codex", version="0.160.0"),
    harness("verify", adapter="codex", install=INSTALL),
    harness("use", adapter="codex", install=INSTALL),
    harness("rollback", adapter="codex"),
    harness("remove", adapter="codex", install=INSTALL),
    harness("prune"),
    harness("prune", confirm="0a1b2c3d"),
], ids=lambda r: r.harness.action if r.harness else "none")
def test_a_well_formed_harness_request_is_served(req: ctl.CtlRequest) -> None:
    assert ctl.refusal(req) == ""


@pytest.mark.parametrize("req", [
    ctl.CtlRequest("harness"),                                              # no harness request
    ctl.CtlRequest("status", harness=ctl.HarnessRequest("list")),           # on another op
    ctl.CtlRequest("harness", ws="alpha", harness=ctl.HarnessRequest("list")),     # takes no workstream
    ctl.CtlRequest("harness", all=True, harness=ctl.HarnessRequest("list")),
    harness("install"),                                                     # no adapter
    harness("verify", adapter="codex"),                                     # no install
    harness("verify", install=INSTALL),
    harness("use", adapter="codex"),
    harness("remove", adapter="codex"),
    harness("rollback"),
    harness("rollback", adapter="codex", install=INSTALL),                  # rollback takes no install
    harness("list", install=INSTALL),
    harness("list", confirm="0a1b2c3d"),
    harness("install", adapter="codex", install=INSTALL),
    harness("use", adapter="codex", install=INSTALL, version="0.160.0"),    # only install takes a version
    harness("use", adapter="codex", install=INSTALL, confirm="0a1b2c3d"),   # only prune takes a code
    harness("prune", adapter="codex"),                                      # prune takes nothing else
    harness("prune", install=INSTALL),
], ids=str)
def test_a_malformed_harness_request_is_refused(req: ctl.CtlRequest) -> None:
    assert ctl.refusal(req) != ""


def test_the_harness_request_decodes_strictly() -> None:
    wire = (b'{"op":"harness","harness":{"action":"use","adapter":"codex","install":"' + INSTALL.encode()
            + b'"}}')
    req = msgspec.json.decode(wire, type=ctl.CtlRequest)
    assert req.harness == ctl.HarnessRequest("use", adapter="codex", install=INSTALL)
    for bad in (b'{"op":"harness","harness":{"action":"select","adapter":"codex"}}',
                b'{"op":"harness","harness":{"action":"list","extra":1}}'):
        with pytest.raises(msgspec.ValidationError):
            msgspec.json.decode(bad, type=ctl.CtlRequest)
    assert ctl.refusal(msgspec.json.decode(b'{"op":"status"}', type=ctl.CtlRequest)) == ""


@pytest.mark.parametrize("extra", [
    {"ws": "alpha"}, {"session": "hz0123456789ab"}, {"generation": 1},
    {"tip": "a" * 40}, {"action": "discard"}, {"job": "pickup"}, {"all": True},
])
def test_harness_cannot_import_workstream_operation_fields(extra):
    wire = msgspec.json.encode({"op": "harness", "harness": {"action": "list"}, **extra})
    req = msgspec.json.decode(wire, type=ctl.CtlRequest)
    assert ctl.refusal(req)


def test_settlement_and_harness_wire_contracts_coexist():
    good = ctl.CtlRequest("sandbox-settle", ws="alpha", session="hz0123456789ab", generation=1)
    assert ctl.refusal(msgspec.json.decode(msgspec.json.encode(good), type=ctl.CtlRequest)) == ""
    mixed = msgspec.structs.replace(good, harness=ctl.HarnessRequest("list"))
    assert ctl.refusal(mixed)
    assert ctl.refusal(harness("list")) == ""
