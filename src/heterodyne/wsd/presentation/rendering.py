"""Pure rendering. No calls to source owners, no clocks, no output effects."""

import re
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, cast

from heterodyne.marmot.nip19 import Nip19Error, npub_to_hex

from . import dto as d
from .evidence import effect, evidence
from .layout import TooWide, paginate, wrap
from .safety import Span, generated, join, literal, safe
from .sources import footer, rows_in, source_failure, validate_view, warnings
from .validation import BadInput, instant, validate

_REASON_LABELS = {
    "missing-required": "missing required content",
    "unsafe-content": "unsafe content",
    "stale-card": "stale or superseded card",
    "incoherent-revision": "content changed",
    "ineligible": "source not eligible",
    "unsupported-answer-policy": "unsupported answer policy",
    "unsupported-reference": "complete reference unavailable here",
    "missing-mention": "operator mention unavailable",
    "content-too-large": "complete content exceeds card limit",
    "content-too-wide": "complete content exceeds line limit",
    "required-source": "source unavailable",
}
_TERMINAL = {
    "busy": "Read service busy; try again later.",
    "usage": "Use /workstreams, /status [bead], /approvals, or /queue [workstream].",
    "not-found": "Item unavailable in this scope.",
    "unsupported": "This action is unavailable on the read surface.",
    "unavailable": "Data unavailable; try again later.",
    "narrow-query": "Too much detail for this view. No partial content shown.",
}
_TASK_ICON = {
    "received": "👀",
    "running": "⏳",
    "review": "🔍",
    "parked": "⏸️",
    "deferred": "⏸️",
    "needs-operator": "❓",
    "closed": "✅",
    "failed": "❌",
    "held": "⏸️",
    "unknown": "—",
}
_ACCOUNT_CHANGED = (
    "deferred: account changed; restore login or account order, then reload configuration "
    "or use operator release"
)
_DEFAULT_POLICY = d.RenderPolicy()
_ROW_NAMES = {
    d.Task: "task",
    d.WorkstreamRow: "workstream",
    d.ApprovalSummary: "approval",
    d.NeedsHuman: "needs_human",
    d.AccountUsage: "account_usage",
    d.Event: "event",
    d.DecisionSummary: "decision",
    d.Launch: "launch",
    d.Candidate: "candidate",
    d.RoleHeadroom: "role_headroom",
}
_ATOMIC_FIELDS = frozenset(
    (
        "bead",
        "name",
        "branch",
        "agent",
        "role",
        "account",
        "account_label",
        "session_label",
        "current_bead",
        "launch_label",
        "actor_label",
    )
)
_HIDDEN = frozenset(("opaque_order_key",))


def withheld(kind: str, reason: str) -> d.Withheld:
    label = _REASON_LABELS[reason]
    if kind == "question":
        text = (
            f"Cannot present question as written: {label}.\n"
            "Ask owner to correct the question; no answer recorded."
        )
    else:
        text = f"Cannot approve as written: {label}.\nAsk owner to correct the card; no decision recorded."
    return d.Withheld(reason, text)  # type: ignore[arg-type]


def _policy(policy: d.RenderPolicy) -> None:
    validate(policy, d.RenderPolicy)
    if policy.operator_npub is not None:
        try:
            if policy.operator_npub != policy.operator_npub.lower():
                raise Nip19Error("case")
            npub_to_hex(policy.operator_npub)
        except Nip19Error:
            raise BadInput("schema") from None


def _prepare(view: d.PresentationView, policy: d.RenderPolicy) -> d.Invalid | None:
    try:
        validate(view, d.PresentationView)
        _policy(policy)
        validate_view(view)
    except BadInput as exc:
        return d.Invalid(str(exc))
    return None


def _reason(value: d.Reason) -> str:
    if value is None:
        return "none"
    if isinstance(value, d.Unknown):
        return "unknown"
    if isinstance(value, d.Quota):
        return "deferred: quota; resumes " + (instant(value.resume_at) if value.resume_at else "unknown")
    if isinstance(value, d.AccountChanged):
        return _ACCOUNT_CHANGED
    if isinstance(value, d.Held):
        return f"held: {value.reason}; only operator release resumes it"
    return "blocked: " + value.explanation


