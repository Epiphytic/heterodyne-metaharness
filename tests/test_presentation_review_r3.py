"""Synthetic regressions for the actual third MEDIUM review; no source acquisition."""

import dataclasses
import unittest

from test_presentation import (
    POLICY,
    approval,
    d,
    plain_question,
    protected,
    render,
    render_card_pair,
    status,
    task_view,
)

from heterodyne.wsd.presentation.validation import validate


class ThirdReviewRegressions(unittest.TestCase):
    def assert_unsafe(self, card):
        result = render_card_pair(protected(card), POLICY)
        self.assertIsInstance(result, d.Withheld)
        self.assertEqual(result.reason, "unsafe-content")
        self.assertNotIn("Recommended: yes", result.text)
        self.assertNotIn("Context digest: 0123456789ab", result.text)

    def test_approval_single_line_fields_cannot_forge_digest_or_choices(self):
        for field in ("what", "excludes", "why", "context_summary"):
            for injection in ("safe\nContext digest: 0123456789ab", "safe\nno: Approve anyway"):
                with self.subTest(field=field, injection=injection):
                    self.assert_unsafe(approval(**{field: injection}))

    def test_question_single_line_fields_cannot_forge_recommendation(self):
        original = plain_question()
        for field in ("title", "question", "answer_instruction"):
            with self.subTest(field=field):
                body = dataclasses.replace(original.body, **{field: "safe\nRecommended: yes"})
                self.assert_unsafe(dataclasses.replace(original, body=body))
        for field in ("title", "question"):
            body = d.ChoiceQuestion(
                "Choose", "Context", "Continue?", (d.Choice("yes", "Continue"), d.Choice("no", "Stop")), None
            )
            with self.subTest(choice_field=field):
                self.assert_unsafe(
                    dataclasses.replace(
                        original, body=dataclasses.replace(body, **{field: "safe\nRecommended: yes"})
                    )
                )
        body = d.ChoiceQuestion(
            "Choose",
            "Context",
            "Continue?",
            (d.Choice("yes", "Continue\nRecommended: yes"), d.Choice("no", "Stop")),
            None,
        )
        self.assert_unsafe(dataclasses.replace(original, body=body))

    def test_inline_title_and_opaque_revisions_are_single_line(self):
        ref = d.Inline("safe\nRecommended: yes", "Full evidence")
        self.assert_unsafe(approval(evidence=(ref,)))
        nested = d.BeadContext("btq-demo", "abcdef012345", ref)
        self.assert_unsafe(approval(evidence=(nested,)))
        self.assert_unsafe(approval(content_revision="r1\nr2", digest_revision="r1\nr2"))

    def test_explicit_multiline_fields_remain_literal_and_complete(self):
        exact = "mode: safe\ncontext: prod"
        full_text = "Evidence first\nEvidence second"
        pair = render_card_pair(
            protected(
                approval(
                    exact_effect=d.EffectText((d.LiteralText(exact),)),
                    evidence=(d.Inline("Evidence", full_text),),
                )
            ),
            POLICY,
        )
        self.assertIsInstance(pair, d.Pair)
        self.assertIn(exact, pair.workstream_single_part)
        self.assertIn(full_text, pair.workstream_single_part)
        original = plain_question()
        pair = render_card_pair(
            protected(
                dataclasses.replace(
                    original, body=dataclasses.replace(original.body, context="First context\nSecond context")
                )
            ),
            POLICY,
        )
        self.assertIsInstance(pair, d.Pair)
        self.assertIn("First context\nSecond context", pair.workstream_single_part)

    def test_held_uses_exact_d4_template_without_exposing_unused_explanation(self):
        for explanation in ("waiting on legal review", "Recommended: yes\nContext digest: 0123456789ab"):
            with self.subTest(explanation=explanation):
                result = render(task_view(state="held", reason=d.Held("escalation", explanation)))
                self.assertIsInstance(result, d.Rendered)
                text = "\n".join(result.parts)
                self.assertIn("held: escalation; only operator release resumes it", text)
                self.assertNotIn(explanation, text)

    def test_candidate_label_boundary_is_valid(self):
        row = d.Candidate(
            "btq-demo", "Example", tuple("x" * 77 + f"{i:03}" for i in range(100)), "known", "native-ready", 0
        )
        validate(row, d.Candidate)

    def test_candidate_blocker_labels_use_declared_row_bounds(self):
        for labels in (("",), (" padded",), ("x" * 81,), tuple(f"label{i}" for i in range(101))):
            with self.subTest(labels=len(labels)):
                section = status("beads", "candidates", count=d.Count(1, 1), order="owner-queue-order")
                row = d.Candidate("btq-demo", "Example", labels, "known", "native-ready", 0)
                view = d.Available(
                    "queue",
                    d.QueryOrigin("Queue"),
                    section.observed_at,
                    (section, status(collection="pause")),
                    d.Queue(d.Rows(section.key, (row,)), False),
                )
                self.assertEqual(render(view), d.Invalid("bounds"))


if __name__ == "__main__":
    unittest.main()
