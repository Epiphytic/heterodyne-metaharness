"""Fourth-review regression: failed synthetic attempts must retain durable diagnostics."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scanner_recovery import environment as recovery
from scanner_recovery import runner


class FailedAttemptResultTests(unittest.TestCase):
    def setUp(self):
        self.layout = recovery.create_layout()
        self.root = Path(self.layout["root"]["path"])
        self.folder = Path(tempfile.mkdtemp(prefix="scanner-four-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.addCleanup(shutil.rmtree, self.folder)
        contract = json.loads(runner.SUPPLEMENT.read_text())
        self.result: dict[str, object] = {key: None for key in contract["required_fields"]}
        self.result["verification_result_or_error"] = {"HOLD": "Not completed"}
        (self.root / "t" / "diagnostic.txt").write_text("synthetic failed-attempt evidence")

    def preserve_result(self):
        runner.finish_scratch(self.layout, self.folder, self.result)
        runner.put(self.folder / "result.json", self.result)
        durable = json.loads((self.folder / "result.json").read_text())
        self.assertIn("HOLD", durable["verification_result_or_error"])
        self.assertIsNone(durable["socket_path_budget_inventory"])
        self.assertEqual(
            (self.folder / "scratch-archive" / "t" / "diagnostic.txt").read_text(),
            "synthetic failed-attempt evidence",
        )
        self.assertIsInstance(durable["scratch_archive_inventory_sha256"], str)
        self.assertNotIn("archive_error", durable)
        return durable

    def test_preflight_failure_with_prefilled_none_retains_result(self):
        with patch.object(recovery.os, "statvfs", return_value=SimpleNamespace(f_bavail=0, f_frsize=4096)):
            with self.assertRaisesRegex(ValueError, "2GiB") as failed:
                recovery.filesystem_preflight(self.layout)
        runner.hold(self.result, "Preflight failed", failed.exception)
        durable = self.preserve_result()
        self.assertEqual(durable["verification_result_or_error"]["HOLD"], "Preflight failed")
        self.assertFalse((self.root / "exec-check").exists())

    def test_partial_layout_with_prefilled_none_retains_result(self):
        del self.layout["r"]
        self.result["verification_result_or_error"] = {"HOLD": "LayoutCreationError"}
        durable = self.preserve_result()
        self.assertIn("runtime_error", durable)
        self.assertEqual(durable["verification_result_or_error"]["HOLD"], "Runtime check failed")
        self.assertEqual(durable["verification_result_or_error"]["previous"], {"HOLD": "LayoutCreationError"})
