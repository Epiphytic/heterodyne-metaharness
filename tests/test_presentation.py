"""Synthetic, pure Plan6A contracts. No harness/native or transport fixtures."""

import dataclasses
import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import heterodyne.wsd.presentation as presentation
from heterodyne.marmot.nip19 import hex_to_npub
from heterodyne.wsd.presentation import dto
from heterodyne.wsd.presentation.layout import paginate, wrap
from heterodyne.wsd.presentation.safety import Span, generated, safe

# Dynamic fixture construction intentionally exercises invalid runtime DTOs and union results.
# Production modules remain fully type checked.
d: Any = dto
classify: Any = presentation.classify
render: Any = presentation.render
render_card_pair: Any = presentation.render_card_pair

NOW = datetime(2026, 10, 16, 12, 3, 4, 123456, tzinfo=UTC)
PIN = "1" * 40
REPO = d.Repo("github", "https://github.com", "example", "project")
POLICY = d.RenderPolicy(operator_npub=hex_to_npub("2" * 64), forges=(REPO,))


def status(
    source="wsd-journal",
    collection="state",
    *,
    required=True,
    count=None,
    scope=None,
    order="none",
    observed=NOW,
    availability="reachable",
    completeness="complete",
    refresh="idle",
):
    return d.SectionStatus(
        d.SectionKey(source, collection, scope),
        observed,
        availability,
        completeness,
        refresh,
        count,
        order,
        required,
    )


def task(**changes):
    return dataclasses.replace(
        d.Task("btq-demo", "Example", "codex", "author", "feature", "running", None, 100), **changes
    )


def task_view(**changes):
    return d.Available("task", d.EventOrigin(), NOW, (status(),), task(**changes))


def approval(**changes):
    base = d.Approval(
        "btq-demo",
        "design",
        "Build example",
        d.EffectText((d.LiteralText("set mode safe"),)),
        "deployment",
        "Enable inspection",
        "Bounded synthetic example",
        (d.File(REPO, PIN, "docs/design.md", 2, 5),),
        "abcdef012345",
        "current",
        "r1",
        "r1",
        True,
        "wsd-standard",
    )
    return dataclasses.replace(base, **changes)


def protected(card=None, *, sections=None):
    card = approval() if card is None else card
    kind = "approval" if isinstance(card, d.Approval) else "question"
    own = status("beads", "approvals") if kind == "approval" else status(collection="needs-human")
    observed = (status("configuration", "configuration"), own, status(collection="decisions"))
    return d.Available(kind, d.EventOrigin(), NOW, observed if sections is None else sections, card)


def plain_question():
    return d.Question(
        "current",
        "r1",
        "r1",
        "abcdef012345",
        True,
        d.PlainQuestion("Continue example?", "Synthetic context", "Continue?", "Reply yes or no."),
    )


def ingress(text="/status", **changes):
    return dataclasses.replace(
        d.ClassificationInput("workstream", "verified", False, d.Text(text), "root", "none"), **changes
    )


class GoldenTests(unittest.TestCase):
    def test_golden_task_and_protected_bytes(self):
        expected = json.loads((Path(__file__).parent / "fixtures/presentation/golden.json").read_text())
        self.assertEqual(render(task_view()), d.Rendered((expected["task"],), "task", True, ()))
        pair = render_card_pair(protected(), POLICY)
        self.assertIsInstance(pair, d.Pair)
        self.assertEqual(pair.workstream_single_part, expected["approval"])
        self.assertEqual(
            pair.control_single_part,
            expected["approval"].replace(
                "Approval (design) btq-demo", "Approval (design) btq-demo nostr:" + POLICY.operator_npub
            ),
        )
        question = render_card_pair(protected(plain_question()), POLICY)
        self.assertEqual(question.workstream_single_part, expected["question"])

    def test_fixed_terminal_catalog(self):
        expected = {
            "busy": "Read service busy; try again later.",
            "usage": "Use /workstreams, /status [bead], /approvals, or /queue [workstream].",
            "not-found": "Item unavailable in this scope.",
            "unsupported": "This action is unavailable on the read surface.",
            "unavailable": "Data unavailable; try again later.",
            "narrow-query": "Too much detail for this view. No partial content shown.",
        }
        for reason, text in expected.items():
            with self.subTest(reason=reason):
                view = d.Available("notice", d.EventOrigin(), NOW, (), d.Terminal(reason))
                self.assertEqual(render(view), d.Rendered((text,), "notice", True, ()))

    def test_no_effects_and_environment_determinism(self):
        expected = render(task_view())
        for values in ({"TZ": "Pacific/Honolulu", "LANG": "C"}, {"TZ": "UTC", "LANG": "tr_TR.UTF-8"}):
            with (
                patch.dict("os.environ", values),
                patch("builtins.open", side_effect=AssertionError),
                patch("socket.socket", side_effect=AssertionError),
                patch("subprocess.Popen", side_effect=AssertionError),
            ):
                self.assertEqual(render(task_view()), expected)
                self.assertIsInstance(render_card_pair(protected(), POLICY), d.Pair)
                self.assertEqual(classify(ingress()).kind, "ReadQuery")
        self.assertNotIn("heterodyne.wsd.commands", sys.modules)
        self.assertNotIn("heterodyne.admind.daemon", sys.modules)

    def test_import_does_not_perform_application_io(self):
        script = """import builtins, socket, subprocess, os
old = builtins.open
def deny(*a, **kw): raise AssertionError('application I/O')
builtins.open = socket.socket = subprocess.Popen = os.getenv = deny
import heterodyne.wsd.presentation
assert 'heterodyne.wsd.commands' not in __import__('sys').modules
"""
        run = subprocess.run(
            [sys.executable, "-B", "-c", script], capture_output=True, text=True, check=False
        )
        self.assertEqual(run.returncode, 0, run.stderr)