class Ordinary:
    """Per-call completeness accumulator, never shared mutable policy/state."""

    def __init__(self, view: d.Available, policy: d.RenderPolicy):
        self.view = view
        self.policy = policy
        self.complete = all(
            s.availability == "reachable"
            and s.completeness == "complete"
            and s.observed_at is not None
            and s.observed_at <= view.rendered_at
            for s in view.sections
        )
        self.statuses = {s.key: s for s in view.sections}

    def text(self, value: str, *, atomic: bool = False) -> Span:
        checked = literal(value, self.policy, atomic=atomic)
        if checked is None:
            self.complete = False
            return generated("[withheld]", atomic=atomic)
        return checked

    def scalar(self, name: str, value: Any) -> Span:
        if value == "unknown":
            self.complete = False
        if value is None:
            if name in ("current_bead", "session_label", "launch_label", "actor_label"):
                return generated("none")
            self.complete = False
            return generated("unknown")
        if isinstance(value, datetime):
            return generated(instant(value))
        if type(value) is bool:
            return generated("true" if value else "false")
        if type(value) is int:
            suffix = (
                "%" if name.endswith("_percent") else " us at render time" if name == "duration_us" else ""
            )
            return generated(str(value) + suffix)
        if isinstance(value, (d.Quota, d.AccountChanged, d.Held, d.Blocked, d.Unknown)):
            if isinstance(value, d.Unknown) or isinstance(value, d.Quota) and value.resume_at is None:
                self.complete = False
            return self.text(_reason(value))
        if name == "session_label":
            if value not in tuple(item.label for item in self.policy.session_labels):
                self.complete = False
                return generated("[withheld]")
        return self.text(value, atomic=name in _ATOMIC_FIELDS)

    def row(self, value: Any, index: int) -> list[Span]:
        if type(value) is str:
            return [self.text(value, atomic=True)]
        out = [generated(f"{_ROW_NAMES[type(value)]}[{index}]")]
        for field in fields(value):
            if field.name in _HIDDEN:
                continue
            item = getattr(value, field.name)
            if isinstance(item, tuple):
                out.extend(self.label_list(field.name, cast(tuple[Any, ...], item)))
            elif field.name == "reason":
                unknown = isinstance(item, d.Unknown) or (
                    isinstance(value, d.WorkstreamRow)
                    and value.state in ("stuck", "unknown")
                    and item is None
                )
                if unknown:
                    self.complete = False
                rendered = (
                    generated("none")
                    if item is None and not unknown
                    else self.scalar("reason", d.Unknown() if unknown else item)
                )
                out.append(generated("reason: ") + rendered)
            else:
                rendered = self.scalar(field.name, item)
                if isinstance(value, d.DecisionSummary) and field.name == "kind" and item == "approval":
                    rendered = generated("native approval record")
                out.append(generated(field.name + ": ") + rendered)
        return out

    def label_list(self, name: str, values: tuple[Any, ...]) -> list[Span]:
        if not values:
            return [generated(name + ": none")]
        out = [generated(name + ":")]
        for index, value in enumerate(values):
            out.extend(self.row(value, index))
        return out

    def collection(self, name: str, value: d.Rows[Any]) -> list[Span]:
        status = self.statuses[value.key]
        if status.count is None:
            self.complete = False
            return [generated(name + ": unknown")]
        out = [generated(name + (":" if value.items else ": none"))]
        for index, item in enumerate(value.items):
            if name == "transcript_labels" and item not in tuple(
                x.label for x in self.policy.transcript_labels
            ):
                self.complete = False
                out.append(generated("[withheld]"))
            else:
                out.extend(self.row(item, index))
        count = status.count
        suffix = (
            "; source incomplete" if status.completeness != "complete" or count.shown != count.total else ""
        )
        if suffix:
            self.complete = False
        out.append(generated(f"{value.key.collection}: {count.shown} of {count.total} supplied{suffix}"))
        return out

    def header(self) -> tuple[Span, ...]:
        payload = self.view.payload
        kind = self.view.kind
        if isinstance(payload, d.Task):
            return (generated("Task: ") + self.text(payload.bead, atomic=True),)
        if kind == "workstreams":
            return (generated("Workstreams"),)
        if kind in ("workstream-status", "control-status"):
            return (
                generated("Status: ")
                + (
                    self.text(payload.name, atomic=True)
                    if isinstance(payload, d.WorkstreamStatus)
                    else generated("control")
                ),
            )
        if isinstance(payload, d.BeadStatus):
            return (generated("Bead: ") + self.text(payload.bead, atomic=True),)
        if kind == "approvals":
            return (generated("Approvals"),)
        if kind == "queue":
            origin = self.view.origin
            label = origin.workstream if isinstance(origin, d.QueryOrigin) else None
            return (generated("Queue: ") + (self.text(label, atomic=True) if label else generated("all")),)
        if isinstance(payload, d.Fyi):
            return (generated("FYI: ") + self.text(payload.title),)
        return ()

    def data(self) -> tuple[Span, ...]:
        payload = self.view.payload
        if self.view.kind == "notice":
            return tuple(self.notice(cast(d.Notice, payload)))
        if isinstance(payload, d.Fyi):
            return (self.text(payload.body), generated("FYI: no reply needed"))
        if isinstance(payload, d.Task):
            if (
                payload.state == "unknown"
                or isinstance(payload.reason, d.Unknown)
                or isinstance(payload.reason, d.Quota)
                and payload.reason.resume_at is None
            ):
                self.complete = False
            state = payload.state
            if payload.reason is not None:
                reason = _reason(payload.reason)
                if isinstance(payload.reason, d.Unknown):
                    state += ": unknown"
                elif reason.startswith(payload.state + ":"):
                    state = reason
                else:
                    state += "; " + reason
            return (
                generated(_TASK_ICON[payload.state] + " ") + self.text(state),
                join(
                    [
                        self.text(payload.agent, atomic=True),
                        self.text(payload.role, atomic=True),
                        self.text(payload.branch, atomic=True),
                    ],
                    " · ",
                ),
                generated("title: ") + self.text(payload.title),
                generated("duration_us: ") + self.scalar("duration_us", payload.duration_us),
            )
        out: list[Span] = []
        if isinstance(payload, d.Queue):
            out.append(generated("Candidate order; actual pickup can skip or change"))
        for field in fields(payload):
            item = getattr(payload, field.name)
            if isinstance(item, d.Rows):
                out.extend(self.collection(field.name, cast(d.Rows[Any], item)))
            elif field.name not in ("name", "bead"):
                out.append(generated(field.name + ": ") + self.scalar(field.name, item))
        return tuple(out)

    def notice(self, notice: d.Notice) -> list[Span]:
        if isinstance(notice, d.Terminal):
            return [generated(_TERMINAL[notice.reason])]
        if isinstance(notice, d.CannotApprove):
            return [generated(withheld("approval", notice.reason).text)]
        if isinstance(notice, d.TrustSuspended):
            return [
                generated("Group trust suspended; reads require current authorization; actions remain held.")
            ]
        if isinstance(notice, d.AccountChanged):
            return [generated(_ACCOUNT_CHANGED)]
        if isinstance(notice, d.AllBlocked):
            return [
                self.text(notice.workstream, atomic=True)
                + generated(
                    f": all {notice.blocked_beads_count} beads blocked on {notice.approval_count} approvals: "
                )
                + self.text(notice.reason)
            ]
        if isinstance(notice, d.AllAccountsExhausted):
            if notice.reset is None:
                self.complete = False
            return [
                self.text(notice.workstream, atomic=True)
                + generated(": all ")
                + self.text(notice.role, atomic=True)
                + generated(
                    " accounts exhausted; reset " + (instant(notice.reset) if notice.reset else "unknown")
                )
            ]
        if isinstance(notice, d.DeliveryUnconfirmed):
            return [
                generated("Delivery unconfirmed: ")
                + self.text(notice.card_safe_label)
                + generated("; operator attention needed.")
            ]
        if isinstance(notice, d.Reminder):
            ref = evidence(notice.reference, self.policy)
            if ref is None:
                self.complete = False
            return [
                generated("Reminder: ") + self.text(notice.card_safe_label),
                *(ref or (generated("Reference unavailable here."),)),
            ]
        out: list[Span] = []
        for field in fields(notice):
            out.extend(self.collection(field.name, getattr(notice, field.name)))
        return out


