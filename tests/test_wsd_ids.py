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


SLUG_MAX = "a" * 128
PROFILE_MAX = "P" * 64


@pytest.mark.parametrize("good", ["a", "0", "a.b_c-d", SLUG_MAX])
def test_slug_accepts_names_up_to_128(good: str) -> None:
    assert ids.slug(good, "thing") == good
    assert ids.ws_session(good) == str(uuid.uuid5(ids.NS, good))


@pytest.mark.parametrize("bad", ["", "Alpha", "a:b", "-x", ".x", "_x", "a\n", SLUG_MAX + "a"])
def test_slug_refuses_and_names_the_key_not_the_value(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^workstream is not a valid slug$"):
        ids.slug(bad, "workstream")


@pytest.mark.parametrize("bad", ["", "B", "a:b", "../x", SLUG_MAX + "a"])
def test_bead_session_refuses_a_bad_workstream_or_bead(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^workstream "):
        ids.bead_session(bad, "btq-1")
    with pytest.raises(ids.BadName, match="^bead "):
        ids.bead_session("alpha", bad)


def test_bead_session_accepts_the_longest_names() -> None:
    assert ids.bead_session(SLUG_MAX, SLUG_MAX) == str(uuid.uuid5(ids.NS, f"{SLUG_MAX}:{SLUG_MAX}"))


@pytest.mark.parametrize("bad", ["", "B", "a:b", "../x", SLUG_MAX + "a"])
def test_role_session_refuses_a_bad_bead_or_role(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^bead "):
        ids.role_session(bad, "coder", "p-one")
    with pytest.raises(ids.BadName, match="^role "):
        ids.role_session("btq-1", bad, "p-one")


@pytest.mark.parametrize("bad", ["", "-p", ".p", "p:q", "p/q", "../x", "p q", PROFILE_MAX + "P"])
def test_role_session_refuses_a_bad_profile(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^profile is not a valid name$"):
        ids.role_session("btq-1", "coder", bad)


@pytest.mark.parametrize("good", ["p", "P-One.2_x", PROFILE_MAX])
def test_role_session_accepts_profiles_up_to_64(good: str) -> None:
    assert ids.role_session("btq-1", "coder", good) == str(uuid.uuid5(ids.NS, f"btq-1:coder:{good}"))


@pytest.mark.parametrize("bad", ["", "p:q", "../x", PROFILE_MAX + "P"])
def test_profile_for_refuses_a_bad_default(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^profile is not a valid name$"):
        ids.profile_for((), "coder", bad)


@pytest.mark.parametrize("bad", ["", "p:q", PROFILE_MAX + "P"])
def test_profile_for_refuses_a_bad_label_profile(bad: str) -> None:
    with pytest.raises(ids.BadName, match="^profile is not a valid name$"):
        ids.profile_for((f"role:coder={bad}",), "coder", "p-one")


def test_profile_for_accepts_the_longest_profile() -> None:
    assert ids.profile_for((f"role:coder={PROFILE_MAX}",), "coder", "p-one") == PROFILE_MAX
    assert ids.profile_for((), "coder", PROFILE_MAX) == PROFILE_MAX


def test_profile_for_reads_only_its_own_role() -> None:
    labels = ("role:coder2=p-two", "role:review=p-three", "ws:alpha", "role:coder", "xrole:coder=p-four")
    assert ids.profile_for(labels, "coder", "p-one") == "p-one"


def test_a_repeated_identical_role_label_is_not_a_conflict() -> None:
    assert ids.profile_for(("role:coder=p-two", "role:coder=p-two"), "coder", "p-one") == "p-two"