class ClassificationTests(unittest.TestCase):
    def test_six_queries_surfaces_and_thread_targets(self):
        cases = {
            "/workstreams": ("Workstreams", None),
            "/status": ("Status", None),
            "/status btq-demo": ("BeadStatus", "btq-demo"),
            "/approvals": ("Approvals", None),
            "/queue": ("Queue", None),
            "/queue project..one": ("QueueFor", "project..one"),
        }
        for surface in ("control", "workstream"):
            for target in (
                "none",
                "current-task",
                "current-approval",
                "current-question",
                "stale",
                "unknown",
            ):
                for text, (query, selector) in cases.items():
                    got = classify(
                        ingress(text, surface=surface, placement="thread", target=target, suspended=True)
                    )
                    self.assertEqual(got, d.Classification("ReadQuery", query, selector))

    def test_headers_before_hostile_payload(self):
        for state in ("unverified", "rejected", "replayed"):
            self.assertEqual(
                classify(ingress(event=object(), ingress=state)), d.Classification("Ignored", state)
            )
        self.assertEqual(classify(ingress(event=object())), d.Invalid("payload"))
        self.assertEqual(classify(ingress(ingress="invented")), d.Invalid("schema"))
        self.assertEqual(classify(ingress(version=True)), d.Invalid("schema"))
        self.assertEqual(classify(ingress(target="current-task")), d.Invalid("schema"))

    def test_parser_boundaries_and_mutation_precedence(self):
        self.assertEqual(
            classify(ingress("  /status   btq-demo ")),
            d.Classification("ReadQuery", "BeadStatus", "btq-demo"),
        )
        for text in (
            "/status\tx",
            "/status\nx",
            "/status -x",
            "/status X",
            "/status ..",
            "/queue x/y",
            "/release",
            "/workstreams extra",
            "/status " + "a" * 129,
            "\u00a0/status",
            "/status\u200bx",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify(ingress(text)).kind, "InvalidCommand")
        self.assertEqual(classify(ingress("/queue " + "a" * 128)).selector, "a" * 128)
        for command in ("approve", "deny", "pause", "resume", "stop", "harness", "trust-group"):
            self.assertEqual(classify(ingress("/" + command + " ignored arguments")).kind, "UnsupportedIn6A")

    def test_intake_steering_and_native_reaction_are_distinct(self):
        self.assertEqual(classify(ingress("hello")).kind, "DeferredIntake")
        self.assertEqual(classify(ingress("hello", surface="control")).kind, "Ignored")
        for target in ("current-task", "current-question", "current-approval"):
            self.assertEqual(
                classify(ingress("👍", placement="thread", target=target)).kind, "DeferredSteering"
            )
        self.assertEqual(
            classify(ingress("yes", placement="thread", target="current-approval", suspended=True)).kind,
            "Ignored",
        )
        self.assertEqual(
            classify(
                ingress(event=d.ReactionAdded("👍"), placement="thread", target="current-approval")
            ).kind,
            "DeferredDecision",
        )
        for event in (d.ReactionRemoved("👍"), d.ReactionAdded("👍")):
            self.assertEqual(
                classify(ingress(event=event, placement="thread", target="current-question")).kind, "Ignored"
            )
        for target in ("none", "unknown", "stale"):
            self.assertEqual(
                classify(ingress("hello", placement="thread", target=target)).kind, "UnresolvedTarget"
            )


class ValidationTests(unittest.TestCase):
    def test_invalid_direct_construction_never_echoes(self):
        views = [
            dataclasses.replace(task_view(), version=2),
            task_view(duration_us=True),
            task_view(duration_us=-1),
            task_view(state="invented"),
            task_view(title="\ud800"),
            task_view(title="x" * 4097),
            dataclasses.replace(task_view(), rendered_at=NOW.replace(tzinfo=None)),
            dataclasses.replace(task_view(), rendered_at=NOW.astimezone(timezone(timedelta(hours=1)))),
        ]
        for view in views:
            with self.subTest(view_type=type(view)):
                got = render(view)
                self.assertIsInstance(got, d.Invalid)
                self.assertNotIn("invented", str(got))
        with self.assertRaises(TypeError):
            d.Task(**{"unexpected": "x"})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            task().title = "changed"

    def test_reason_state_consistency_and_literal_labels(self):
        for state, reason, expected in (
            (
                "held",
                d.Held("operator-stop", "Wait"),
                "held: operator-stop; only operator release resumes it",
            ),
            ("deferred", d.Quota(NOW), "deferred: quota; resumes 2026-10-16T12:03:04.123456Z"),
            ("deferred", d.Quota(None), "deferred: quota; resumes unknown"),
            ("deferred", d.AccountChanged(), "deferred: account changed"),
            ("deferred", d.Unknown(), "unknown"),
        ):
            got = render(task_view(state=state, reason=reason))
            self.assertIsInstance(got, d.Rendered)
            self.assertIn(expected, "\n".join(got.parts))
        self.assertIsInstance(render(task_view(state="held")), d.Invalid)
        self.assertIsInstance(render(task_view(state="deferred", reason=d.Blocked("x"))), d.Invalid)

    def test_source_precedence_and_freshness_boundaries(self):
        self.assertEqual(
            render(dataclasses.replace(task_view(), sections=())), d.Unavailable("required-source")
        )
        self.assertEqual(render(d.Missing("task", d.EventOrigin())), d.Unavailable("missing-snapshot"))
        self.assertEqual(
            render_card_pair(d.Missing("approval", d.EventOrigin()), POLICY).reason, "missing-required"
        )
        for age, expected in ((30, d.Pair), (30.000001, d.Withheld), (-1, d.Withheld)):
            sections = tuple(
                dataclasses.replace(s, observed_at=NOW - timedelta(seconds=age)) for s in protected().sections
            )
            self.assertIsInstance(render_card_pair(protected(sections=sections), POLICY), expected)
        stale = dataclasses.replace(status(), observed_at=NOW - timedelta(days=1), refresh="pending")
        text = "\n".join(render(dataclasses.replace(task_view(), sections=(stale,))).parts)
        self.assertIn("wsd-journal: stale", text)
        self.assertIn("2026-10-15T12:03:04.123456Z", text)
        future = dataclasses.replace(status(), observed_at=NOW + timedelta(seconds=1))
        self.assertIn(
            "time-unverified", "\n".join(render(dataclasses.replace(task_view(), sections=(future,))).parts)
        )


