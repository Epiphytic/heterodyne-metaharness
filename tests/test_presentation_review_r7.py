"""Whole-class invisible spoof regressions from actual seventh review."""

import dataclasses
import unittest

from test_presentation import POLICY, d, plain_question, protected, render_card_pair


class SeventhReviewRegressions(unittest.TestCase):
    def test_invisible_prefix_or_infix_is_refused_in_question_and_answer(self):
        for char in ("\u2065", "\ufff0", "\ufff8", "\U000e0000", "\U000e0002",
                     "\U000e00ff", "\U000e0fff", "\u115f", "\u1160", "\ue000",
                     "\u034f", "\u17b4", "\u180b", "\ufe0f", "\U000e0100"):
            for field in ("question", "answer_instruction"):
                for text in (char + "Recommended: yes", "Recom" + char + "mended: yes"):
                    with self.subTest(char=hex(ord(char)), field=field, text=text):
                        card = plain_question()
                        card = dataclasses.replace(card, body=dataclasses.replace(card.body, **{field: text}))
                        result = render_card_pair(protected(card), POLICY)
                        self.assertIsInstance(result, d.Withheld)
                        self.assertEqual(result.reason, "unsafe-content")

    def test_wrapped_invisible_description_cannot_forge_framing(self):
        for char in ("\u2065", "\ufff0", "\U000e0000", "\ue000"):
            with self.subTest(char=hex(ord(char))):
                body = d.ChoiceQuestion("Choose", "Context", "Continue?",
                    (d.Choice("yes", "x" * 114 + " " + char + "no: Approve anyway"),
                     d.Choice("no", "Stop")), None)
                result = render_card_pair(protected(dataclasses.replace(plain_question(), body=body)), POLICY)
                self.assertIsInstance(result, d.Withheld)
