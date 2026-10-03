# tests/test_admind_r13_redact.py
import json
import re
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.admind import redact as redact_module
from heterodyne.admind.audit import Audit, ref_id
from heterodyne.admind.redact import redact, redact_continuation, unique_key
from heterodyne.marmot.nip19 import hex_to_npub

HEX = "ab" * 32
NPUB = hex_to_npub("c3" * 32)
TOKEN = "ghp_" + "A" * 30
# Characters that interact: hex digits, controls whose escapes add hex digits, and pattern prefixes.
TRICKY = st.text(alphabet="0123456789abcdefABCDEF\x00\x01\x1b\x7f\x9b\n\t xgh_p-npub1skAKIAeyJ.",
                 max_size=400)


def test_replaces_only_the_match() -> None:
    assert redact(f"commit {HEX} is bad") == "commit <redacted hex key> is bad"
    assert redact(f"ask {NPUB}, please") == "ask <redacted npub>, please"
    assert redact(f"token {TOKEN} end") == "token <redacted GitHub token> end"


def test_longer_hex_runs_are_hidden_whole() -> None:
    assert redact("x" + "f" * 65 + "y") == "x<redacted hex key>y"


def test_pem_block_is_hidden_to_its_end() -> None:
    pem = "-----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"   # split: gitleaks
    assert redact(f"a\n{pem}\nb") == "a\n<redacted PEM private key>\nb"


def test_layout_kept_controls_escaped() -> None:
    assert redact("a\tb\nc\rd\x1b[2Je\x9b") == "a\tb\nc\\x0dd\\x1b[2Je\\x9b"


def test_an_escape_next_to_hex_is_redacted_in_one_pass() -> None:
    # Codex r1 finding 5: escaping after the hex pass turned 63 hex digits into a 65-digit run.
    once = redact("\x1b" + "f" * 63)
    assert once == "\\x<redacted hex key>"
    assert redact(once) == once


def test_a_marker_that_makes_a_boundary_is_redacted_again() -> None:
    # Codex r2 finding 1: a hex run glued to a token hides it from the token pattern until the run
    # becomes a marker; the fixpoint loop catches it in the same call.
    once = redact("f" * 64 + TOKEN)
    assert once == "<redacted hex key><redacted GitHub token>"
    assert redact(once) == once


def test_a_control_before_a_secret_does_not_hide_it() -> None:
    assert redact("a\x01" + TOKEN) == "a\\x01<redacted GitHub token>"


@given(st.one_of(st.text(), TRICKY))
def test_idempotent(text: str) -> None:
    once = redact(text)
    assert redact(once) == once


@given(st.one_of(st.text(alphabet="0123456789abcdefABCDEF xyz\n", max_size=300), TRICKY))
def test_no_hex_key_survives(text: str) -> None:
    assert re.search(r"[0-9A-Fa-f]{64}", redact(text)) is None


@given(st.text())
def test_no_control_survives(text: str) -> None:
    out = redact(text)
    assert all(c in "\n\t" or not (ord(c) < 32 or 127 <= ord(c) <= 159) for c in out)


def test_continuation_hides_the_rest_of_a_split_value() -> None:
    assert redact_continuation("key ghp_AAAAAAAAAA", "A" * 20 + " rest") == "<redacted fragment> rest"
    pem_head = "-----BEG"
    pem_rest = "IN " + "PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----\nafter"   # split: gitleaks
    assert redact_continuation("see " + pem_head, pem_rest) == "<redacted fragment>\nafter"
    # An escape next to a hex run changes where the run starts; the continuation sees the same text.
    previous = "\x1b" + "f" * 62 + TOKEN[:8]
    rest = TOKEN[8:] + " rest"
    assert TOKEN not in redact(previous + rest)
    assert redact_continuation(previous, rest) == "<redacted fragment> rest"
    assert redact_continuation("plain words. ", "more words") == "more words"
    # A value glued to a hex run that straddles the boundary is found the way redact finds it.
    assert redact_continuation("f" * 64 + "ghp_AAAA", "A" * 26 + " rest") == "<redacted fragment> rest"


def test_text_that_never_settles_is_replaced_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    flips = iter(range(1000))
    monkeypatch.setattr(redact_module, "_pass", lambda text: f"{text}x{next(flips)}")
    assert redact("anything") == redact_module.UNSTABLE
    assert next(flips) == redact_module.MAX_PASSES