class SafetyTests(unittest.TestCase):
    def test_whole_field_hostile_catalog(self):
        attacks = (
            "secret nsec1" + "q" * 60,
            "did:example:abc",
            "a@b.example",  # install-agnostic: allow=email; synthetic hostile content, not config
            "a" * 40,
            "00000000-0000-0000-0000-000000000000",
            "/private/file",
            "/status/file",
            "C:\\private",
            "\\\\host",
            "https://example.invalid",
            "www.example.invalid",
            "[x](target)",
            "<tag>",
            "`code`",
            "nostr:npub1bad",
            "\u202ehidden",
            "\u200d",
            "\t",
            "\n",
            "\u2800",
            "↳ part 1/2",
            "👍 approve · 👎 deny · ❤️ always · reply to steer",
        )
        for text in attacks:
            with self.subTest(text=text[:20]):
                result = render(task_view(title="prefix " + text + " suffix"))
                self.assertIsInstance(result, d.Rendered)
                self.assertFalse(result.complete)
                self.assertIn("title: [withheld]", "\n".join(result.parts))
                blocked = render_card_pair(protected(approval(what=text)), POLICY)
                self.assertEqual(blocked.reason, "unsafe-content") if text != "\n" else self.assertIsInstance(
                    blocked, d.Pair
                )
                if isinstance(blocked, d.Withheld):
                    self.assertNotIn("👍", blocked.text)
        policy = dataclasses.replace(POLICY, login_dir_forms=("private-alias",))
        self.assertFalse(render(task_view(title="private-alias"), policy).complete)

    def test_narrow_public_hashes_and_cross_span_screening(self):
        allowed = approval(
            exact_effect=d.EffectText((d.LiteralText("pin "), d.PublicHash("sha256", "a" * 64)))
        )
        self.assertIsInstance(render_card_pair(protected(allowed), POLICY), d.Pair)
        split = approval(exact_effect=d.EffectText((d.LiteralText("a" * 20), d.LiteralText("a" * 20))))
        self.assertEqual(render_card_pair(protected(split), POLICY).reason, "unsafe-content")
        crossed = approval(exact_effect=d.EffectText((d.LiteralText("a"), d.PublicHash("sha1", "a" * 40))))
        self.assertEqual(render_card_pair(protected(crossed), POLICY).reason, "unsafe-content")
        for owner, path in (("a" * 40, "readme.md"), ("example", "a" * 40 + ".md")):
            repo = dataclasses.replace(REPO, owner=owner)
            policy = dataclasses.replace(POLICY, forges=(repo,))
            card = approval(evidence=(d.File(repo, PIN, path),))
            self.assertEqual(render_card_pair(protected(card), policy).reason, "unsafe-content")

    def test_exact_effect_fence_and_width(self):
        effect = "value <old> ``` value"
        pair = render_card_pair(
            protected(approval(exact_effect=d.EffectText((d.LiteralText(effect),)))), POLICY
        )
        self.assertIsInstance(pair, d.Pair)
        self.assertIn("\n````\n" + effect + "\n````\n", pair.workstream_single_part)
        card = approval(exact_effect=d.EffectText((d.LiteralText("x" * 121),)))
        self.assertEqual(render_card_pair(protected(card), POLICY).reason, "content-too-wide")
        ordinary = render(task_view(bead="z" * 128))
        self.assertEqual(ordinary, d.NeedsDetails("content-too-wide"))
        for label in ("/status", "branch-a/b", "ordinary safe text"):
            self.assertTrue(safe(Span(label), POLICY))