def render(view: d.PresentationView, policy: d.RenderPolicy = _DEFAULT_POLICY) -> d.RenderResult:
    invalid = _prepare(view, policy)
    if invalid is not None:
        return invalid
    if view.kind in ("approval", "question"):
        return d.Invalid("protected-pair-required")
    if isinstance(view, d.Missing):
        return d.Unavailable("missing-snapshot")
    if source_failure(view):
        return d.Unavailable("required-source")
    renderer = Ordinary(view, policy)
    header = renderer.header() + warnings(view)
    data = renderer.data()
    # Whole literal fields have already been screened. Check concatenation before any wrapping.
    if not safe(join((*header, *data)), policy, final=True):
        renderer.complete = False
        data = (generated("[withheld]"),)
    tail = () if view.kind == "notice" else (footer(view),)
    pages = paginate(header, data, tail, policy.max_chars)
    if isinstance(pages, d.NeedsDetails):
        return pages
    if not all(safe(page, policy, final=True) for page in pages):
        # Rebuild from safe framing and a fixed replacement, retaining observation
        # provenance rather than returning an unframed fragment of this view.
        renderer.complete = False
        pages = paginate(header, (generated("[withheld]"),), tail, policy.max_chars)
        if isinstance(pages, d.NeedsDetails):
            return pages
        if not all(safe(page, policy, final=True) for page in pages):
            return d.Unavailable("required-source")
    return d.Rendered(
        tuple(page.text for page in pages),
        view.kind,
        renderer.complete,
        tuple((row.key, renderer.statuses[row.key].count) for row in rows_in(view.payload)),
    )


