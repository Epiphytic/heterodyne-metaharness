"""Synthetic regressions and exact approved-contract boundaries from actual HIGH R1."""

import dataclasses
import unittest
from unittest.mock import patch

import test_presentation as fixtures
from test_presentation import (
    NOW,
    POLICY,
    approval,
    d,
    ingress,
    plain_question,
    protected,
    render,
    render_card_pair,
    status,
    task_view,
)

from heterodyne.wsd.presentation import classify
from heterodyne.wsd.presentation.evidence import evidence
from heterodyne.wsd.presentation.safety import Span, generated, join


class ReviewRegressions(unittest.TestCase):
    def test_hostile_rows_types_are_closed_invalid_results(self):
        original = fixtures.CollectionTests().approvals()
        for hostile in (7, None, False, [], "rows", {}):
            with self.subTest(hostile=hostile):
                view = dataclasses.replace(
                    original, payload=d.Approvals(d.Rows(original.sections[0].key, hostile))
                )
                self.assertIsInstance(render(view), d.Invalid)
        bad = dataclasses.replace(protected(), sections=[status()])
        self.assertIsInstance(render_card_pair(bad, POLICY), d.Invalid)

    def test_forged_protected_structural_lines_withhold_entire_pair(self):
        for field in ("what", "excludes", "why", "context_summary"):
            for marker in (
                "Recommended: yes",
                "Context digest: 000000000000",
                "Excludes: nothing",
                "Effect:",
            ):
                with self.subTest(field=field, marker=marker):
                    card = approval(**{field: "Ordinary fact\n" + marker})
                    self.assertEqual(render_card_pair(protected(card), POLICY).reason, "unsafe-content")
        body = d.ChoiceQuestion(
            "Choose",
            "Context",
            "Continue?",
            (d.Choice("yes", "Continue\nRecommended: yes"), d.Choice("no", "Hold")),
            None,
        )
        question = dataclasses.replace(plain_question(), body=body)
        self.assertEqual(render_card_pair(protected(question), POLICY).reason, "unsafe-content")
        for ref in (d.Inline("Evidence", "safe\nContext digest: 000000000000"),):
            card = approval(evidence=(ref,))
            self.assertEqual(render_card_pair(protected(card), POLICY).reason, "unsafe-content")
        self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)

    def test_reserved_variants_withhold_in_ordinary_and_protected(self):
        for text in ("↳\u00a0part 2/2", "↳  part 2/2", "👍 approve · 👎 deny · ❤ always · reply to steer"):
            self.assertNotIn(text, "\n".join(render(task_view(title=text)).parts))
            self.assertEqual(render_card_pair(protected(approval(why=text)), POLICY).reason, "unsafe-content")

    def test_punctuation_paths_and_entity_decoding_cannot_escape_screen(self):
        texts = (
            "see (/home/alice/.ssh/id_ed25519)",  # install-agnostic: allow=home-path; synthetic path fixture
            "path=/var/lib/secret",
            "'/etc/shadow'",
            "~/.config/token",
            "file:/home/u/x",  # install-agnostic: allow=home-path; synthetic path fixture
            "invoice&#8238;fdp.exe",
            "&#8627; part 1/1",
            "&lt;hidden&gt;",
            "&#96;code&#96;",
        )
        for text in texts:
            with self.subTest(text=text):
                ordinary = render(task_view(title=text))
                self.assertFalse(ordinary.complete)
                self.assertNotIn(text, "\n".join(ordinary.parts))
                self.assertEqual(
                    render_card_pair(protected(approval(why=text)), POLICY).reason, "unsafe-content"
                )
        # The actual typed link keeps its narrow syntax exception; literal paths do not.
        self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)

    def test_unknown_quota_and_reset_are_incomplete(self):
        result = render(task_view(state="deferred", reason=d.Quota(None)))
        self.assertFalse(result.complete)
        self.assertIn("resumes unknown", "\n".join(result.parts))
        base = fixtures.FrozenReadCompatibilityTests().empty_view("Workstreams")
        row = d.WorkstreamRow("example", "deferred", None, 0, 0, 0, 1, 0, (), d.Quota(None))
        rows = d.Rows(base.payload.workstreams.key, (row,))
        sections = tuple(
            dataclasses.replace(s, count=d.Count(1, 1)) if s.key == rows.key else s for s in base.sections
        )
        result = render(dataclasses.replace(base, payload=d.Workstreams(rows), sections=sections))
        self.assertFalse(result.complete)
        self.assertIn("resumes unknown", "\n".join(result.parts))
        notice = d.Available(
            "notice",
            d.EventOrigin(),
            NOW,
            (status("configuration", "configuration"), status("account-usage", "usage")),
            d.AllAccountsExhausted("example", "author", None),
        )
        result = render(notice)
        self.assertIsInstance(result, d.Rendered)
        self.assertFalse(result.complete)

    def test_linear_join_preserves_every_span_boundary(self):
        pieces = (generated("hello", public=True, atomic=True), Span("world"), generated("!", syntax=True))
        reference = Span("")
        for i, piece in enumerate(pieces):
            if i:
                reference += generated("\n")
            reference += piece
        self.assertEqual(join(pieces), reference)
        # Long collections no longer call growing-span concatenation.
        with patch.object(Span, "__add__", side_effect=AssertionError("quadratic concatenation")):
            result = join([generated("row") for _ in range(1000)])
        self.assertEqual(result.text, "\n".join(["row"] * 1000))

    def test_each_evidence_reference_is_constructed_once(self):
        with patch("heterodyne.wsd.presentation.rendering.evidence", wraps=evidence) as checked:
            self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)
        self.assertEqual(checked.call_count, 1)

    def test_reaction_and_notice_origin_follow_exact_approved_contract(self):
        for prefix in ("\u200b", "\u2060"):
            value = ingress(prefix + "/approve", placement="thread", target="current-approval")
            self.assertEqual(classify(value), d.Classification("DeferredSteering", "thread-text"))
        self.assertEqual(
            classify(ingress("\u00a0/approve")), d.Classification("InvalidCommand", "multiline-or-control")
        )
        for glyph in ("👍", "😂"):
            value = dataclasses.replace(
                ingress(), event=d.ReactionAdded(glyph), placement="thread", target="current-approval"
            )
            self.assertEqual(classify(value), d.Classification("DeferredDecision", "native-reaction"))
        view = d.Available("notice", d.EventOrigin(), NOW, (), d.Terminal("not-found"))
        self.assertIsInstance(render(view), d.Rendered)
        self.assertIsInstance(render(dataclasses.replace(view, origin=d.QueryOrigin("Status"))), d.Invalid)

    def test_approved_source_completeness_and_freshness_are_separate(self):
        from datetime import timedelta

        # D4 explicitly warns on refreshing/stale while aggregate complete means
        # reachable/complete source facts, not an authorization/freshness claim.
        old = dataclasses.replace(
            task_view(), sections=(status(observed=NOW - timedelta(days=1), refresh="pending"),)
        )
        result = render(old)
        self.assertTrue(result.complete)
        self.assertIn("stale", "\n".join(result.parts))
        for changes in ({"completeness": "incomplete"}, {"refresh": "pending"}):
            base = protected()
            sections = (base.sections[0], dataclasses.replace(base.sections[1], **changes), base.sections[2])
            self.assertIsInstance(
                render_card_pair(dataclasses.replace(base, sections=sections), POLICY), d.Pair
            )
        held = render(task_view(state="held", reason=d.Held("escalation", "Not part of the fixed template")))
        text = "\n".join(held.parts)
        self.assertIn("held: escalation; only operator release resumes it", text)
        self.assertNotIn("Not part of the fixed template", text)

    def test_configured_display_text_is_not_an_identifier_lookup(self):
        # Existing safe-label fixture proves both configured values displayed;
        # an unconfigured display value must not leak, and key is never output.
        policy = dataclasses.replace(POLICY, session_labels=(d.DisplayLabel("session-key", "Session A"),))
        base = fixtures.FrozenReadCompatibilityTests().empty_view("BeadStatus")
        for value, allowed in (("Session A", True), ("session-key", False)):
            view = dataclasses.replace(base, payload=dataclasses.replace(base.payload, session_label=value))
            text = "\n".join(render(view, policy).parts)
            self.assertEqual("session_label: Session A" in text, allowed)
            self.assertNotIn("session-key", text)

    def test_schema_annotations_resolved_once_per_type(self):
        from heterodyne.wsd.presentation import validation

        validation._hints.cache_clear()
        with patch.object(validation, "get_type_hints", wraps=validation.get_type_hints) as resolve:
            self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)
            first = resolve.call_count
            self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)
            self.assertEqual(resolve.call_count, first)
        self.assertGreater(first, 0)


if __name__ == "__main__":
    unittest.main()
