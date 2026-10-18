"""Bounded private construction only: no scanner test collection or gate launch."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scanner_recovery import environment as recovery
from scanner_recovery import runner


class PrivateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.layout = recovery.create_layout()
        self.root = Path(self.layout["root"]["path"])
        self.addCleanup(shutil.rmtree, self.root)
        self.outer = Path(tempfile.mkdtemp(prefix="scanner-helper-test-"))
        self.addCleanup(shutil.rmtree, self.outer)
        self.contract = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "docs/reviews/scanner-boundary/environment-contract.json"
            ).read_text()
        )

    def values(self):
        return recovery.environment(
            self.layout,
            Path("/private/bin/python"),
            self.outer,
            self.outer / "plugins",
            self.outer / "collection.json",
            self.contract,
        )

    def test_reservation_refuses_changed_expired_or_naive_records(self):
        import datetime as dt

        path = self.outer / "reservation.json"
        now = dt.datetime.now(dt.UTC)
        record = {
            "start_utc": (now - dt.timedelta(seconds=1)).isoformat(),
            "expires_utc": (now + dt.timedelta(seconds=30)).isoformat(),
        }
        path.write_text(json.dumps(record))
        runner.slot_check(path, record)
        with self.assertRaisesRegex(ValueError, "too short"):
            runner.slot_check(path, record, 60)
        path.write_text(json.dumps(record | {"changed": True}))
        with self.assertRaisesRegex(ValueError, "Reservation changed"):
            runner.slot_check(path, record)
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            runner.instant("2026-10-18T00:00:00")

    def test_collection_guards_order_uniqueness_and_added_node_adjacency(self):
        nodes = ["a", "added", "b"]
        manifest = {
            "collection_delta": {
                "added_node": "added",
                "immediate_predecessor": "a",
                "immediate_successor": "b",
                "ordered_minus_added_legacy_sha256": runner.sha(json.dumps(["a", "b"]).encode()),
            },
            "metadata19": {"nodes": ["a", "b"]},
        }
        runner.collection_check(nodes, 3, manifest, True)
        for invalid in (["a", "b", "added"], ["a", "added", "a"], ["b", "added", "a"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                runner.collection_check(invalid, 3, manifest, True)

    def test_private_layout_and_exact_boundary_bind(self):
        self.assertLessEqual(len(os.fsencode(self.root)), 16)
        for key in recovery.CHILDREN:
            self.assertLessEqual(len(os.fsencode(self.layout[key]["path"])), 18)
        self.assertEqual(
            recovery.socket_construction_probe(self.layout),
            {"bound_encoded_bytes": 100, "over_budget_rejected_before_bind": True},
        )

    def test_environment_does_not_inherit_parent_or_live_bus(self):
        with patch.dict(
            os.environ,
            {"DBUS_SESSION_BUS_ADDRESS": "forbidden", "TMPDIR": "/forbidden", "PYTEST_ADDOPTS": "-n8"},
        ):
            values = self.values()
        self.assertEqual(len(values), 14)
        self.assertEqual(values["TMPDIR"], self.layout["t"]["path"])
        self.assertEqual(values["XDG_RUNTIME_DIR"], self.layout["r"]["path"])
        self.assertEqual(
            values["PYTHONPATH"].split(os.pathsep), [str(self.outer / "src"), str(self.outer / "plugins")]
        )
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", values)

    def test_four_separate_construction_rejections(self):
        values = self.values()
        cases = []
        thirteen = values.copy()
        del thirteen["XDG_RUNTIME_DIR"]
        cases.append(lambda: recovery.validate_environment(thirteen, self.contract))
        fifteen = values | {"DBUS_SESSION_BUS_ADDRESS": "forbidden"}
        cases.append(lambda: recovery.validate_environment(fifteen, self.contract))
        missing = dict.fromkeys(self.contract["required_fields"], "fixture")
        del missing["scratch_archive_inventory_sha256"]
        cases.append(lambda: recovery.validate_result(missing, self.contract))
        cases.append(lambda: recovery.socket_budget(Path("/" + "x" * 100)))
        for number, case in enumerate(cases):
            with self.subTest(probe=number), self.assertRaises(ValueError):
                case()

    def test_construction_probe_report_is_separate_and_source_bound(self):
        result = recovery.construction_probes(self.contract)
        self.assertEqual(result["count"], 4)
        self.assertEqual(len(set(result["rejected"])), 4)
        self.assertEqual(
            result["support_source_sha256"], recovery.digest(Path(recovery.__file__).read_bytes())
        )

    def test_runtime_must_be_empty_and_identity_stable(self):
        runtime = Path(self.layout["r"]["path"])
        (runtime / "bus").touch()
        with self.assertRaisesRegex(ValueError, "initially be empty"):
            self.values()
        (runtime / "bus").unlink()
        runtime.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "owner/mode"):
            self.values()

    def test_archive_never_follows_links_and_retains_special_metadata(self):
        (self.root / "nested").mkdir()
        (self.root / "nested" / "data").write_bytes(b"retained")
        outside = self.outer / "outside"
        outside.write_bytes(b"must-not-read")
        (self.root / "link").symlink_to(outside)
        (self.root / "dirlink").symlink_to(self.outer, target_is_directory=True)
        os.mkfifo(self.root / "fifo", 0o600)
        target = self.outer / "archive"
        result = recovery.archive_scratch(self.layout, target)
        inventory = json.loads((target / "inventory.json").read_text())
        rows = {row["path"]: row for row in inventory["entries"]}
        self.assertEqual(result["bytes"], 8)
        self.assertEqual((target / "nested/data").read_bytes(), b"retained")
        self.assertEqual(rows["link"]["kind"], "symlink")
        self.assertEqual(rows["dirlink"]["kind"], "symlink")
        self.assertEqual(rows["fifo"]["kind"], "excluded-special")
        self.assertFalse((target / "link").exists())
        self.assertEqual(result["sha256"], recovery.digest((target / "inventory.json").read_bytes()))

    def test_archive_capacity_failure_is_explicit_and_preserves_scratch(self):
        (self.root / "data").write_bytes(b"too big")
        with self.assertRaisesRegex(ValueError, "byte limit"):
            recovery.archive_scratch(self.layout, self.outer / "archive", maximum_bytes=2)
        self.assertEqual((self.root / "data").read_bytes(), b"too big")

    def test_missing_source_inventory_and_default_tmux_hold(self):
        (self.outer / "src").mkdir()
        (self.outer / "tests").mkdir()
        (self.outer / "tests/example.py").write_text('command = ["tmux", "new-session"]\n')
        candidates = recovery.source_candidates(self.outer)
        inventory = {
            "source_root": str(self.outer),
            "source_candidates": candidates,
            "reviewed_by_controller": True,
            "derivations": [
                {
                    "kind": "tmux",
                    "references": list(candidates),
                    "reason": "fixture",
                    "isolation": "default-server",
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "Unbound default"):
            recovery.validate_inventory(self.outer, self.layout, inventory)
        inventory["derivations"][0].update(isolation="audited-guard", guard_references=list(candidates))
        result = recovery.validate_inventory(self.outer, self.layout, inventory)
        self.assertEqual(result["candidates"], 1)
        (self.outer / "src/new.py").write_text('path = "new.sock"\n')
        with self.assertRaisesRegex(ValueError, "coverage changed"):
            recovery.validate_inventory(self.outer, self.layout, inventory)

    def test_socket_derivation_holds_before_binding_when_over_budget(self):
        (self.outer / "src").mkdir()
        (self.outer / "tests").mkdir()
        (self.outer / "src/socket_case.py").write_text('path = "p.sock"\n')
        candidates = recovery.source_candidates(self.outer)
        inventory = {
            "source_root": str(self.outer),
            "source_candidates": candidates,
            "reviewed_by_controller": True,
            "derivations": [
                {
                    "kind": "socket",
                    "references": list(candidates),
                    "reason": "fixture",
                    "base": "p",
                    "case_component_bytes": 32,
                    "suffix_bytes": 80,
                    "maximum_encoded_bytes": 130,
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "exceeds budget"):
            recovery.validate_inventory(self.outer, self.layout, inventory)


if __name__ == "__main__":
    unittest.main()
