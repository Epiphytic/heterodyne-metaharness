"""NIP-19 `npub` encoding: bech32 (BIP-173) over a 32-byte public key.

Only public keys are handled here; nothing in heterodyne decodes secret keys. Error messages never
echo the input, because an npub is an install-specific value (ADR 0001 §15).
"""

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_HRP = "npub"


class Nip19Error(ValueError):
    pass


def _polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, gen in enumerate(_GEN):
            if (top >> i) & 1:
                chk ^= gen
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise Nip19Error("invalid data value")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or (acc << (tobits - bits)) & maxv:
        raise Nip19Error("invalid padding")
    return out


def npub_to_hex(npub: str) -> str:
    """The 64-character lowercase hex public key encoded by `npub`."""
    if npub != npub.lower() and npub != npub.upper():
        raise Nip19Error("npub has mixed case")
    text = npub.lower()
    sep = text.rfind("1")
    hrp, data_part = text[:sep], text[sep + 1:]
    if sep < 0 or hrp != _HRP or len(data_part) < 7:
        raise Nip19Error("not an npub")
    try:
        data = [_CHARSET.index(c) for c in data_part]
    except ValueError:
        raise Nip19Error("npub contains a character outside the bech32 alphabet") from None
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise Nip19Error("npub checksum does not match")
    raw = _convertbits(data[:-6], 5, 8, pad=False)
    if len(raw) != 32:
        raise Nip19Error("npub does not encode a 32-byte key")
    return bytes(raw).hex()


def hex_to_npub(hexkey: str) -> str:
    try:
        raw = bytes.fromhex(hexkey)
    except ValueError:
        raise Nip19Error("public key is not hex") from None
    if len(raw) != 32:
        raise Nip19Error("public key must be 32 bytes")
    data = _convertbits(list(raw), 8, 5, pad=True)
    polymod = _polymod(_hrp_expand(_HRP) + data + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return _HRP + "1" + "".join(_CHARSET[d] for d in data + checksum)
