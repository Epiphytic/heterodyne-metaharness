"""Actual disposable admind SQLite relay transactions; no network or native authority."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import msgspec
import pytest

from heterodyne.admind import asks, ctl
from heterodyne.admind.store import Store

pytest_plugins = ("test_admind_management_presentation",)
KEY = "sandbox-manager:0044fed7-381d-449e-bc62-6f487467f1dc:design-question"
PEER = ctl.Peer(123)


def request(key=KEY, **changes):
    post = asks.AskPost("question", "Untrusted sandbox manager request",
        "This is an untrusted sandbox manager question. Please review the following request "
        "in its retained task context and answer through the normal operator channel.", form="yes-no")
    return asks.AskQuestionOnce(key, msgspec.structs.replace(post, **changes))


def count(daemon, table):
    assert table in {"asks", "outbox", "ask_question_keys"}
    return daemon.store.db.execute("SELECT count(*) FROM " + table).fetchone()[0]  # noqa: S608 - closed set


def test_strict_wire_roundtrip_and_question_only_scope():
    req = request()
    assert asks.check(req) is None
    assert msgspec.json.decode(msgspec.json.encode(req), type=asks.AskRequest) == req
    for invalid in (request("other:key"), request(KEY + ":nested"), request(KEY + "\n"),
                    request(kind="approval"), request(bead="btq-example"),
                    request(presentation="task-management-consent-v1")):
        assert asks.check(invalid) is not None
    assert asks.describe(req)["op"] == "question.once.v1"
    assert "request_sha256" in asks.describe(req)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(msgspec.json.encode(req)[:-1] + b',"authority":true}', type=asks.AskRequest)


def test_exact_replay_wins_when_full_latched_and_hourly_limited(daemon, monkeypatch):
    req = request()
    first = daemon.post_question_once(req, PEER)
    assert first.result == "posted"
    baseline = [count(daemon, table) for table in ("asks", "outbox", "ask_question_keys")]
    assert baseline[0] == baseline[2] == 1
    monkeypatch.setattr(daemon, "latched", lambda: True)
    monkeypatch.setattr(daemon.store, "unanswered_question_count", lambda: 100)
    monkeypatch.setattr(daemon.store, "ask_posted_since", lambda _: asks.MAX_PER_HOUR)
    replay = daemon.post_question_once(req, PEER)
    assert replay.ask.summary.ask_id == first.ask.summary.ask_id
    assert replay.result == "posted"
    assert daemon.post_question_once(request(body=req.post.body + " changed"), PEER).result == "refused"
    assert [count(daemon, table) for table in ("asks", "outbox", "ask_question_keys")] == baseline
    assert daemon.post_question_once(request(KEY + "2"), PEER).result == "refused"


def test_transaction_rolls_back_ask_and_outbox_when_binding_fails(daemon):
    original = daemon.store.bind_question
    def fail(*args):
        original(*args)
        raise RuntimeError("crash after key insert before commit")
    with patch.object(daemon.store, "bind_question", side_effect=fail), pytest.raises(RuntimeError):
        daemon.post_question_once(request(), PEER)
    assert all(count(daemon, table) == 0 for table in ("asks", "outbox", "ask_question_keys"))
    assert daemon.post_question_once(request(), PEER).result == "posted"


def test_ack_loss_restart_replays_actual_retained_id(daemon):
    req = request()
    # Response is discarded after actual SQLite COMMIT, as on transport loss.
    daemon.post_question_once(req, PEER)
    actual = daemon.store.question_key(KEY)[2]
    path = daemon.store.path
    daemon.store.close()
    daemon.store = Store(path)
    reply = daemon.post_question_once(req, PEER)
    assert reply.ask.summary.ask_id == actual
    assert count(daemon, "asks") == count(daemon, "ask_question_keys") == 1


@pytest.mark.parametrize("conflicting", [False, True])
def test_two_actual_sqlite_connections_serialize_same_key(daemon, conflicting):
    second = copy.copy(daemon)
    second.store = Store(daemon.store.path)
    def enqueue(prefix, parts, lane=1, *, owner):
        for i, text in enumerate(parts):
            second.store.enqueue(f"{prefix}:{i}", text, None, ownership_kind="system")
    second.post_parts = enqueue
    barrier = Barrier(2)
    def post(d):
        barrier.wait(timeout=5)
        req = request(body=request().post.body + " changed") if conflicting and d is second else request()
        return d.post_question_once(req, PEER)
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(post, d) for d in (daemon, second)]
            replies = [f.result(timeout=10) for f in futures]
        assert sorted(r.result for r in replies) == (["posted", "refused"] if conflicting else ["posted"] * 2)
        assert len({r.ask.summary.ask_id for r in replies if r.ask is not None}) == 1
        assert count(daemon, "asks") == count(daemon, "ask_question_keys") == 1
        assert count(daemon, "outbox") == 1
    finally:
        second.store.close()


def test_actual_100_unanswered_limit_but_answered_history_unbounded(daemon, monkeypatch):
    monkeypatch.setattr(asks, "MAX_PER_HOUR", 1000)
    for i in range(100):
        assert daemon.post_question_once(request(KEY + str(i)), PEER).result == "posted"
    assert daemon.store.unanswered_question_count() == 100
    retained = daemon.store.question_key(KEY + "0")[2]
    assert daemon.post_question_once(request(KEY + "0"), PEER).ask.summary.ask_id == retained
    assert daemon.post_question_once(request(KEY + "overflow"), PEER).result == "refused"
    assert count(daemon, "ask_question_keys") == 100
    daemon.store.db.execute("UPDATE asks SET status='answered'")
    for i in range(101):
        assert daemon.post_question_once(request(KEY + "history" + str(i)), PEER).result == "posted"
        daemon.store.db.execute("UPDATE asks SET status='answered'")
    assert count(daemon, "asks") == 201
    assert daemon.store.unanswered_question_count() == 0


def test_missing_retained_ask_refuses_without_repost(daemon):
    daemon.post_question_once(request(), PEER)
    daemon.store.db.execute("DELETE FROM asks")
    before = count(daemon, "outbox")
    assert daemon.post_question_once(request(), PEER).result == "refused"
    assert count(daemon, "asks") == 0
    assert count(daemon, "outbox") == before


def test_actual_async_dispatch_uses_closed_once_request(daemon):
    daemon.ask_post_lock = asyncio.Lock()
    daemon.posts_in_flight = 0
    reply = asyncio.run(daemon.on_ask(request(), PEER))
    assert reply.result == "posted" and daemon.store.question_key(KEY)[2] == reply.ask.summary.ask_id
