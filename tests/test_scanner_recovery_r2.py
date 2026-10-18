"""Second-review regressions: private synthetic fixtures, no scanner gates."""

import errno
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scanner_recovery import environment as recovery
from scanner_recovery import runner


class TraversalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.layout = recovery.create_layout()
        self.root = Path(self.layout["root"]["path"])
        self.outer = Path(tempfile.mkdtemp(prefix="scanner-review-two-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.addCleanup(shutil.rmtree, self.outer)

    def test_nested_runtime_socket_holds_and_still_archives(self):
        nested = Path(self.layout["r"]["path"]) / "nested"
        nested.mkdir()
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(nested / "ctl.sock"))
            result = {"verification_result_or_error": {"PASS": "fixture"}}
            runner.finish_scratch(self.layout, self.outer, result)
        self.assertIn("HOLD", result["verification_result_or_error"])
        self.assertIn("nested/ctl.sock", result["runtime_entries_after"])
        self.assertIsNotNone(result["scratch_archive_inventory_sha256"])

    def test_runtime_does_not_stat_external_link_target(self):
        runtime = Path(self.layout["r"]["path"])
        (runtime / "link").symlink_to(self.outer)
        result = {"verification_result_or_error": {"PASS": "fixture"}}
        with patch.object(Path, "is_socket", side_effect=AssertionError("followed target")):
            runner.finish_scratch(self.layout, self.outer, result)
        self.assertEqual(result["runtime_entries_after"], ["link"])
        rows = json.loads((self.outer / "scratch-archive/inventory.json").read_bytes())["entries"]
        self.assertEqual(next(row for row in rows if row["path"] == "r/link")["kind"], "symlink")

    def test_wide_scratch_archive_uses_depth_bounded_descriptors(self):
        for number in range(100):
            (self.root / "p" / str(number)).mkdir()
        opened = set()
        peak = 0
        real_open, real_close = os.open, os.close

        def tracked_open(*args, **kwargs):
            nonlocal peak
            if len(opened) >= 24:
                raise OSError(errno.EMFILE, "fixture descriptor limit")
            descriptor = real_open(*args, **kwargs)
            opened.add(descriptor)
            peak = max(peak, len(opened))
            return descriptor

        def tracked_close(descriptor):
            opened.discard(descriptor)
            real_close(descriptor)

        with (
            patch.object(os, "open", side_effect=tracked_open),
            patch.object(os, "close", side_effect=tracked_close),
        ):
            result = recovery.archive_scratch(self.layout, self.outer / "archive")
        self.assertEqual(result["entries"], 105)
        self.assertLessEqual(peak, 5)
        self.assertFalse(opened)


class InventoryRegressionTests(unittest.TestCase):
    layout: dict[str, Any]
    root: Path
    outer: Path
    setUp = TraversalRegressionTests.setUp

    def inventory(self, text='path = "fixture.sock"\n', kind="socket"):
        (self.outer / "case.py").write_text(text)
        refs = recovery.source_candidates(self.outer)
        row = {"kind": kind, "references": list(refs), "reason": "private fixture"}
        if kind == "socket":
            row.update(base="h", case_component_bytes=0, suffix_bytes=7, maximum_encoded_bytes=25)
        return {
            "source_root": str(self.outer),
            "source_candidates": refs,
            "reviewed_by_controller": True,
            "derivations": [row],
        }

    def test_extensionless_and_systemd_candidates(self):
        (self.outer / "entry").write_text("#!/bin/sh\ntmux new-session\n")
        (self.outer / "worker.service").write_text("ExecStart=tmux new-session\n")
        (self.outer / "worker.socket").write_text("ListenStream=/tmp/private.sock\n")
        refs = recovery.source_candidates(self.outer)
        self.assertIn("entry:2", refs)
        self.assertIn("worker.service:1", refs)
        self.assertIn("worker.socket:1", refs)

    def test_nonendpoint_cannot_cover_socket_or_tmux(self):
        for text in ('path = "fixture.sock"\n', 'command = ["tmux", "new-session"]\n'):
            inv = self.inventory(text, "non-endpoint")
            with self.assertRaisesRegex(ValueError, "endpoint.*classification"):
                recovery.validate_inventory(self.outer, self.layout, inv)

    def test_home_base_and_raw_inventory_hash(self):
        inv = self.inventory()
        raw = json.dumps(inv, indent=3).encode() + b"\n"
        result = recovery.validate_inventory(self.outer, self.layout, inv, raw=raw)
        self.assertEqual(result["inventory_sha256"], recovery.digest(raw))

    def test_required_worst_case_measurement_rejects_understatement(self):
        inv = self.inventory()
        with self.assertRaisesRegex(ValueError, "reconstructed"):
            recovery.validate_inventory(self.outer, self.layout, inv, measured=True)
        inv["derivations"][0].update(worst_case_component="", worst_case_suffix="/x.sock")
        result = recovery.validate_inventory(self.outer, self.layout, inv, measured=True)
        self.assertEqual(
            result["measured_paths"][0]["encoded_bytes"], len(os.fsencode(self.root / "h/x.sock"))
        )
        inv["derivations"][0]["worst_case_suffix"] = "/too-long.sock"
        with self.assertRaises(ValueError):
            recovery.validate_inventory(self.outer, self.layout, inv, measured=True)

    def test_layout_failure_retains_created_root(self):
        real_mkdir = Path.mkdir
        roots = []
        real_mkdtemp = tempfile.mkdtemp

        def made(*args, **kwargs):
            result = real_mkdtemp(*args, **kwargs)
            roots.append(Path(result))
            return result

        def failed(path, *args, **kwargs):
            if path.name == "r":
                raise OSError("fixture full disk")
            return real_mkdir(path, *args, **kwargs)

        try:
            with patch.object(tempfile, "mkdtemp", side_effect=made), patch.object(Path, "mkdir", failed):
                with self.assertRaises(recovery.LayoutCreationError) as error:
                    recovery.create_layout()
            self.assertEqual(error.exception.layout["root"]["path"], str(roots[0]))
            self.assertTrue(roots[0].exists())
        finally:
            for root in roots:
                shutil.rmtree(root)


class ChildCleanupRegressionTests(unittest.TestCase):
    def test_normal_exit_kills_descendant_before_return(self):
        import time

        with tempfile.TemporaryDirectory(prefix="scanner-child-fixture-") as directory:
            folder = Path(directory)
            marker = folder / "late"
            code = (
                "import os,time,pathlib\n"
                "if os.fork()==0:\n time.sleep(0.4)\n pathlib.Path("
                + repr(str(marker))
                + ').write_text("late")\n os._exit(0)\n'
            )
            result = runner.run(
                [sys.executable, "-c", code], {"PATH": "/usr/bin:/bin"}, folder, folder, "child.log", 5, []
            )
            time.sleep(0.5)
            self.assertFalse(marker.exists(), "descendant wrote after archive boundary")
            self.assertTrue(result["process_group_quiescent"])


class PreservationRegressionTests(unittest.TestCase):
    layout: dict[str, Any]
    root: Path
    outer: Path
    setUp = TraversalRegressionTests.setUp

    def test_longer_surviving_socket_holds_even_below_absolute_budget(self):
        path = self.root / "p" / ("s" * 75)
        length = len(os.fsencode(path))
        self.assertLess(length, 100)
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(path))
            result = {
                "verification_result_or_error": {"PASS": "fixture"},
                "socket_path_budget_inventory": {
                    "receipt": {
                        "derivations": [{"kind": "socket", "base": "p", "maximum_encoded_bytes": length - 1}]
                    }
                },
            }
            runner.finish_scratch(self.layout, self.outer, result)
        self.assertIn("HOLD", result["verification_result_or_error"])
        self.assertIsNotNone(result["scratch_archive_inventory_sha256"])
        observed = result["scratch_archive"]["surviving_socket_paths"][0]
        self.assertEqual(observed["socket_encoded_bytes"], length)
        self.assertEqual(observed["margin_to_100"], 100 - length)

    def test_partial_layout_is_archived_without_runtime_child(self):
        result = {"verification_result_or_error": {"HOLD": "construction failed"}}
        partial = {"root": self.layout["root"], "h": self.layout["h"]}
        runner.finish_scratch(partial, self.outer, result)
        self.assertIn("HOLD", result["verification_result_or_error"])
        self.assertIsNotNone(result["scratch_archive_inventory_sha256"])
