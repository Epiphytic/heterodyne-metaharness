"""Named synthetic regressions for the actual second HIGH review."""

import dataclasses
import unittest
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import patch

import test_presentation as fixtures
from test_presentation import (
    POLICY,
    approval,
    d,
    plain_question,
    protected,
    render,
    render_card_pair,
    task_view,
)

from heterodyne.wsd.presentation import dto
from heterodyne.wsd.presentation.evidence import evidence
from heterodyne.wsd.presentation.rendering import Ordinary
from heterodyne.wsd.presentation.safety import Span, generated, safe


class SecondReviewRegressions(unittest.TestCase):
    def test_explicit_absence_is_none_and_known_complete(self):
        renderer = Ordinary(task_view(), POLICY)
        for name in ("current_bead", "session_label", "launch_label", "actor_label"):
            self.assertEqual(renderer.scalar(name, None).text, "none")
            self.assertTrue(renderer.complete)
        self.assertEqual(renderer.scalar("reset_at", None).text, "unknown")
        self.assertFalse(renderer.complete)
        base = fixtures.FrozenReadCompatibilityTests().empty_view("Workstreams")
        row = d.WorkstreamRow("example", "idle", None, 0, 0, 0, 0, 0, (), None)
        rows = d.Rows(base.payload.workstreams.key, (row,))
        sections = tuple(
            dataclasses.replace(item, count=d.Count(1, 1)) if item.key == rows.key else item
            for item in base.sections
        )
        result = render(dataclasses.replace(base, payload=d.Workstreams(rows), sections=sections))
        self.assertIn("current_bead: none", "\n".join(result.parts))
        self.assertTrue(result.complete)

    def test_structural_words_are_valid_exact_effect_and_choice_content(self):
        effect = "mode: safe\ncontext: prod\nevidence: retained"
        result = render_card_pair(
            protected(approval(exact_effect=d.EffectText((d.LiteralText(effect),)))), POLICY
        )
        self.assertIsInstance(result, d.Pair)
        self.assertIn(effect, result.workstream_single_part)
        body = d.ChoiceQuestion(
            "Choose",
            "Context",
            "Continue?",
            (d.Choice("context", "Context"), d.Choice("evidence", "Evidence")),
            None,
        )
        self.assertIsInstance(
            render_card_pair(protected(dataclasses.replace(plain_question(), body=body)), POLICY), d.Pair
        )

    def test_postwrap_safety_fallback_keeps_header_warning_footer(self):
        from heterodyne.wsd.presentation import rendering

        calls = 0

        def newly_unsafe(span, policy, *, final=False):
            nonlocal calls
            calls += 1
            return calls != 2

        with patch.object(rendering, "safe", side_effect=newly_unsafe):
            result = render(task_view())
        text = "\n".join(result.parts)
        self.assertIn("Task: btq-demo", text)
        self.assertIn("Observed ", text)
        self.assertIn("[withheld]", text)
        self.assertFalse(result.complete)
        # A slash is not at a token boundary in the input, but wrapping makes it
        # start a physical line. The final screen must retain warning framing.
        view = task_view(title="g" * 120 + "/private")
        view = dataclasses.replace(
            view,
            sections=(dataclasses.replace(view.sections[0], observed_at=fixtures.NOW - timedelta(days=1)),),
        )
        result = render(view)
        text = "\n".join(result.parts)
        self.assertIn("[withheld]", text)
        self.assertIn("Task: btq-demo", text)
        self.assertIn("⚠️ wsd-journal: stale", text)
        self.assertIn("Observed ", text)
        self.assertNotIn("/private", text)
        with patch.object(rendering, "safe", return_value=False):
            self.assertEqual(render(view), d.Unavailable("required-source"))

    def test_union_retains_bounds_payload_and_timestamp(self):
        self.assertEqual(render(task_view(title="x" * 4097)), d.Invalid("bounds"))
        self.assertEqual(render(task_view(title="\ud800")), d.Invalid("payload"))
        view = dataclasses.replace(task_view(), rendered_at=datetime(2026, 1, 1))
        self.assertEqual(render(view), d.Invalid("timestamp"))
        self.assertEqual(render(dataclasses.replace(task_view(), payload=7)), d.Invalid("schema"))

    def test_collections_have_field_heading_even_empty(self):
        renderer = Ordinary(fixtures.FrozenReadCompatibilityTests().empty_view("BeadStatus"), POLICY)
        payload = renderer.view.payload
        assert isinstance(payload, dto.BeadStatus)
        rows = cast(dto.BeadStatus, payload).transcript_labels
        self.assertEqual(renderer.collection("transcript_labels", rows)[0].text, "transcript_labels: none")
        self.assertEqual(
            renderer.collection("transcript_labels", rows)[-1].text, "transcripts: 0 of 0 supplied"
        )
        changed = dataclasses.replace(
            renderer.view,
            sections=tuple(
                dataclasses.replace(item, count=d.Count(1, 2), completeness="incomplete")
                if item.key == rows.key
                else item
                for item in renderer.view.sections
            ),
        )
        policy = dataclasses.replace(POLICY, transcript_labels=(d.DisplayLabel("synthetic", "Log A"),))
        lines = Ordinary(changed, policy).collection("transcript_labels", d.Rows(rows.key, ("Log A",)))
        self.assertEqual(
            [line.text for line in lines],
            ["transcript_labels:", "Log A", "transcripts: 1 of 2 supplied; source incomplete"],
        )

    def test_all_identity_positions_and_human_labels_are_validated(self):
        for label in ("fake · 0123456789ab", "UPPER", ""):
            ref = d.BeadContext(label, "abcdef012345", d.Inline("Detail", "Complete"))
            self.assertEqual(
                render_card_pair(protected(approval(evidence=(ref,))), POLICY), d.Invalid("schema")
            )
        view = fixtures.FrozenReadCompatibilityTests().empty_view("Workstreams")
        # Collection labels use the same human-label bound as all other label fields.
        from heterodyne.wsd.presentation.validation import BadInput, _semantic

        for label in ("", " padded ", "x" * 81):
            with self.assertRaises(BadInput):
                _semantic(d.Rows(view.payload.workstreams.key, (label,)))

    def test_deferred_unknown_keeps_state_and_other_reason_keeps_observed_state(self):
        self.assertIn(
            "⏸️ deferred: unknown", "\n".join(render(task_view(state="deferred", reason=d.Unknown())).parts)
        )
        self.assertIn(
            "✅ closed; deferred: quota",
            "\n".join(render(task_view(state="closed", reason=d.Quota(None))).parts),
        )

    def test_atomic_label_can_move_after_prefix_without_being_split(self):
        renderer = Ordinary(task_view(), POLICY)
        from heterodyne.wsd.presentation.layout import wrap

        row = d.WorkstreamRow("example", "running", "g" * 110, 0, 0, 0, 0, 0, (), None)
        line = next(item for item in renderer.row(row, 0) if item.text.startswith("current_bead:"))
        parts = wrap(line)
        self.assertEqual(parts[-1].text, "g" * 110)
        ref = d.BeadContext("g" * 128, "abcdef012345", d.Inline("Detail", "Complete"))
        result = render_card_pair(protected(approval(evidence=(ref,))), POLICY)
        self.assertEqual(result.reason, "content-too-wide")
        encoded = evidence(ref, POLICY)
        self.assertIsNotNone(encoded)
        assert encoded is not None
        self.assertTrue(encoded[0].unbreakable)

    def test_label_and_launch_collection_duplicate_identity(self):
        from heterodyne.wsd.presentation.sources import validate_view
        from heterodyne.wsd.presentation.validation import BadInput, _semantic

        base = fixtures.FrozenReadCompatibilityTests().empty_view("BeadStatus")
        row = base.payload.launches
        launch = d.Launch(1, "author", None, "unknown", None)
        payload = dataclasses.replace(base.payload, launches=d.Rows(row.key, (launch, launch)))
        sections = tuple(
            dataclasses.replace(s, count=d.Count(2, 2)) if s.key == row.key else s for s in base.sections
        )
        with self.assertRaises(BadInput):
            validate_view(dataclasses.replace(base, payload=payload, sections=sections))
        with self.assertRaises(BadInput):
            _semantic(d.Rows(row.key, ("Label", "Label")))

    def test_home_paths_and_uppercase_commands_are_not_exempt(self):
        for text in ("~/stop", "~/queue,", "/STATUS", "/QUEUE", "/status/file"):
            self.assertFalse(safe(Span(text), POLICY))
        for text in ("/status", "/queue,", "/stop!"):
            self.assertTrue(safe(Span(text), POLICY))
        self.assertTrue(
            safe(generated("👍 approve · 👎 deny · ❤️ always · reply to steer"), POLICY, final=True)
        )


if __name__ == "__main__":
    unittest.main()
