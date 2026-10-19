"""Exercise V2 row decoding through actual fulfillment projection selectors."""

import json

import pytest
from test_admind_fulfillment import apply, seed
from test_admind_management_presentation import META

from heterodyne.admind import asks, management
from heterodyne.admind import fulfillment as f

pytest_plugins = ("test_admind_fulfillment",)


@pytest.mark.parametrize("state", ["active", "fulfilled", "inactive", "dirty"])
def test_projection_selectors_preserve_management_metadata(store, state):
    seed(store, status="open" if state == "active" else "answered")
    store.db.execute(
        "UPDATE asks SET presentation=?,render_version=?,management=? WHERE ask_id='abcd'",
        (management.TAG, management.VERSION, json.dumps(META)),
    )
    if state in ("fulfilled", "dirty"):
        with pytest.raises(f.Invalid, match="unsupported-management-fulfillment"):
            apply(store)
    if state == "inactive":
        store.set_ask("abcd", "cancelled")
    if state == "dirty":
        store.db.execute("UPDATE asks SET body=body||' changed' WHERE ask_id='abcd'")
    expected = store.ask("abcd")
    selected = {
        "active": store.capacity_asks(),
        "fulfilled": store.fulfilled_asks(),
        "inactive": store.inactive_asks(),
    }
    destination = "inactive" if state == "inactive" else "active"
    assert selected[destination] == [expected]
    assert expected.presentation == management.TAG
    assert expected.render_version == management.VERSION
    assert json.loads(expected.management) == META
    assert store.capacity_count() == len(selected["active"])
    assert selected["fulfilled"] == []
    if state != "inactive":
        assert selected["inactive"] == []


def test_projection_selector_retains_malformed_management_for_closed_refusal(store):
    seed(store, status="open")
    store.db.execute(
        "UPDATE asks SET presentation=?,render_version=?,management='{}' WHERE ask_id='abcd'",
        (management.TAG, management.VERSION),
    )
    rows = store.capacity_asks()
    assert len(rows) == 1 and rows[0].management == "{}"
    with pytest.raises(management.Refusal):
        asks.full_chunks(rows[0], 4000)
