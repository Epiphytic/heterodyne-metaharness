"""Construct evidence links from closed typed identities, never accept arbitrary URLs."""

from . import dto as d
from .safety import Span, generated, join, literal


def supported(repo: d.Repo, policy: d.RenderPolicy) -> bool:
    return repo.forge == "github" and repo.origin == "https://github.com" and repo in policy.forges


def _url(repo: d.Repo, suffix: str, pins: tuple[str, ...] = ()) -> Span:
    prefix = f"{repo.origin}/{repo.owner}/{repo.name}/"
    text = prefix + suffix
    # Only the typed suffix's exact commit positions exempt identifier scanning.
    regions: list[tuple[int, int]] = []
    cursor = 0
    for pin in pins:
        position = suffix.index(pin, cursor)
        regions.append((len(prefix) + position, len(prefix) + position + len(pin)))
        cursor = position + len(pin)
    return Span(
        text,
        public=tuple(regions),
        syntax=((0, len(text)),),
        template=((0, len(text)),),
        atomic=True,
        url=True,
    )


def evidence(ref: d.EvidenceRef, policy: d.RenderPolicy) -> tuple[Span, ...] | None:
    """None means recognized unsupported material. Unsafe literal text is retained for screening."""
    if isinstance(ref, d.OperatorDoc):
        return None
    if isinstance(ref, d.Inline):
        return (generated("Evidence: ") + Span(ref.title), Span(ref.full_text))
    if isinstance(ref, d.BeadContext):
        detail = evidence(ref.accessible_detail, policy)
        if detail is None:
            return None
        return (
            generated("Evidence: ")
            + Span(ref.short_bead)
            + generated(" · ")
            + generated(ref.digest12, public=True),
            *detail,
        )
    if not supported(ref.repo, policy):
        return None
    if isinstance(ref, d.File):
        suffix = f"blob/{ref.commit}/{ref.path}"
        if ref.line_start is not None:
            suffix += f"#L{ref.line_start}"
            if ref.line_end is not None:
                suffix += f"-L{ref.line_end}"
        return (_url(ref.repo, suffix, (ref.commit,)),)
    if isinstance(ref, d.Commit):
        return (_url(ref.repo, f"commit/{ref.commit}", (ref.commit,)),)
    if isinstance(ref, d.PullRequest):
        return (_url(ref.repo, f"pull/{ref.number}"), _url(ref.repo, f"commit/{ref.head}", (ref.head,)))
    return (
        generated(ref.base + ".." + ref.head, public=True, atomic=True),
        _url(ref.repo, f"compare/{ref.base}...{ref.head}", (ref.base, ref.head)),
    )


def effect(value: d.EffectText, policy: d.RenderPolicy) -> Span | None:
    parts: list[Span] = []
    for part in value.spans:
        if isinstance(part, d.PublicHash):
            parts.append(generated(part.text, public=True))
        else:
            checked = literal(part.text, policy, multiline=True, effect=True)
            if checked is None:
                return None
            parts.append(checked)
    return join(parts, "")
