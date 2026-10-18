"""Third-review regressions: synthetic helper inputs only, never scanner stages."""

import ast
import errno
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


class InventoryReviewThreeTests(unittest.TestCase):
    def setUp(self):
        self.layout = recovery.create_layout()
        self.root = Path(self.layout["root"]["path"])
        self.folder = Path(tempfile.mkdtemp(prefix="scanner-three-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.addCleanup(shutil.rmtree, self.folder)

    def inventory(self, text, kind):
        (self.folder / "case.py").write_text(text)
        refs = recovery.source_candidates(self.folder)
        row = {"kind": kind, "references": list(refs), "reason": "synthetic source"}
        if kind == "tmux":
            row.update(isolation="audited-guard", guard_references=list(refs))
        return {
            "source_root": str(self.folder),
            "source_candidates": refs,
            "reviewed_by_controller": True,
            "derivations": [row],
        }

    def test_mixed_tmux_socket_cannot_bypass_bound(self):
        inv = self.inventory('cmd = "tmux"\npath = "fixture.sock"\n', "tmux")
        with self.assertRaisesRegex(ValueError, "socket endpoint"):
            recovery.validate_inventory(self.folder, self.layout, inv)
        inv["derivations"].append(
            {
                "kind": "socket",
                "reason": "bounded socket",
                "references": ["case.py:2"],
                "base": "h",
                "case_component_bytes": 0,
                "suffix_bytes": 7,
                "maximum_encoded_bytes": 25,
            }
        )
        recovery.validate_inventory(self.folder, self.layout, inv)

    def test_shortdir_listenurl_cannot_be_nonendpoint(self):
        for text in ('path = short_dir / "ctl"\n', "listen_url = endpoint\n"):
            with self.subTest(text=text):
                inv = self.inventory(text, "non-endpoint")
                with self.assertRaisesRegex(ValueError, "socket endpoint"):
                    recovery.validate_inventory(self.folder, self.layout, inv)

    def test_case_and_additional_endpoint_tokens(self):
        lines = [
            "TMUX = value",
            "Tmux = value",
            "s.connect_ex(path)",
            "s.sendto(data, path)",
            "pair = socket.socketpair()",
            "mgr = multiprocessing.Manager()",
            "x = Listener(path)",
            'x = os.environ["XDG_RUNTIME_DIR"]',
        ]
        (self.folder / "case.py").write_text("\n".join(lines) + "\n")
        refs = recovery.source_candidates(self.folder)
        self.assertEqual(set(refs), {f"case.py:{i}" for i in range(1, len(lines) + 1)})

    def test_config_and_known_extensionless_launchers(self):
        names = [
            "tmux.conf",
            "worker.timer",
            "worker.path",
            "worker.mount",
            "worker.target",
            "run.zsh",
            "run.env",
            ".env",
            "Makefile",
            "Dockerfile",
            "justfile",
            "Procfile",
        ]
        for name in names:
            (self.folder / name).write_text("fixture endpoint configuration\n")
        refs = recovery.source_candidates(self.folder)
        self.assertEqual(set(refs), {f"{name}:1" for name in names})

    def test_fifo_is_rejected_before_read(self):
        os.mkfifo(self.folder / "launcher")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unsafe read")):
            with self.assertRaisesRegex(ValueError, "regular"):
                recovery.source_candidates(self.folder)

    def test_oversized_regular_file_holds_before_read(self):
        with (self.folder / "launcher").open("wb") as stream:
            stream.truncate(9 * 1024 * 1024)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unsafe read")):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                recovery.source_candidates(self.folder)

    def test_new_candidate_file_refuses_after_planning(self):
        (self.folder / "case.py").write_text('path = "fixture.sock"\n')
        candidates = recovery.source_candidates(self.folder)
        hashes = {"case.py": recovery.digest((self.folder / "case.py").read_bytes())}
        (self.folder / "new.py").write_text('path = "other.sock"\n')
        with self.assertRaisesRegex(ValueError, "candidate.*changed"):
            runner.revalidate_candidates(self.folder, candidates, hashes)

    def test_deep_scratch_records_hold_without_recursion_error(self):
        leaf = self.root / "r"
        for _ in range(80):
            leaf /= "d"
            leaf.mkdir()
        result = {"verification_result_or_error": {"PASS": "fixture"}}
        runner.finish_scratch(self.layout, self.folder, result)
        self.assertIn("HOLD", result["verification_result_or_error"])
        self.assertIn("depth", result["archive_error"])
        self.assertTrue(self.root.exists())

    def test_inspect_literal_substitution_preserves_mods_in_path(self):
        active = self.folder / "MODS-ROOT-fixture"
        code = runner.inspect_source(active)
        tree = ast.parse(code)
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)]
        root = assignments[0]
        assert isinstance(root.value, ast.Call)
        self.assertEqual(ast.literal_eval(root.value.args[0]), str(active))
        self.assertEqual(ast.literal_eval(assignments[1].value), runner.ORIGINS)


class ProcessReadReviewThreeTests(unittest.TestCase):
    def test_vanished_proc_esrch_is_not_a_writer(self):
        with patch.object(Path, "iterdir", return_value=iter([Path("/proc/123")])):
            with patch.object(Path, "read_text", side_effect=OSError(errno.ESRCH, "gone")):
                self.assertFalse(runner.group_has_writers(123))

    def test_inaccessible_proc_is_explicit_hold(self):
        with patch.object(Path, "iterdir", return_value=iter([Path("/proc/123")])):
            with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(ValueError, "Cannot inspect"):
                    runner.group_has_writers(123)
