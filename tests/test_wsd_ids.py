import uuid

import pytest

from heterodyne.wsd import ids


def test_session_ids_are_deterministic_uuid5() -> None:
    assert ids.ws_session("alpha") == str(uuid.uuid5(ids.NS, "alpha"))
    assert ids.bead_session("alpha", "btq-1") == str(uuid.uuid5(ids.NS, "alpha:btq-1"))
    assert ids.role_session("btq-1", "coder", "p-one") == str(uuid.uuid5(ids.NS, "btq-1:coder:p-one"))
    assert ids.bead_session("alpha", "btq-1") != ids.bead_session("beta", "btq-1")


def test_namespace_is_pinned() -> None:
    # Changing NS orphans every claim and session: this value must never change.
    assert str(ids.NS) == str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:heterodyne:wsd"))


@pytest.mark.parametrize("bad", ["", "Alpha", "a b", "../x", "a/b", "-x", "x" * 129])
def test_bad_slugs_are_refused(bad: str) -> None:
    with pytest.raises(ids.BadName):
        ids.ws_session(bad)


def test_role_label_overrides_profile() -> None:
    assert ids.profile_for(("role:coder=p-two", "role:review=p-one"), "coder", "p-one") == "p-two"
    assert ids.profile_for((), "coder", "p-one") == "p-one"


def test_conflicting_role_labels_are_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=p-one", "role:coder=p-two"), "coder", "p-one")


def test_bad_profile_in_label_is_refused() -> None:
    with pytest.raises(ids.BadName):
        ids.profile_for(("role:coder=../x",), "coder", "p-one")