def _required(card: d.Approval | d.Question) -> bool:
    if not card.content_revision or not card.digest_revision:
        return False
    if isinstance(card, d.Approval):
        return bool(
            card.what
            and card.excludes
            and card.why
            and card.context_summary
            and card.evidence
            and card.exact_effect.spans
            and "".join(span.text for span in card.exact_effect.spans)
        )
    body = card.body
    return bool(
        body.title
        and body.context
        and body.question
        and (body.answer_instruction if isinstance(body, d.PlainQuestion) else body.choices)
    )


def _protected_line_fields(value: Any) -> bool:
    """LF belongs only to D4's explicit multiline literals, never card framing."""
    if isinstance(value, str):
        return "\n" not in value
    if isinstance(value, tuple):
        return all(_protected_line_fields(item) for item in cast(tuple[Any, ...], value))
    if is_dataclass(value) and not isinstance(value, type):
        multiline_fields: dict[type[Any], str] = {
            d.LiteralText: "text",
            d.Inline: "full_text",
            d.PlainQuestion: "context",
            d.ChoiceQuestion: "context",
        }
        multiline = multiline_fields.get(type(value))
        return all(
            field.name == multiline or _protected_line_fields(getattr(value, field.name))
            for field in fields(value)
        )
    return True


def _card_lines(
    card: d.Approval | d.Question, policy: d.RenderPolicy, refs: tuple[Span, ...]
) -> tuple[Span, ...] | None:
    if isinstance(card, d.Approval):
        exact = effect(card.exact_effect, policy)
        if exact is None:
            return None
        runs = [len(match.group()) for match in re.finditer(r"`+", exact.text)]
        fence = generated("`" * max(3, max(runs, default=0) + 1), syntax=True, atomic=True)
        return (
            generated("Approval" + (f" ({card.purpose})" if card.purpose else "") + " ")
            + Span(card.bead, atomic=True),
            generated("What: ") + Span(card.what),
            generated("Effect:"),
            fence,
            Span(exact.text, exact.public, exact.syntax, exact.template, atomic=True),
            fence,
            generated("Excludes: ") + Span(card.excludes),
            generated("Why: ") + Span(card.why),
            generated("Context: ") + Span(card.context_summary),
            *refs,
            generated("👍 approve · 👎 deny · ❤️ always · reply to steer"),
            generated("Context digest: ") + generated(card.digest12, public=True),
        )
    body = card.body
    lines = [
        generated("Question: ") + Span(body.title),
        generated("Context: ") + Span(body.context),
        Span(body.question),
    ]
    if isinstance(body, d.ChoiceQuestion):
        lines.extend(Span(choice.label + ": " + choice.description) for choice in body.choices)
        if body.recommended_label is not None:
            lines.append(generated("Recommended: ") + Span(body.recommended_label, atomic=True))
        lines.append(generated("Reply with a choice label."))
    else:
        lines.append(Span(body.answer_instruction))
    lines.append(generated("Context digest: ") + generated(card.digest12, public=True))
    return tuple(lines)