class ProtectedTests(unittest.TestCase):
    def test_every_withheld_reason_priority(self):
        cases = (
            (protected(sections=()), POLICY, "required-source"),
            (
                protected(
                    sections=tuple(dataclasses.replace(s, observed_at=None) for s in protected().sections)
                ),
                POLICY,
                "stale-card",
            ),
            (protected(approval(lifecycle="superseded")), POLICY, "stale-card"),
            (protected(approval(digest_revision="r2")), POLICY, "incoherent-revision"),
            (protected(approval(source_eligible=False)), POLICY, "ineligible"),
            (protected(approval(answer_policy="unsupported")), POLICY, "unsupported-answer-policy"),
            (protected(), d.RenderPolicy(), "missing-mention"),
            (
                protected(approval(evidence=(d.OperatorDoc("material", "abcdef012345"),))),
                POLICY,
                "unsupported-reference",
            ),
            (protected(approval(what="[fake](url)")), POLICY, "unsafe-content"),
            (
                protected(approval(exact_effect=d.EffectText((d.LiteralText("z" * 121),)))),
                POLICY,
                "content-too-wide",
            ),
            (protected(approval(what="word " * 800)), POLICY, "content-too-large"),
            (protected(approval(why="")), POLICY, "missing-required"),
        )
        for view, policy, reason in cases:
            with self.subTest(reason=reason):
                result = render_card_pair(view, policy)
                self.assertIsInstance(result, d.Withheld)
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(result.text.splitlines()), 2)
                self.assertNotIn("Context digest:", result.text)
                self.assertNotIn("nostr:", result.text)
        self.assertEqual(render(protected()), d.Invalid("protected-pair-required"))
        self.assertEqual(render_card_pair(task_view(), POLICY), d.Invalid("schema"))

    def test_choice_question_recommended_no(self):
        body = d.ChoiceQuestion(
            "Continue?",
            "Costs one slot",
            "Choose one",
            (d.Choice("yes", "Continue"), d.Choice("no", "Hold")),
            "no",
        )
        card = dataclasses.replace(plain_question(), body=body)
        pair = render_card_pair(protected(card), POLICY)
        self.assertEqual(
            pair.workstream_single_part,
            "Question: Continue?\nContext: Costs one slot\nChoose one\nyes: Continue\nno: Hold\n"
            "Recommended: no\nReply with a choice label.\nContext digest: abcdef012345",
        )
        self.assertNotIn("👍", pair.control_single_part)
        for wrong in (
            dataclasses.replace(body, recommended_label="other"),
            dataclasses.replace(body, choices=(d.Choice("yes", "One"), d.Choice("yes", "Two"))),
            dataclasses.replace(body, choices=(d.Choice("YES", "One"), d.Choice("no", "Two"))),
        ):
            self.assertIsInstance(
                render_card_pair(protected(dataclasses.replace(card, body=wrong)), POLICY), d.Invalid
            )
        plain = dataclasses.replace(
            card, body=d.PlainQuestion("Title", "Context", "Question", "Say a sentence.")
        )
        self.assertIn("Say a sentence.", render_card_pair(protected(plain), POLICY).workstream_single_part)
        no_purpose = render_card_pair(protected(approval(purpose=None)), POLICY)
        self.assertTrue(no_purpose.workstream_single_part.startswith("Approval btq-demo\n"))

    def test_pair_never_accepts_one_oversized_mirror(self):
        base = render_card_pair(protected(), POLICY)
        plain_size = len(base.workstream_single_part)
        self.assertGreater(len(base.control_single_part), plain_size)
        pair = render_card_pair(protected(), dataclasses.replace(POLICY, max_chars=plain_size))
        self.assertEqual(pair.reason, "content-too-large")
        card = approval(exact_effect=d.EffectText((d.LiteralText("\n".join(["x"] * 30)),)))
        self.assertEqual(render_card_pair(protected(card), POLICY).reason, "content-too-large")

    def test_evidence_closed_union_and_exact_links(self):
        urls = (
            (d.File(REPO, PIN, "docs/a.md"), f"https://github.com/example/project/blob/{PIN}/docs/a.md"),
            (d.Commit(REPO, PIN), f"https://github.com/example/project/commit/{PIN}"),
            (
                d.PullRequest(REPO, 12, PIN),
                f"https://github.com/example/project/pull/12\n"
                f"https://github.com/example/project/commit/{PIN}",
            ),
            (
                d.Range(REPO, PIN, "2" * 40),
                PIN + ".." + "2" * 40 + f"\nhttps://github.com/example/project/compare/{PIN}..." + "2" * 40,
            ),
            (d.Range(REPO, PIN, PIN), f"https://github.com/example/project/compare/{PIN}...{PIN}"),
            (d.Inline("Complete doc", "All content"), "Evidence: Complete doc\nAll content"),
            (
                d.BeadContext("btq-demo", "abcdef012345", d.Inline("Full", "Details")),
                "Evidence: btq-demo · abcdef012345\nEvidence: Full\nDetails",
            ),
        )
        for ref, expected in urls:
            with self.subTest(ref_type=type(ref)):
                pair = render_card_pair(protected(approval(evidence=(ref,))), POLICY)
                self.assertIsInstance(pair, d.Pair)
                self.assertIn(expected, pair.workstream_single_part)
        for forge, origin in (("gitlab", "https://gitlab.com"), ("other", "https://example.com")):
            repo = d.Repo(forge, origin, "example", "project")
            result = render_card_pair(protected(approval(evidence=(d.Commit(repo, PIN),))), POLICY)
            self.assertEqual(result.reason, "unsupported-reference")
        for bad in (
            d.File(REPO, "main", "a.md"),
            d.File(REPO, PIN, "../a.md"),
            d.File(REPO, PIN, "a%20.md"),
            d.File(REPO, PIN, "a.md", 5, 2),
            d.File(REPO, PIN, "a.md", None, 2),
            d.PullRequest(REPO, 0, PIN),
            d.File(
                d.Repo(
                    "github",
                    "https://user@github.com",  # install-agnostic: allow=email; synthetic forbidden URL
                    "x",
                    "y",
                ),
                PIN,
                "a.md",
            ),
        ):
            self.assertIsInstance(render_card_pair(protected(approval(evidence=(bad,))), POLICY), d.Invalid)
        wide = d.File(REPO, PIN, ("x" * 80 + "/") * 6 + "a.md")
        self.assertEqual(
            render_card_pair(protected(approval(evidence=(wide,))), POLICY).reason, "content-too-wide"
        )


