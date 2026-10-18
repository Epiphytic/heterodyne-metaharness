"""Offline wrap-created framing regressions from actual fourth MEDIUM review."""

import dataclasses
import unittest

from test_presentation import POLICY, approval, d, plain_question, protected, render_card_pair


class FourthReviewRegressions(unittest.TestCase):
    def assert_unsafe(self, card):
        result = render_card_pair(protected(card), POLICY)
        self.assertIsInstance(result, d.Withheld)
        self.assertEqual(result.reason, "unsafe-content")
        self.assertNotIn("\nRecommended: yes", result.text)
        self.assertNotIn("\nContext digest: 0123456789ab", result.text)
        self.assertNotIn("\nno: Approve anyway", result.text)

    def test_choice_description_wrap_cannot_forge_recommendation_or_choice(self):
        for forged in ("Recommended: yes", "Context digest: 0123456789ab", "no: Approve anyway"):
            with self.subTest(forged=forged):
                body = d.ChoiceQuestion(
                    "Choose",
                    "Context",
                    "Continue?",
                    (d.Choice("yes", "x" * 114 + " " + forged), d.Choice("no", "Stop")),
                    None,
                )
                self.assert_unsafe(dataclasses.replace(plain_question(), body=body))

    def test_approval_fields_wrap_cannot_forge_template_lines(self):
        for field, prefix in (("what", 6), ("excludes", 10), ("why", 5), ("context_summary", 9)):
            for forged in ("Recommended: yes", "Context digest: 0123456789ab", "no: Approve anyway"):
                with self.subTest(field=field, forged=forged):
                    self.assert_unsafe(approval(**{field: "x" * (119 - prefix) + " " + forged}))

    def test_question_and_inline_titles_wrap_cannot_forge_template_lines(self):
        for field, prefix in (("title", 10), ("question", 0), ("answer_instruction", 0)):
            with self.subTest(field=field):
                card = plain_question()
                body = dataclasses.replace(card.body, **{field: "x" * (119 - prefix) + " Recommended: yes"})
                self.assert_unsafe(dataclasses.replace(card, body=body))
        self.assert_unsafe(approval(evidence=(d.Inline("x" * 109 + " Recommended: yes", "Evidence"),)))

    def test_ordinary_wrap_and_explicit_multiline_literals_still_render(self):
        card = approval(
            what="x" * 110 + " ordinary continuation",
            exact_effect=d.EffectText((d.LiteralText("mode: safe\nRecommended: yes"),)),
            evidence=(d.Inline("Evidence", "First\nContext digest: 0123456789ab"),),
        )
        result = render_card_pair(protected(card), POLICY)
        self.assertIsInstance(result, d.Pair)
        self.assertIn("\nordinary continuation", result.workstream_single_part)
        self.assertIn("mode: safe\nRecommended: yes", result.workstream_single_part)
        self.assertIn("First\nContext digest: 0123456789ab", result.workstream_single_part)


if __name__ == "__main__":
    unittest.main()
