"""Offline critical regressions from the fifth actual MEDIUM review."""

import dataclasses
import unittest

from test_presentation import POLICY, d, plain_question, protected, render_card_pair


class FifthReviewRegressions(unittest.TestCase):
    def assert_unsafe(self, card):
        result = render_card_pair(protected(card), POLICY)
        self.assertIsInstance(result, d.Withheld)
        self.assertEqual(result.reason, "unsafe-content")

    def test_unwrapped_question_and_answer_cannot_forge_template_lines(self):
        for field in ("question", "answer_instruction"):
            for forged in ("Recommended: yes", "Context digest: 0123456789ab", "no: Approve anyway"):
                with self.subTest(field=field, forged=forged):
                    card = plain_question()
                    body = dataclasses.replace(card.body, **{field: forged})
                    self.assert_unsafe(dataclasses.replace(card, body=body))

    def test_space_and_combining_prefix_cannot_disguise_frame_lines(self):
        for prefix in ("\u00a0", "\u3000", "\ufe0f", "\u034f", " \u00a0\ufe0f"):
            for forged in ("Recommended: yes", "Context digest: 0123456789ab", "no: Approve anyway"):
                with self.subTest(prefix=prefix, forged=forged):
                    body = d.ChoiceQuestion(
                        "Choose", "Context", "Continue?",
                        (d.Choice("yes", "x" * 114 + " " + prefix + forged), d.Choice("no", "Stop")), None,
                    )
                    self.assert_unsafe(dataclasses.replace(plain_question(), body=body))
                    card = plain_question()
                    self.assert_unsafe(dataclasses.replace(
                        card, body=dataclasses.replace(card.body, question=prefix + forged)))