class CollectionTests(unittest.TestCase):
    def approvals(self, items=(), **changes):
        section = status(
            "beads", "approvals", count=d.Count(len(items), len(items)), order="approvals-created-key"
        )
        section = dataclasses.replace(section, **changes)
        return d.Available(
            "approvals", d.QueryOrigin("Approvals"), NOW, (section,), d.Approvals(d.Rows(section.key, items))
        )

    def test_collection_all_rows_and_counts_never_prefix(self):
        items = tuple(
            d.ApprovalSummary("btq-x" + str(i), "Title " + str(i), "open", NOW, f"{i:02x}") for i in range(6)
        )
        result = render(self.approvals(items, count=d.Count(6, 9), completeness="incomplete"))
        self.assertIsInstance(result, d.Rendered)
        text = "\n".join(result.parts)
        for i in range(6):
            self.assertIn("Title " + str(i), text)
        self.assertIn("approvals: 6 of 9 supplied; source incomplete", text)
        self.assertFalse(result.complete)
        for i, part in enumerate(result.parts):
            self.assertLessEqual(len(part.splitlines()), 8)
            self.assertTrue(part.endswith(f"↳ part {i + 1}/{len(result.parts)}"))
            self.assertLessEqual(len(part), 2000)
        excess = tuple(
            d.ApprovalSummary("btq-x" + str(i), "Title", "open", NOW, f"{i:02x}") for i in range(100)
        )
        self.assertEqual(render(self.approvals(excess)), d.NeedsDetails("excessive-parts"))
        self.assertIsInstance(render(self.approvals((*excess, excess[0]))), d.Invalid)

    def test_order_duplicate_scope_and_count_fences(self):
        rows = (
            d.ApprovalSummary("btq-a", "First", "open", NOW, "01"),
            d.ApprovalSummary("btq-b", "Second", "open", NOW, "02"),
        )
        for view in (
            self.approvals(rows[::-1]),
            self.approvals((rows[0], rows[0])),
            self.approvals((dataclasses.replace(rows[0], opaque_order_key="1"),)),
            self.approvals(rows, count=d.Count(1, 2)),
            self.approvals(rows, count=d.Count(True, 2)),
            self.approvals(rows, key=d.SectionKey("beads", "approvals", "other")),
        ):
            self.assertIsInstance(render(view), d.Invalid)
        self.assertEqual(render(self.approvals(rows, order="unknown")), d.Unavailable("required-source"))
        unavailable = self.approvals((), availability="unavailable", completeness="unavailable", count=None)
        self.assertEqual(render(unavailable), d.Unavailable("required-source"))
        self.assertEqual(
            render(dataclasses.replace(self.approvals(), sections=(self.approvals().sections * 2))),
            d.Invalid("duplicate-key"),
        )

    def test_queue_order_pause_and_no_authority(self):
        candidates = (
            d.Candidate("btq-a", "Resume item", (), "known", "resumable", 0),
            d.Candidate("btq-b", "New item", ("awaiting input",), "unknown", "native-ready", 1),
        )
        section = status("beads", "candidates", count=d.Count(2, 3), order="owner-queue-order")
        view = d.Available(
            "queue",
            d.QueryOrigin("Queue"),
            NOW,
            (section, status(collection="pause")),
            d.Queue(d.Rows(section.key, candidates), None),
        )
        result = render(view)
        text = "\n".join(result.parts)
        self.assertIn("Candidate order; actual pickup can skip or change", text)
        self.assertIn("paused: unknown", text)
        self.assertIn("eligibility: unknown", text)
        self.assertFalse(result.complete)
        for invalid in (
            (dataclasses.replace(candidates[0], ordinal=1), candidates[1]),
            (
                dataclasses.replace(candidates[0], segment="native-ready"),
                dataclasses.replace(candidates[1], segment="resumable"),
            ),
        ):
            self.assertIsInstance(
                render(dataclasses.replace(view, payload=d.Queue(d.Rows(section.key, invalid), None))),
                d.Invalid,
            )

    def test_scoped_source_owner_and_optional_unknown(self):
        scope = "project"
        keys = [("wsd-journal", field) for field in ("running", "ready", "parked", "held", "deferred")]
        keys += [("beads", "approvals"), ("account-usage", "usage")]
        unavailable = tuple(
            status(
                src,
                col,
                required=False,
                count=None,
                scope=scope,
                availability="unavailable",
                completeness="unavailable",
            )
            for src, col in keys
        )
        rows = tuple(d.Rows(s.key, ()) for s in unavailable)
        payload = d.WorkstreamStatus(scope, "unknown", *rows)
        view = d.Available(
            "workstream-status",
            d.QueryOrigin("Status", scope),
            NOW,
            (status("configuration", "configuration", scope=scope), status(scope=scope), *unavailable),
            payload,
        )
        got = render(view)
        self.assertIsInstance(got, d.Rendered)
        text = "\n".join(got.parts)
        self.assertFalse(got.complete)
        for name in ("running", "ready", "parked", "held", "deferred", "approvals", "usage"):
            self.assertIn(name + ": unknown", text)
        self.assertIsInstance(
            render(dataclasses.replace(view, payload=dataclasses.replace(payload, name="other"))), d.Invalid
        )
        # A status for one collection cannot attest another collection's rows.
        wrong = dataclasses.replace(payload, running=d.Rows(unavailable[-2].key, ()))
        self.assertIsInstance(render(dataclasses.replace(view, payload=wrong)), d.Invalid)

    def test_eight_lines_twenty_pages_and_atomic_move(self):
        header, footer = (generated("H"),), (generated("F"),)
        pages = paginate(header, tuple(generated(str(i)) for i in range(100)), footer, 2000)
        assert isinstance(pages, tuple)
        self.assertEqual(len(pages), 20)
        self.assertEqual(pages[0].text, "H\n0\n1\n2\n3\n4\nF\n↳ part 1/20")
        self.assertEqual(
            paginate(header, tuple(generated(str(i)) for i in range(101)), footer, 2000),
            d.NeedsDetails("excessive-parts"),
        )
        joined = generated("prefix " * 10) + Span("z" * 80, atomic=True)
        self.assertEqual(tuple(x.text for x in wrap(joined)), ("prefix " * 9 + "prefix", "z" * 80))


