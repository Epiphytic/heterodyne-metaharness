"""Host capability selection, independent daemon lifetimes and disabled protocol."""

import dataclasses
import os

import pytest
from fakes.settings import make_settings
from test_admind_settings import BASE_CONFIG, write

from heterodyne.admind import ctl
from heterodyne.admind import fulfillment as f
from heterodyne.admind.settings import resolve
from heterodyne.config import ConfigError, load
from heterodyne.config.layers import read_defaults


@pytest.mark.parametrize(("literal", "expected"), [(None, False), ("false", False), ("true", True)])
def test_host_boolean_default_and_frozen_instance(tmp_path, literal, expected):
    config = BASE_CONFIG
    if literal is not None:
        config = config.replace("[admind]\n", f"[admind]\nfulfillment_enabled = {literal}\n")
    env = write(tmp_path, config)
    settings = resolve(load(None, env), env)
    assert settings.fulfillment_enabled is expected
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.fulfillment_enabled = not expected  # pyright: ignore[reportAttributeAccessIssue]
    assert make_settings(tmp_path).fulfillment_enabled is False
    assert "fulfillment_enabled" not in read_defaults()["admind"]


@pytest.mark.parametrize("value", [0, 1, "true", "false", 1.0, [], {}, None])
def test_non_boolean_programmatic_config_is_rejected(tmp_path, value):
    env = write(tmp_path)
    config = load(None, env)
    config.values["admind"]["fulfillment_enabled"] = value
    with pytest.raises(ConfigError, match="fulfillment_enabled.*boolean"):
        resolve(config, env)


def test_host_only_no_environment_override_and_omitted_rollback(tmp_path):
    env = write(tmp_path)
    (tmp_path / "workstreams").mkdir()
    (tmp_path / "workstreams" / "w.toml").write_text("[admind]\nfulfillment_enabled = true\n")
    with pytest.raises(ConfigError, match="not allowed in a workstream"):
        load("w", env)
    with pytest.raises(ConfigError) as rejected:
        load(None, {**env, "HETERODYNE_ADMIND_FULFILLMENT_ENABLED": "true"})
    assert str(rejected.value) == (
        "HETERODYNE_ADMIND_FULFILLMENT_ENABLED: environment overrides are limited to "
        "['HETERODYNE_CONFIG_DIR', 'HETERODYNE_LOG_LEVEL', 'HETERODYNE_STATE_DIR']"
    )
    # Removing the host key restores the old closed-key consumer's configuration shape.
    old_keys = frozenset(load(None, env).values["admind"])
    enabled = BASE_CONFIG.replace("[admind]\n", "[admind]\nfulfillment_enabled = true\n")
    env = write(tmp_path, enabled)
    assert resolve(load(None, env), env).fulfillment_enabled is True
    assert set(load(None, env).values["admind"]) - old_keys == {"fulfillment_enabled"}
    env = write(tmp_path)
    assert frozenset(load(None, env).values["admind"]) == old_keys
    assert resolve(load(None, env), env).fulfillment_enabled is False


def test_independent_daemons_disable_get_and_inspect_without_global_bypass(tmp_path):
    from test_admind_r1 import Unit, run

    off = Unit(tmp_path / "off")
    on = Unit(tmp_path / "on", fulfillment_enabled=True)
    try:
        peer = ctl.Peer(123, os.getuid(), os.getgid())
        for request in (f.Get("abcd"), f.Inspect()):
            disabled = run(off.daemon.on_fulfillment(request, peer))
            assert disabled.result == "unavailable"
            assert disabled.message == "capability disabled"
        assert run(on.daemon.on_fulfillment(f.Inspect(), ctl.Peer(123))).result == "refused"
        assert not hasattr(f, "ENABLED")
        assert off.daemon.s.fulfillment_enabled is False
        assert on.daemon.s.fulfillment_enabled is True
    finally:
        off.store.close()
        on.store.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_owned_socket_union_fresh_apply_and_disable_preserves_history(tmp_path, monkeypatch, enabled):
    import asyncio

    from test_admind_fulfillment import completion, declaration, seed
    from test_admind_r1 import Unit, run

    from heterodyne.admind import asks
    from heterodyne.admind.fulfillment_projection import migrate
    from heterodyne.admind.store import Store

    # Prepare projection before the daemon opens; no external migration while serving.
    path = make_settings(tmp_path).state_dir / "admind.db"
    offline = Store(path)
    migrate(offline, apply=True)
    offline.close()
    unit = Unit(tmp_path, fulfillment_enabled=enabled)
    seed(unit.store)
    # Existing durable history predates this daemon selection, including the off case.
    seed(unit.store, "cafe")
    retained = unit.store.fulfillment.apply(completion(unit.store, "cafe"), uid=os.getuid(), pid=123)
    assert retained.result == "applied"
    retained_receipt = unit.store.fulfillment.stored("cafe")
    assert retained_receipt is not None
    before = unit.store.db.execute("SELECT * FROM asks").fetchall()

    async def body():
        started = asyncio.Event()

        async def pause_agent(*, startup):
            started.set()  # run() already constructed and started its genuine ask_server.
            await asyncio.Event().wait()

        monkeypatch.setattr(unit.daemon, "start_agent", pause_agent)
        task = asyncio.create_task(unit.daemon.run())
        try:
            async with asyncio.timeout(5):
                await started.wait()
                server = unit.daemon.ask_server
                assert server is not None
                unit.daemon.latch("fixture")
                request = f.Apply(completion(unit.store))
                assert unit.store.fulfillment.stored("abcd") is None
                async with unit.daemon.work_lock:
                    reply = await ctl.request(server.path, request, 3, reply_type=f.Reply)
                assert reply.result == ("applied" if enabled else "unavailable")
                assert unit.store.db.execute("SELECT * FROM asks").fetchall() == before
                ordinary = await ctl.request(server.path, asks.AskGet("abcd"), 3, reply_type=asks.AskReply)
                assert ordinary.ask is not None and ordinary.ask.summary.status == "answered"
                if enabled:
                    # Dispose a pending notice while enabled, before a separately constructed off daemon.
                    seed(unit.store, "efab")
                    pending = unit.store.fulfillment.propose(
                        f.Propose("efab", declaration(unit.store, "efab")), uid=os.getuid(), chunk_chars=350
                    )
                    assert pending.view is not None
                    disposed = await ctl.request(
                        server.path,
                        f.Dispose("efab", str(pending.view["digest"]), "operator-withdrawal"),
                        3,
                        reply_type=f.Reply,
                    )
                    assert disposed.result == "retired"
                else:
                    for req in (f.Get("abcd"), f.Inspect()):
                        result = await ctl.request(server.path, req, 3, reply_type=f.Reply)
                        assert (result.result, result.message) == ("unavailable", "capability disabled")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    try:
        run(body())
        receipt = unit.store.fulfillment.stored("abcd")
    finally:
        unit.store.close()
    restarted = Unit(tmp_path)  # New immutable off settings, same newest database/history.
    try:
        assert restarted.store.fulfillment.stored("cafe") == retained_receipt
        assert restarted.store.fulfillment.stored("abcd") == receipt
        assert run(restarted.daemon.on_fulfillment(f.Get("abcd"), ctl.Peer(123))).result == "unavailable"
    finally:
        restarted.store.close()