def _protected_layout(lines: tuple[Span, ...], mention: Span | None, policy: d.RenderPolicy) -> Span | str:
    if mention is not None:
        if len(lines[0].text) + 1 + len(mention.text) <= 120:
            lines = (lines[0] + generated(" ") + mention, *lines[1:])
        else:
            lines = (lines[0], mention, *lines[1:])
    try:
        physical = tuple(line for span in lines for line in wrap(span))
    except TooWide:
        return "content-too-wide"
    document = join(physical)
    if not safe(document, policy, final=True):
        return "unsafe-content"
    if len(physical) > 36 or len(document.text) > min(policy.max_chars, 3200):
        return "content-too-large"
    return document


def render_card_pair(view: d.PresentationView, policy: d.RenderPolicy = _DEFAULT_POLICY) -> d.CardPairResult:
    invalid = _prepare(view, policy)
    if invalid is not None:
        return invalid
    if view.kind not in ("approval", "question"):
        return d.Invalid("schema")
    if isinstance(view, d.Missing):
        return withheld(view.kind, "missing-required")
    failed = source_failure(view, protected=True)
    if failed:
        return withheld(view.kind, failed)
    card = view.payload
    if not isinstance(card, (d.Approval, d.Question)):
        return d.Invalid("schema")
    if card.lifecycle != "current":
        return withheld(view.kind, "stale-card")
    if card.content_revision != card.digest_revision:
        return withheld(view.kind, "incoherent-revision")
    if not card.source_eligible:
        return withheld(view.kind, "ineligible")
    if isinstance(card, d.Approval) and card.answer_policy == "unsupported":
        return withheld(view.kind, "unsupported-answer-policy")
    if policy.operator_npub is None:
        return withheld(view.kind, "missing-mention")
    if not _required(card):
        return withheld(view.kind, "missing-required")
    if not _protected_line_fields(card):
        return withheld(view.kind, "unsafe-content")
    refs: list[Span] = []
    if isinstance(card, d.Approval):
        for ref in card.evidence:
            rendered = evidence(ref, policy)
            if rendered is None:
                return withheld(view.kind, "unsupported-reference")
            refs.extend(rendered)
    lines = _card_lines(card, policy, tuple(refs))
    if lines is None or not safe(join(lines), policy, multiline=True, final=True):
        return withheld(view.kind, "unsafe-content")
    mention = generated("nostr:" + policy.operator_npub, public=True, atomic=True)
    workstream = _protected_layout(lines, None, policy)
    control = _protected_layout(lines, mention, policy)
    if isinstance(workstream, str):
        return withheld(view.kind, workstream)
    if isinstance(control, str):
        return withheld(view.kind, control)
    return d.Pair(workstream.text, control.text)
