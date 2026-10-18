"""Review regressions use private fixtures only, never a scanner gate."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scanner_recovery import environment as recovery
from scanner_recovery import runner


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.outer = Path(tempfile.mkdtemp(prefix="recovery-review-"))
        self.addCleanup(shutil.rmtree, self.outer)
        self.layout = recovery.create_layout()
        self.root = Path(self.layout["root"]["path"])
        self.addCleanup(shutil.rmtree, self.root)

    def test_archive_attempted_after_runtime_failure_keeps_original_hold(self):
        (Path(self.layout["r"]["path"]) / "bus").touch()
        result = {"verification_result_or_error": {"HOLD": "original failure"}}
        runner.finish_scratch(self.layout, self.outer, result)
        self.assertIsNotNone(result["scratch_archive_inventory_sha256"])
        self.assertIn("original failure", json.dumps(result["verification_result_or_error"]))
        self.assertIn("Runtime", result["runtime_error"])

    def test_archive_never_opens_symlink_target(self):
        (self.root / "outside-link").symlink_to(self.outer, target_is_directory=True)
        real_open = os.open
        calls = []

        def observed(path, flags, *args, **kwargs):
            calls.append((str(path), flags))
            return real_open(path, flags, *args, **kwargs)

        with patch.object(recovery.os, "open", side_effect=observed):
            recovery.archive_scratch(self.layout, self.outer / "archive")
        self.assertFalse(any(name == "outside-link" for name, _ in calls))
        self.assertTrue(all(flags & os.O_NOFOLLOW for name, flags in calls if flags & os.O_DIRECTORY))

    def test_archive_directory_error_is_not_silent(self):
        nested = self.root / "unreadable"
        nested.mkdir()
        real_open = os.open

        def denied(path, flags, *args, **kwargs):
            if str(path) == "unreadable":
                raise PermissionError("fixture unreadable directory")
            return real_open(path, flags, *args, **kwargs)

        with patch.object(recovery.os, "open", side_effect=denied), self.assertRaises(PermissionError):
            recovery.archive_scratch(self.layout, self.outer / "archive")
        self.assertFalse((self.outer / "archive/inventory.json").exists())

    def test_all_later_stages_bind_diagnostic_and_common_chain(self):
        for name, (_, _, _, required) in runner.STAGES.items():
            if name != "diagnostic":
                self.assertIn("diagnostic", required)

    def test_h92_rejects_unrelated_and_missing_added_nodes(self):
        delta = {
            "added_node": "tests/test_sandbox_hostread.py::added",
            "immediate_predecessor": "tests/test_sandbox_hostread.py::before",
            "immediate_successor": "tests/test_sandbox_hostread.py::after",
        }
        nodes = [delta["immediate_predecessor"], delta["added_node"], delta["immediate_successor"]]
        runner.h92_collection_check(nodes, {"collection_delta": delta})
        for bad in ([nodes[0], "tests/other.py::added", nodes[2]], [nodes[1], nodes[0], nodes[2]]):
            with self.assertRaises(ValueError):
                runner.h92_collection_check(bad, {"collection_delta": delta})

    def test_same_count_supplement_substitution_is_rejected(self):
        contract = json.loads(runner.SUPPLEMENT.read_bytes())
        manifest = json.loads(runner.MANIFEST.read_bytes())
        runner.validate_supplement(contract, manifest)
        for field, replacement in (
            ("environment_allowlist", "DBUS_SESSION_BUS_ADDRESS"),
            ("required_fields", "invented"),
        ):
            changed = dict(contract)
            changed[field] = [replacement, *contract[field][1:]]
            with self.assertRaises(ValueError):
                runner.validate_supplement(changed, manifest)

    def test_candidates_include_indirect_calls_shell_and_config(self):
        (self.outer / "src").mkdir()
        (self.outer / "docs").mkdir()
        (self.outer / "src/control.py").write_text(
            "sock.bind(str(self.path))\nawait open_unix_connection(path)\n"
        )
        (self.outer / "docs/inner.sh").write_text("TMUX_TMPDIR=/tmp tmux -L s9-10\n")
        (self.outer / "defaults.toml").write_text('endpoint="helper.sock"\n')
        candidates = recovery.source_candidates(self.outer)
        self.assertEqual(
            set(candidates), {"src/control.py:1", "src/control.py:2", "docs/inner.sh:1", "defaults.toml:1"}
        )

    def test_socket_derivation_counts_join_separator(self):
        (self.outer / "src").mkdir()
        (self.outer / "src/socket.py").write_text('p="a.sock"\n')
        refs = recovery.source_candidates(self.outer)
        row = {
            "kind": "socket",
            "reason": "fixture",
            "references": list(refs),
            "base": "p",
            "case_component_bytes": 32,
            "suffix_bytes": 38,
            "maximum_encoded_bytes": 18 + 32 + 38,
        }
        inv = {
            "source_root": str(self.outer),
            "source_candidates": refs,
            "derivations": [row],
            "reviewed_by_controller": True,
        }
        with self.assertRaises(ValueError):
            recovery.validate_inventory(self.outer, self.layout, inv)
        row["maximum_encoded_bytes"] += 1
        recovery.validate_inventory(self.outer, self.layout, inv)

    def plan_fixture(self, stage="diagnostic"):
        import datetime as dt

        work = self.outer / "work"
        work.mkdir()
        (work / ".git").touch()
        (work / "src").mkdir()
        (work / "src/example.py").write_text('path="fixture.sock"\n')
        original = self.outer / "original"
        original.mkdir()
        fields = {path: "source-hash" for path in runner.PATHS}
        head = "e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8" if stage == "h92" else "fixture-head"
        snapshot = {"head": head, "files": fields, "porcelain": "", "tree": "tree", "diff_sha256": "diff"}
        receipt = {
            "task": "btq-1wu2i",
            "stage": stage,
            "phase": runner.STAGES[stage][2],
            "source_snapshot": snapshot,
            "source_planning_commits": ["plan"],
            "authority_evidence_sha256": "authority",
            "environment_provenance_sha256": "env",
            "h_delivery_evidence_sha256": "h",
            "b1_three_file_sha256": dict.fromkeys(runner.B1, "source-hash"),
            "b2_target_file_sha256": dict.fromkeys(runner.B2, "source-hash"),
            "b2_before_file_sha256": dict.fromkeys(runner.B2, "source-hash"),
            "predecessors": {},
        }
        contract = json.loads(runner.SUPPLEMENT.read_bytes())
        manifest = json.loads(runner.MANIFEST.read_bytes())
        nodes = ["tests/fixture.py::test_" + str(i) for i in range(375)]

        def write(name, value):
            path = self.outer / name
            path.write_text(json.dumps(value))
            return path

        collection = write("failed-collection.json", nodes)
        failed = write(
            "failed-result.json", {"raw_collection_file_sha256": runner.sha(collection.read_bytes())}
        )
        contract["failed_result_sha256"] = runner.sha(failed.read_bytes())
        supplement = write("supplement.json", contract)
        failure_map = write(
            "map.json",
            {
                "source_result_sha256": contract["failed_result_sha256"],
                "failures": [{"node": n, "classification": "environment"} for n in nodes],
            },
        )
        receipt_path = write("receipt.json", receipt)
        refs = recovery.source_candidates(work)
        inventory = write(
            "inventory.json",
            {
                "source_root": str(work),
                "source_candidates": refs,
                "reviewed_by_controller": True,
                "derivations": [{"kind": "non-endpoint", "references": list(refs), "reason": "fixture"}],
            },
        )
        disposition = write(
            "disposition.json",
            {
                "mode": "FRESH",
                "approval": "btq-7k1bf",
                "approved_by": "Liam",
                "via_ref": "marmot:id:2a9d3b2de451",
                "failed_attempt": contract["failed_attempt"],
            },
        )
        now = dt.datetime.now(dt.UTC)
        reservation = write(
            "reservation.json",
            {
                "task": "btq-1wu2i",
                "source_head": head,
                "stage": stage,
                "exclusive": True,
                "status": "reserved",
                "reservation_id": "fixture",
                "start_utc": (now - dt.timedelta(seconds=1)).isoformat(),
                "expires_utc": (now + dt.timedelta(hours=4)).isoformat(),
            },
        )
        run_dir = self.outer / "unused-attempt"
        argv = [
            "runner",
            "--stage",
            stage,
            "--worktree",
            str(work),
            "--private-originals",
            str(original),
            "--python",
            sys.executable,
            "--head",
            head,
            "--run-dir",
            str(run_dir),
            "--reservation",
            str(reservation),
            "--source-receipt",
            str(receipt_path),
            "--source-receipt-sha256",
            runner.sha(receipt_path.read_bytes()),
            "--inventory",
            str(inventory),
            "--baseline-disposition",
            str(disposition),
            "--failed-result",
            str(failed),
            "--failed-collection",
            str(collection),
        ]
        return argv, snapshot, manifest, supplement, failure_map, run_dir, inventory

    def invoke_plan(self, fixture):
        import contextlib
        import io

        argv, snapshot, manifest, supplement, failure_map, _, _ = fixture
        with (
            patch.object(sys, "argv", argv),
            patch.object(runner, "SUPPLEMENT", supplement),
            patch.object(runner, "FAILURE_MAP", failure_map),
            patch.object(runner, "pinned_inputs", return_value=(manifest, {}, {})),
            patch.object(runner, "snapshot", return_value=snapshot),
            patch.object(
                runner, "git", return_value=snapshot["head"] if argv[2] == "h92" else "absent-merge"
            ),
            patch.object(runner, "run", side_effect=AssertionError("No child allowed")),
            patch.object(recovery, "create_layout", side_effect=AssertionError("No scratch allowed")),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            value = runner.main()
        return value, json.loads(out.getvalue())

    def test_main_plan_validates_diagnostic_without_burning_attempt(self):
        fixture = self.plan_fixture()
        value, plan = self.invoke_plan(fixture)
        self.assertEqual(value, 0)
        self.assertEqual(plan["count"], 375)
        self.assertFalse(fixture[5].exists())

    def test_main_refuses_bad_inventory_before_release_or_child(self):
        fixture = self.plan_fixture()
        fixture[6].write_text("{}")
        fixture[0].append("--execute")
        with self.assertRaisesRegex(ValueError, "coverage changed"):
            self.invoke_plan(fixture)
        self.assertFalse(fixture[5].exists())

    def test_main_refuses_missing_diagnostic_input_before_release(self):
        fixture = self.plan_fixture()
        argv = fixture[0]
        del argv[argv.index("--failed-result") : argv.index("--failed-result") + 2]
        with self.assertRaisesRegex(ValueError, "before release"):
            self.invoke_plan(fixture)
        self.assertFalse(fixture[5].exists())

    def test_main_refuses_changed_diagnostic_identity_before_release(self):
        fixture = self.plan_fixture()
        Path(fixture[0][fixture[0].index("--failed-result") + 1]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "result pin changed"):
            self.invoke_plan(fixture)
        self.assertFalse(fixture[5].exists())

    def test_main_h92_refuses_dirty_source_before_release(self):
        fixture = self.plan_fixture("h92")
        fixture[1]["porcelain"] = " M tests/test_sandbox_hostread.py"
        with self.assertRaisesRegex(ValueError, "clean porcelain"):
            self.invoke_plan(fixture)
        self.assertFalse(fixture[5].exists())

    def test_predecessor_rejects_different_diagnostic_chain_and_source(self):
        contract = json.loads(runner.SUPPLEMENT.read_bytes())
        receipt = {
            "task": "btq-1wu2i",
            "h_delivery_evidence_sha256": "h",
            "b1_three_file_sha256": {"p": "v"},
            "b2_before_file_sha256": {"t": "v"},
            "predecessors": {},
        }
        chain = {"driver": "driver", "support": "support", "supplement": "supp", "disposition": "disp"}
        old: dict[str, Any] = dict.fromkeys(contract["required_fields"], "fixture")
        old.update(
            stage="diagnostic",
            unavailable_fields=[],
            source_receipt_identity=receipt,
            b1_three_file_sha256={"p": "v"},
            b2_target_file_sha256={"t": "v"},
            chain_identity=chain,
            verification_result_or_error={"PASS": {"total": 375, "failed": 0, "passed": 375}},
        )
        runner.predecessor_check("diagnostic", old, receipt, contract, chain)
        for field, bad in (
            ("chain_identity", {"driver": "other"}),
            ("b1_three_file_sha256", {"p": "other"}),
            ("b2_target_file_sha256", {"t": "other"}),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.predecessor_check("diagnostic", old | {field: bad}, receipt, contract, chain)


if __name__ == "__main__":
    unittest.main()
