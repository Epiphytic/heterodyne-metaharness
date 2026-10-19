"""V1 lifecycle never attests to unbound V2 management consent metadata."""

import json
import os

import pytest
from test_admind_fulfillment import apply, completion, declaration, deliver, seed
from test_admind_management_presentation import META

from heterodyne.admind import fulfillment as f
from heterodyne.admind import fulfillment_projection as projection
from heterodyne.admind import management

pytest_plugins = ("test_admind_fulfillment",)

VARIANTS = ["valid", "malformed", "partial", "version-text"]


def metadata(store, variant):
    if variant in ("valid", "malformed"):
        store.db.execute(
            "UPDATE asks SET presentation=?,render_version=?,management=?,"
            "question_form='choices',choices=? WHERE ask_id='abcd'",
            (
                management.TAG,
                management.VERSION,
                json.dumps(META) if variant == "valid" else "{}",
                json.dumps({"choices": management.CHOICES, "recommended": "A"}),
            ),
        )
    elif variant == "partial":
        store.db.execute("UPDATE asks SET presentation=? WHERE ask_id='abcd'", (management.TAG,))
    else:
        store.db.execute("UPDATE asks SET render_version='bad' WHERE ask_id='abcd'")
    if variant == "valid":
        assert management.stored(store.ask("abcd")) is not None


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("route", ["binding", "apply", "propose", "confirm"])
def test_management_metadata_refuses_all_v1_fulfillment_routes(store, variant, route):
    seed(store, owned=True)
    receipt = completion(store)
    proposed = f.Propose("abcd", declaration(store))
    target = None
    if route == "confirm":
        pending = store.fulfillment.propose(proposed, uid=os.getuid(), chunk_chars=4000)
        target = deliver(store, pending.view["intent_id"])
    metadata(store, variant)
    with pytest.raises(f.Invalid, match="unsupported-management-fulfillment"):
        if route == "binding":
            store.fulfillment.binding("abcd")
        elif route == "apply":
            apply(store, receipt)
        elif route == "propose":
            store.fulfillment.propose(proposed, uid=os.getuid(), chunk_chars=4000)
        else:
            store.fulfillment.confirm(target, principal="Liam", message_id="d" * 64)
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (0,)
    assert store.capacity_count() == 1


@pytest.mark.parametrize(
    "column,value", [("presentation", management.TAG), ("render_version", 2), ("management", "{}")]
)
def test_preexisting_v1_receipt_cannot_suppress_metadata_added_after_verification(store, column, value):
    seed(store)
    receipt = completion(store)
    assert apply(store, receipt).result == "applied"
    assert store.capacity_count() == 0
    generation = store.db.execute("SELECT generation FROM fulfillment_generations").fetchone()
    # Deliberately exercise the existing v2 projection DDL without changing its trigger or hash.
    store.db.execute(f"UPDATE asks SET {column}=? WHERE ask_id='abcd'", (value,))  # noqa: S608 - finite parametrized columns
    assert store.db.execute("SELECT generation FROM fulfillment_generations").fetchone() == generation
    assert store.capacity_count() == 1
    assert [row.ask_id for row in store.capacity_asks()] == ["abcd"]
    assert store.fulfilled_asks() == []
    assert store.inactive_asks() == []
    view = store.fulfillment.view("abcd")
    assert view["lifecycle"] == "unverified" and view["binding"] is None
    with pytest.raises(f.Invalid, match="unsupported-management-fulfillment"):
        projection.bounded_binding(store.db, "abcd")
    with pytest.raises(f.Invalid, match="unsupported-management-fulfillment"):
        apply(store, receipt)  # An exact duplicate must not report misleading V2 fulfillment.
    assert store.db.execute("SELECT COUNT(*) FROM ask_fulfillments").fetchone() == (1,)