class NoticeTests(unittest.TestCase):
    def notice(self, item, pairs):
        return d.Available(
            "notice", d.EventOrigin(), NOW, tuple(status(src, col) for src, col in pairs), item
        )

    def test_every_notice_and_fyi_exact_without_mentions(self):
        cfg = ("configuration", "configuration")
        state = ("wsd-journal", "state")
        cases = (
            (
                d.AllBlocked("project", 3, 2, "Design pending"),
                (cfg, ("beads", "state"), ("beads", "approvals")),
                "project: all 3 beads blocked on 2 approvals: Design pending",
            ),
            (
                d.AllAccountsExhausted("project", "author", NOW),
                (cfg, ("account-usage", "usage")),
                "project: all author accounts exhausted; reset 2026-10-16T12:03:04.123456Z",
            ),
            (
                d.DeliveryUnconfirmed("Example card"),
                (cfg, state, ("wsd-journal", "decisions")),
                "Delivery unconfirmed: Example card; operator attention needed.",
            ),
            (
                d.TrustSuspended(),
                (cfg, state),
                "Group trust suspended; reads require current authorization; actions remain held.",
            ),
            (
                d.CannotApprove("unsafe-content"),
                (),
                (
                    "Cannot approve as written: unsafe content.\n"
                    "Ask owner to correct the card; no decision recorded."
                ),
            ),
            (
                d.Reminder("Example card", d.Commit(REPO, PIN)),
                (cfg, state, ("wsd-journal", "decisions")),
                "Reminder: Example card\nhttps://github.com/example/project/commit/" + PIN,
            ),
        )
        for item, pairs, expected in cases:
            with self.subTest(notice=type(item)):
                result = render(self.notice(item, pairs), POLICY)
                self.assertEqual(result.parts, (expected,))
                self.assertTrue(result.complete)
                self.assertNotIn("nostr:", result.parts[0])
        item = d.AccountChanged()
        result = render(self.notice(item, (cfg, ("account-usage", "usage"))), POLICY)
        self.assertEqual(
            result.parts,
            (
                "deferred: account changed; restore login or account order, then reload "
                "configuration or use operator release",
            ),
        )
        view = d.Available(
            "fyi", d.EventOrigin(), NOW, (status(*cfg), status()), d.Fyi("News", "Completed example")
        )
        self.assertEqual(
            render(view).parts,
            (
                "FYI: News\nCompleted example\nFYI: no reply needed\n"
                "Observed 2026-10-16T12:03:04.123456Z; rendered "
                "2026-10-16T12:03:04.123456Z",
            ),
        )

    def test_digest_five_independent_counts(self):
        pairs = (
            ("beads", "closed-yesterday"),
            ("beads", "open-approval-labels"),
            ("wsd-journal", "needs-human"),
            ("wsd-journal", "idle-workstreams"),
            ("account-usage", "accounts-hit-limit"),
        )
        statuses = tuple(status(src, col, count=d.Count(1, i + 1)) for i, (src, col) in enumerate(pairs))
        rows = tuple(d.Rows(s.key, ("Item " + str(i),)) for i, s in enumerate(statuses))
        view = d.Available(
            "notice",
            d.EventOrigin(),
            NOW,
            (status("configuration", "configuration"), *statuses),
            d.Digest(*rows),
        )
        result = render(view)
        self.assertFalse(result.complete)
        text = "\n".join(result.parts)
        for i, (_, col) in enumerate(pairs):
            self.assertIn("Item " + str(i), text)
            self.assertIn(f"{col}: 1 of {i + 1} supplied", text)
        self.assertNotIn("Observed ", text)
        self.assertEqual(render(dataclasses.replace(view, sections=())), d.Unavailable("required-source"))

    def test_reminder_unsupported_and_wrong_union(self):
        pairs = (("configuration", "configuration"), ("wsd-journal", "state"), ("wsd-journal", "decisions"))
        unsupported = d.Commit(d.Repo("gitlab", "https://gitlab.com", "example", "project"), PIN)
        result = render(self.notice(d.Reminder("Card", unsupported), pairs), POLICY)
        self.assertEqual(result.parts, ("Reminder: Card\nReference unavailable here.",))
        self.assertFalse(result.complete)
        for wrong in (
            d.Inline("Title", "Full"),
            d.OperatorDoc("Title", "abcdef012345"),
            d.PullRequest(REPO, 1, PIN),
            d.Range(REPO, PIN, "2" * 40),
        ):
            self.assertIsInstance(render(self.notice(d.Reminder("Card", wrong), pairs), POLICY), d.Invalid)