def records(path: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_audit_redacts_every_field(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    audit.write("probe", message_id=HEX, key=f"cmd:{HEX}:0", text=f"hi {NPUB}\x1b",
                nested={"list": [TOKEN, 3, None], "deep": (HEX,)}, other=Path(f"/x/{HEX}"))
    raw = (tmp_path / "audit.jsonl").read_text()
    assert HEX not in raw and NPUB not in raw and TOKEN not in raw and "\x1b" not in raw
    r = records(tmp_path / "audit.jsonl")[0]
    assert r["message_id"] == ref_id(HEX) and r["key"] == f"cmd:{ref_id(HEX)}:0"
    assert r["text"] == "hi <redacted npub>\\x1b"
    assert r["nested"] == {"list": ["<redacted GitHub token>", 3, None], "deep": ["<redacted hex key>"]}
    assert r["other"] == "/x/<redacted hex key>"
    assert ref_id(HEX) == ref_id(HEX.upper()) and re.fullmatch(r"id:[0-9a-f]{12}", ref_id(HEX))


def test_audit_redacts_kind_and_field_names(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    audit.write(TOKEN, **{TOKEN: "safe"})
    raw = (tmp_path / "audit.jsonl").read_text()
    assert TOKEN not in raw
    assert records(tmp_path / "audit.jsonl")[0]["kind"] == "<redacted GitHub token>"


def test_audit_keeps_keys_that_redact_alike(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.jsonl")
    audit.write("probe", nested={"a" * 64: "first", "b" * 64: "second"})
    r = records(tmp_path / "audit.jsonl")[0]
    assert r["nested"] == {"<redacted hex key>": "first", "<redacted hex key> #2": "second"}
    assert unique_key("k", {"k", "k #2"}) == "k #3" and unique_key("k", set()) == "k"


from admind_waits import wait_until  # noqa: E402
from test_admind_daemon import Harness, needs_tmux, run_with  # noqa: E402  (shared harness)


@needs_tmux
def test_audit_is_whole_and_posts_are_redacted(tmp_path: Path) -> None:
    long_text = "line " * 1000 + HEX          # over the old 2,000-char cut

    async def scenario(h: Harness) -> None:
        await h.say("hello")                           # join signal
        await wait_until(lambda: any("listening" in t for t in h.texts()))
        mid = await h.say(long_text)
        await wait_until(lambda: any(r.get("kind") == "inbound" and len(str(r.get("text", ""))) > 4000
                                     for r in records(h.settings.state_dir / "audit.jsonl")))
        assert any(r.get("message_id") == ref_id(mid) for r in records(h.settings.state_dir / "audit.jsonl"))
        h.daemon.post("probe", f"see {HEX}", None)
        await wait_until(lambda: any("see <redacted hex key>" == t for t in h.texts()))

    h = run_with(tmp_path, scenario)
    raw = (h.settings.state_dir / "audit.jsonl").read_text()
    assert re.search(r"[0-9A-Fa-f]{64}", raw) is None
    inbound = [r for r in records(h.settings.state_dir / "audit.jsonl")
               if r.get("kind") == "inbound" and "line line" in str(r.get("text", ""))]
    assert inbound and inbound[-1]["text"] == redact(long_text)
    assert all(HEX not in t for t in h.texts())


def test_alert_name_is_redacted_before_any_cut(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    h.daemon.relay_alert("_" + HEX, b"raw", None)
    raw = (h.settings.state_dir / "audit.jsonl").read_text()
    assert re.search(r"[0-9A-Fa-f]{40}", raw) is None
    assert "_<redacted hex key>" in raw


@pytest.mark.parametrize("lead", ["\n", "\t"])
def test_audit_hides_a_token_inside_bytes_and_sets(tmp_path: Path, lead: str) -> None:
    """A repr would escape the control character into a letter before redaction and hide the token start."""
    secret = lead + TOKEN
    path = tmp_path / "audit.jsonl"
    Audit(path).write(
        "probe",
        raw=secret.encode(),
        array=bytearray(secret.encode()),
        view=memoryview(secret.encode()),
        members={secret, "other"},
        frozen=frozenset({secret}),
        nested={"k": [{secret}]},
        by_bytes={secret.encode(): "v"},
    )
    raw = path.read_text()
    assert TOKEN not in raw and TOKEN[4:] not in raw
    members = cast(list[str], records(path)[0]["members"])
    assert members == sorted(members, key=lambda x: json.dumps(x))  # deterministic
    assert "other" in members


class _Bag(Mapping[Any, Any]):
    def __init__(self, data: dict[Any, Any]) -> None:
        self._d = data

    def __getitem__(self, k: Any) -> Any:
        return self._d[k]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __repr__(self) -> str:
        return repr(self._d)


@pytest.mark.parametrize("lead", ["\n", "\t"])
@pytest.mark.parametrize("make", [
    lambda s: {(s,): "v"},
    lambda s: {frozenset({s}): "v"},
    lambda s: {frozenset({(s,)}): "v"},
    lambda s: deque([s]),
    lambda s: _Bag({"k": s}),
    lambda s: _Bag({(s,): "v"}),
])
def test_audit_hides_a_token_in_compound_keys_and_generic_containers(
    tmp_path: Path, lead: str, make: Callable[[str], object]
) -> None:
    path = tmp_path / "audit.jsonl"
    Audit(path).write("probe", value=make(lead + TOKEN))
    raw = path.read_text()
    assert TOKEN not in raw and TOKEN[4:] not in raw
