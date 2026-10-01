import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.marmot.nip19 import Nip19Error, hex_to_npub, npub_to_hex

VECTOR = json.loads((Path(__file__).parent / "fixtures" / "nip19.json").read_text())


def test_spec_vector_both_ways() -> None:
    assert npub_to_hex(VECTOR["npub"]) == VECTOR["hex"]
    assert hex_to_npub(VECTOR["hex"]) == VECTOR["npub"]


def test_uppercase_is_accepted_and_mixed_case_rejected() -> None:
    assert npub_to_hex(VECTOR["npub"].upper()) == VECTOR["hex"]
    mixed = VECTOR["npub"][:10] + VECTOR["npub"][10:].upper()
    with pytest.raises(Nip19Error, match="mixed case"):
        npub_to_hex(mixed)


@pytest.mark.parametrize("mutate", [
    lambda s: s[:-1] + ("q" if s[-1] != "q" else "p"),   # checksum
    lambda s: "nsec" + s[4:],                               # wrong prefix
    lambda s: s.replace("1", "b", 1),                       # no separator
    lambda s: s[:20] + "o" + s[21:],                        # 'o' is not in the bech32 alphabet
    lambda s: s[:-7],                                       # truncated
])
def test_invalid_inputs_raise_without_echoing_the_value(mutate: object) -> None:
    bad = mutate(VECTOR["npub"])  # type: ignore[operator]
    with pytest.raises(Nip19Error) as exc:
        npub_to_hex(bad)
    assert bad not in str(exc.value)


def test_hex_must_be_32_bytes() -> None:
    with pytest.raises(Nip19Error):
        hex_to_npub("ab" * 31)
    with pytest.raises(Nip19Error):
        hex_to_npub("zz" * 32)


@given(st.binary(min_size=32, max_size=32))
def test_round_trip(raw: bytes) -> None:
    assert npub_to_hex(hex_to_npub(raw.hex())) == raw.hex()