class FrozenReadCompatibilityTests(unittest.TestCase):
    """Frozen 6B 0ed49add query DTOs only; not implemented ingress/auth proof."""

    def empty_view(self, query, scope=None):
        base = (status("configuration", "configuration", scope=scope), status(scope=scope))
        if query == "Workstreams":
            return d.Available(
                "workstreams",
                d.QueryOrigin(query),
                NOW,
                (base[0], dataclasses.replace(base[1], count=d.Count(0, 0))),
                d.Workstreams(d.Rows(base[1].key, ())),
            )
        if query == "Status" and scope is None:
            slots = (
                status(
                    "beads", "approvals", required=False, count=d.Count(0, 0), order="approvals-created-key"
                ),
                status(collection="needs-human", required=False, count=d.Count(0, 0)),
                status("account-usage", "usage", required=False, count=d.Count(0, 0)),
            )
            return d.Available(
                "control-status",
                d.QueryOrigin(query),
                NOW,
                (base[0], dataclasses.replace(base[1], count=d.Count(0, 0)), *slots),
                d.ControlStatus(d.Rows(base[1].key, ()), *(d.Rows(s.key, ()) for s in slots)),
            )
        if query == "Status":
            pairs = [("wsd-journal", x) for x in ("running", "ready", "parked", "held", "deferred")]
            pairs += [("beads", "approvals"), ("account-usage", "usage")]
            slots = tuple(
                status(
                    src,
                    col,
                    scope=scope,
                    required=False,
                    count=d.Count(0, 0),
                    order="approvals-created-key" if col == "approvals" else "none",
                )
                for src, col in pairs
            )
            return d.Available(
                "workstream-status",
                d.QueryOrigin(query, scope),
                NOW,
                (*base, *slots),
                d.WorkstreamStatus(scope, "idle", *(d.Rows(s.key, ()) for s in slots)),
            )
        if query == "BeadStatus":
            slots = tuple(
                status(collection=x, required=False, count=d.Count(0, 0))
                for x in ("recent-events", "decisions", "transcripts", "launches")
            )
            return d.Available(
                "bead-status",
                d.QueryOrigin(query, bead="btq-demo"),
                NOW,
                (status(), *slots),
                d.BeadStatus("btq-demo", "Title", "unknown", None, None, *(d.Rows(s.key, ()) for s in slots)),
            )
        if query == "Approvals":
            slot = status("beads", "approvals", count=d.Count(0, 0), order="approvals-created-key")
            return d.Available(
                "approvals", d.QueryOrigin(query), NOW, (slot,), d.Approvals(d.Rows(slot.key, ()))
            )
        slot = status("beads", "candidates", scope=scope, count=d.Count(0, 0), order="owner-queue-order")
        return d.Available(
            "queue",
            d.QueryOrigin(query, scope),
            NOW,
            (slot, status(collection="pause", scope=scope)),
            d.Queue(d.Rows(slot.key, ()), True),
        )

    def test_frozen_query_matrix_origin_header_source_and_partial_mapping(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/presentation/read-compatibility.json").read_text()
        )
        self.assertEqual(fixture["version"], 1)
        self.assertEqual(fixture["proposed_input"]["commit"], "0ed49add285a203a758dad8e97598ffa1b83f28d")
        cases = fixture["queries"]
        for query, scope, header in cases:
            with self.subTest(query=query, scope=scope):
                view = self.empty_view(query, scope)
                result = render(view)
                self.assertIsInstance(result, d.Rendered)
                self.assertTrue(all(part.startswith(header + "\n") for part in result.parts))
                self.assertTrue(all("Observed " in part for part in result.parts))
                self.assertIsInstance(render(dataclasses.replace(view, origin=d.EventOrigin())), d.Invalid)
                required = tuple(s for s in view.sections if s.required)
                self.assertEqual(
                    render(
                        dataclasses.replace(
                            view, sections=tuple(s for s in view.sections if s != required[0])
                        )
                    ),
                    d.Unavailable("required-source"),
                )
                cached = tuple(
                    dataclasses.replace(s, availability="cached-unreachable") for s in view.sections
                )
                result = render(dataclasses.replace(view, sections=cached))
                if isinstance(result, d.Rendered):
                    self.assertFalse(result.complete)
                    self.assertIn("unreachable", "\n".join(result.parts))
                else:
                    self.assertIsInstance(result, d.NeedsDetails)

    def test_bead_events_labels_and_native_record(self):
        view = self.empty_view("BeadStatus")
        payload = view.payload
        event = d.Event(1, NOW, "started", "Started example")
        decision = d.DecisionSummary(
            NOW, "approval", "approved", "Operator", "native-record", "Reviewed example"
        )
        launch = d.Launch(1, "author", "Account A", "unknown", NOW)
        rows = (
            d.Rows(payload.recent_events.key, (event,)),
            d.Rows(payload.decisions.key, (decision,)),
            d.Rows(payload.transcript_labels.key, ("Log A",)),
            d.Rows(payload.launches.key, (launch,)),
        )
        payload = dataclasses.replace(
            payload,
            branch="feature",
            session_label="Session A",
            recent_events=rows[0],
            decisions=rows[1],
            transcript_labels=rows[2],
            launches=rows[3],
        )
        sections = (
            view.sections[0],
            *(dataclasses.replace(s, count=d.Count(1, 1)) for s in view.sections[1:]),
        )
        view = dataclasses.replace(view, payload=payload, sections=sections)
        policy = dataclasses.replace(
            POLICY,
            session_labels=(d.DisplayLabel("session", "Session A"),),
            transcript_labels=(d.DisplayLabel("log", "Log A"),),
        )
        result = render(view, policy)
        text = "\n".join(result.parts)
        for expected in (
            "session_label: Session A",
            "Log A",
            "kind: native approval record",
            "via: native-record",
            "account_label: Account A",
            "trust: unknown",
            "Started example",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("Plan5", text)
        hidden = render(view, POLICY)
        self.assertFalse(hidden.complete)
        self.assertNotIn("Session A", "\n".join(hidden.parts))
        events = tuple(dataclasses.replace(event, sequence=i) for i in range(6))
        oversized = dataclasses.replace(
            view,
            payload=dataclasses.replace(payload, recent_events=d.Rows(rows[0].key, events)),
            sections=(sections[0], dataclasses.replace(sections[1], count=d.Count(6, 6)), *sections[2:]),
        )
        self.assertIsInstance(render(oversized, policy), d.Invalid)


class BoundAndWarningTests(unittest.TestCase):
    def test_input_limits_before_screening_and_no_payload_in_errors(self):
        # Canonical payload is larger than256KiB although every field/collection is individually bounded.
        card = approval(evidence=tuple(d.Inline("Title", "z" * 4096) for _ in range(70)))
        with patch(
            "heterodyne.wsd.presentation.rendering.safe", side_effect=AssertionError("must not screen")
        ):
            self.assertEqual(render_card_pair(protected(card), POLICY), d.Invalid("bounds"))
        self.assertIsInstance(render(task_view(title="🙂" * 4096)), d.Rendered)
        self.assertEqual(render(task_view(title="🙂" * 4097)), d.Invalid("bounds"))
        many = dataclasses.replace(task_view(), sections=(status(),) * 1025)
        self.assertIsInstance(render(many), d.Invalid)
        self.assertEqual(classify(ingress("x" * 262145)), d.Invalid("payload"))
        self.assertEqual(classify(ingress("\ud800")), d.Invalid("payload"))
        self.assertIsInstance(render(task_view(duration_us=float("nan"))), d.Invalid)
        self.assertIsInstance(render(task_view(agent="z" * 81)), d.Invalid)
        # Earliest valid UTC year is still exactly four digits in canonical output.
        early = dataclasses.replace(
            task_view(),
            rendered_at=NOW.replace(year=1),
            sections=(dataclasses.replace(status(), observed_at=NOW.replace(year=1)),),
        )
        self.assertIn("Observed 0001-10-16T12:03:04.123456Z", "\n".join(render(early).parts))

    def test_four_source_warnings_coalesce_and_repeat_each_page(self):
        view = FrozenReadCompatibilityTests().empty_view("Status")
        sections = tuple(
            dataclasses.replace(s, availability="cached-unreachable", observed_at=NOW - timedelta(days=1))
            for s in view.sections
        )
        result = render(dataclasses.replace(view, sections=sections))
        expected = (
            "⚠️ configuration: unreachable; beads: unreachable\n"
            "⚠️ wsd-journal: unreachable; account-usage: unreachable\n"
            "Warnings as of 2026-10-15T12:03:04.123456Z"
        )
        self.assertIsInstance(result, d.Rendered)
        for part in result.parts:
            self.assertIn(expected, part)
            self.assertLessEqual(len(part.splitlines()), 8)
        self.assertFalse(result.complete)

    def test_all_task_icons_and_reason_reset_truth(self):
        icons = {
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
        for state, icon in icons.items():
            reason = (
                d.Held("escalation", "Hold")
                if state == "held"
                else d.Unknown()
                if state == "deferred"
                else None
            )
            text = "\n".join(render(task_view(state=state, reason=reason)).parts)
            self.assertTrue(text.splitlines()[1].startswith(icon + " "))
        text = "\n".join(render(task_view(state="deferred", reason=d.AccountChanged())).parts)
        self.assertNotIn("resumes ", text)
        self.assertNotIn("reset ", text)

    def test_cross_field_link_and_secret_never_gain_generated_exemption(self):
        # Identifier starts in a literal then crosses into a typed hash: neither span covers it.
        card = approval(exact_effect=d.EffectText((d.LiteralText("f"), d.PublicHash("sha256", "f" * 64))))
        self.assertEqual(render_card_pair(protected(card), POLICY).reason, "unsafe-content")
        # A configured repository name is not a secret exemption either.
        repo = dataclasses.replace(REPO, name="ghp_" + "z" * 24)
        policy = dataclasses.replace(POLICY, forges=(repo,))
        card = approval(evidence=(d.Commit(repo, PIN),))
        self.assertEqual(render_card_pair(protected(card), policy).reason, "unsafe-content")
        text = "safe " * 80 + "did:private:identifier"
        result = render(task_view(title=text))
        self.assertIn("title: [withheld]", "\n".join(result.parts))
        self.assertNotIn("safe safe", "\n".join(result.parts))


class DetailedRowsTests(unittest.TestCase):
    def test_excessive_layout_never_joins_an_unbounded_page(self):
        from heterodyne.wsd.presentation.safety import join

        def bounded_join(parts, separator="\n"):
            self.assertLessEqual(len(parts), 8)
            return join(parts, separator)

        with patch("heterodyne.wsd.presentation.layout.join", side_effect=bounded_join):
            self.assertEqual(
                paginate((generated("Header"),), (generated("Full row"),) * 2000, (), 2000),
                d.NeedsDetails("excessive-parts"),
            )

    def test_role_headroom_and_unknown_usage_preserve_all_supplied_facts(self):
        row = d.WorkstreamRow(
            "project",
            "running",
            "btq-demo",
            1,
            2,
            3,
            4,
            5,
            (d.RoleHeadroom("author", "Account A", 67, "trusted", NOW),),
            None,
        )
        section = status(count=d.Count(1, 1))
        view = d.Available(
            "workstreams",
            d.QueryOrigin("Workstreams"),
            NOW,
            (status("configuration", "configuration"), section),
            d.Workstreams(d.Rows(section.key, (row,))),
        )
        result = render(view)
        self.assertIsInstance(result, d.Rendered)
        text = "\n".join(result.parts)
        for expected in (
            "current_bead: btq-demo",
            "ready: 1",
            "parked: 2",
            "held: 3",
            "deferred: 4",
            "open_approval: 5",
            "role_headroom:",
            "account: Account A",
            "remaining_percent: 67%",
            "trust: trusted",
            "observed_at: 2026-10-16T12:03:04.123456Z",
        ):
            self.assertIn(expected, text)
        self.assertTrue(result.complete)
        view = FrozenReadCompatibilityTests().empty_view("Status")
        usage = d.AccountUsage("Account A", "author", "Launch A", "daily", None, None, None, NOW, "unknown")
        usage_status = dataclasses.replace(view.sections[-1], count=d.Count(1, 1))
        view = dataclasses.replace(
            view,
            sections=(*view.sections[:-1], usage_status),
            payload=dataclasses.replace(view.payload, usage=d.Rows(usage_status.key, (usage,))),
        )
        result = render(view)
        self.assertIsInstance(result, d.Rendered)
        text = "\n".join(result.parts)
        self.assertIn("used_percent: unknown", text)
        self.assertIn("remaining_percent: unknown", text)
        self.assertIn("launch_label: Launch A", text)
        self.assertFalse(result.complete)

    def test_payload_and_collection_limits_precede_semantic_regexes(self):
        card = approval(evidence=tuple(d.Inline("Title", "z" * 4096) for _ in range(70)))
        with patch(
            "heterodyne.wsd.presentation.validation._semantic", side_effect=AssertionError("too early")
        ):
            self.assertEqual(render_card_pair(protected(card), POLICY), d.Invalid("bounds"))
            self.assertIsInstance(
                render(dataclasses.replace(task_view(), sections=(status(),) * 1025)), d.Invalid
            )
            self.assertIsInstance(
                render(
                    CollectionTests().approvals(
                        tuple(
                            d.ApprovalSummary(f"btq-a{i}", "Title", "open", NOW, f"{i:04x}")
                            for i in range(101)
                        )
                    )
                ),
                d.Invalid,
            )

    def test_protected_exact_physical_line_limit_and_email_boundaries(self):
        policy = dataclasses.replace(POLICY, max_chars=3200)
        card = approval(context_summary="\n".join("Complete context" for _ in range(25)))
        pair = render_card_pair(protected(card), policy)
        self.assertIsInstance(pair, d.Pair)
        self.assertEqual(len(pair.workstream_single_part.splitlines()), 36)
        self.assertEqual(len(pair.control_single_part.splitlines()), 36)
        oversized = dataclasses.replace(card, context_summary=card.context_summary + "\nOne more line")
        self.assertEqual(render_card_pair(protected(oversized), policy).reason, "content-too-large")
        for email in ("x@example.com", "+x.y@example.com", "prefix+x@example.com", "word(x@example.com)"):
            with self.subTest(email=email):
                self.assertNotIn(email, "\n".join(render(task_view(title=email)).parts))
