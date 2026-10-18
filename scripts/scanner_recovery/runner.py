#!/usr/bin/env python3
"""Private recovery runner: one stage, one attempt. Default plan performs no test execution.
Root must review this exact file and release each attempt. Not an approval API.
Never imports an old driver or changes the actual source worktree.
"""

import argparse
import datetime as dt
import errno
import hashlib
import json
import math
import os
import runpy
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

if __package__:
    from . import environment as recovery
else:
    import environment as recovery

DOCUMENTS = Path(__file__).resolve().parents[2] / "docs/reviews/scanner-boundary"
MANIFEST = DOCUMENTS / "execution-fd-prerequisite-d4-inputs.json"
SUPPLEMENT = DOCUMENTS / "environment-contract.json"
FAILURE_MAP = DOCUMENTS / "environment-recovery-failure-map.json"
BASE = "07637745fe75dc614109a0b8ba72d3489f262a97"
B1 = [
    "src/heterodyne/config/secret_scan.py",
    "src/heterodyne/admind/redact.py",
    "src/heterodyne/admind/hook.py",
]
B2 = ["tests/test_admind_approvals.py", "tests/test_admind_r13_redact.py"]
ORIGINS = {
    "heterodyne.config.secret_scan": B1[0],
    "heterodyne.admind.redact": B1[1],
    "heterodyne.admind.hook": B1[2],
    "heterodyne.sandbox.creates": "src/heterodyne/sandbox/creates.py",
    "heterodyne.sandbox.hostread": "src/heterodyne/sandbox/hostread.py",
}
PATHS = list(
    dict.fromkeys(
        [
            *ORIGINS.values(),
            *B2,
            "tests/test_sandbox_hostread.py",
            "tests/test_secret_scan_boundary.py",
            "tests/conftest.py",
            "pyproject.toml",
            "uv.lock",
            "docs/reviews/scanner-boundary/expected-red-results.json",
        ]
    )
)
# Each required predecessor is a successful strict gate, not pytest exit zero.
STAGES = {
    "diagnostic": (375, None, "b1", []),
    "h92": (92, None, "baseline", ["diagnostic"]),
    "t1": (88, "t1", "baseline", ["diagnostic", "h92"]),
    "create-red": (6, "create-alert-before-patch", "baseline", ["diagnostic", "h92", "t1"]),
    "b1": (3373, "t2s2", "b1", ["diagnostic", "h92", "t1", "create-red"]),
    "overlap": (7, "t2s5", "b1", ["diagnostic", "h92", "t1", "create-red", "b1"]),
    "b2": (3373, None, "b2", ["diagnostic", "h92", "t1", "create-red", "b1", "overlap"]),
    "create-green": (6, None, "b2", ["diagnostic", "h92", "t1", "create-red", "b1", "overlap", "b2"]),
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def put(path, obj):
    with Path(path).open("x") as f:
        f.write(json.dumps(obj, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())


def stamp():
    return dt.datetime.now(dt.UTC).isoformat()


def instant(value):
    value = dt.datetime.fromisoformat(value)
    recovery.require(value.utcoffset() == dt.timedelta(0), "UTC-aware timestamp required")
    return value


def git(w, *args, binary=False):
    return subprocess.check_output(
        ["/usr/bin/git", "-C", str(w), *args],
        text=not binary,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )


def snapshot(w, paths=PATHS):
    return {
        "head": git(w, "rev-parse", "HEAD").strip(),
        "tree": git(w, "rev-parse", "HEAD^{tree}").strip(),
        "porcelain": git(w, "status", "--porcelain=v1"),
        "files": {p: sha((w / p).read_bytes()) for p in paths},
        "diff_sha256": sha(git(w, "diff", "HEAD", "--binary", binary=True)),
    }


def validate_supplement(supplement, manifest):
    original = manifest["result_contract"]
    recovery.require(
        supplement["original_required_fields"] == original["required_fields"]
        and supplement["original_environment_allowlist"] == original["environment_allowlist"],
        "Original contract changed",
    )
    recovery.require(
        supplement["required_fields"]
        == original["required_fields"] + supplement["additional_required_fields"],
        "Supplement field substitution",
    )
    recovery.require(
        supplement["environment_allowlist"] == original["environment_allowlist"] + ["XDG_RUNTIME_DIR"],
        "Supplement environment substitution",
    )
    recovery.require(
        len(supplement["additional_required_fields"]) == 6 and len(set(supplement["required_fields"])) == 57,
        "Exactly six additive fields required",
    )


def h92_collection_check(nodes, manifest):
    recovery.require(
        all(node.startswith("tests/test_sandbox_hostread.py::") for node in nodes),
        "H92 must contain only hostread nodes",
    )
    adjacency_index(nodes, manifest["collection_delta"])


def adjacency_index(nodes, delta):
    added = delta["added_node"]
    recovery.require(added in nodes, "Added regression missing")
    index = nodes.index(added)
    recovery.require(
        0 < index < len(nodes) - 1
        and nodes[index - 1] == delta["immediate_predecessor"]
        and nodes[index + 1] == delta["immediate_successor"],
        "Collection adjacency changed",
    )
    return index


def hold(result, message, error):
    result["verification_result_or_error"] = {
        "HOLD": message,
        "reason": type(error).__name__ + ": " + str(error),
        "previous": result["verification_result_or_error"],
    }


def finish_scratch(layout, folder, result):
    try:
        runtime = Path(layout["r"]["path"])
        recovery.require(recovery.private_directory(runtime) == layout["r"], "Runtime identity changed")
        descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        entries = []
        exposed = False
        try:
            opened = os.fstat(descriptor)
            recovery.require(
                (opened.st_dev, opened.st_ino) == (layout["r"]["device"], layout["r"]["inode"]),
                "Runtime identity changed",
            )
            for parent, names, parent_fd in recovery.directory_entries(descriptor):
                for name in names:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    entries.append(str(Path(parent) / name))
                    exposed |= name == "bus" or stat.S_ISSOCK(info.st_mode)
        finally:
            os.close(descriptor)
        result["runtime_entries_after"] = entries
        recovery.require(not exposed, "Runtime contains socket/bus")
    except (OSError, ValueError, KeyError) as error:
        result["runtime_error"] = str(error)
        hold(result, "Runtime check failed", error)
    # Preserve regardless of runtime result. An archive failure retains both earlier diagnostics.
    try:
        archived = recovery.archive_scratch(layout, folder / "scratch-archive")
        result["scratch_archive_inventory_sha256"] = archived["sha256"]
        result["scratch_archive"] = archived
        bounds = result.get("socket_path_budget_inventory", {}).get("receipt", {}).get("derivations")
        if bounds is not None:
            for observed in archived["surviving_socket_paths"]:
                relative = Path(observed["path"])
                base = relative.parts[0] if relative.parts[0] in recovery.CHILDREN else "root"
                maxima = [
                    row["maximum_encoded_bytes"]
                    for row in bounds
                    if row["kind"] == "socket" and row["base"] == base
                ]
                recovery.require(
                    bool(maxima) and observed["socket_encoded_bytes"] <= max(maxima),
                    "Observed socket exceeds source-backed reviewed maximum",
                )
                recovery.require(
                    observed["socket_encoded_bytes"] <= 100, "Observed socket exceeds100byte budget"
                )
    except (OSError, ValueError) as error:
        result["archive_error"] = str(error)
        hold(result, "Scratch preservation failed", error)


def diagnostic_selection(result_data, collection_data, failure_data, supplement):
    recovery.require(sha(result_data) == supplement["failed_result_sha256"], "Failed result pin changed")
    failed = json.loads(result_data)
    recovery.require(
        sha(collection_data) == failed["raw_collection_file_sha256"], "Failed collection pin changed"
    )
    failure_map = json.loads(failure_data)
    recovery.require(
        failure_map["source_result_sha256"] == supplement["failed_result_sha256"],
        "Failure map result pin changed",
    )
    unexpected = {
        row["node"]
        for row in failure_map["failures"]
        if row["classification"] != "expected-scanner-assertion"
    }
    selection = [node for node in json.loads(collection_data) if node in unexpected]
    recovery.require(
        len(selection) == len(set(selection)) == len(unexpected) == 375,
        "Exact375diagnostic identities required",
    )
    return selection


def predecessor_check(stage, old, receipt, supplement, chain_identity):
    recovery.require(
        old["stage"] == stage and "PASS" in old["verification_result_or_error"], "Predecessor did not pass"
    )
    recovery.require(old["unavailable_fields"] == [], "Predecessor missing evidence")
    recovery.validate_result(old, supplement)
    recovery.require(
        old["source_receipt_identity"]["task"] == receipt["task"]
        and old["b1_three_file_sha256"] == receipt["b1_three_file_sha256"]
        and old["source_receipt_identity"]["h_delivery_evidence_sha256"]
        == receipt["h_delivery_evidence_sha256"],
        "Predecessor source chain changed",
    )
    recovery.require(old["chain_identity"] == chain_identity, "Predecessor driver/environment chain changed")
    if stage != "b2":
        recovery.require(
            old["b2_target_file_sha256"] == receipt["b2_before_file_sha256"], "Predecessor B2 source changed"
        )
    if stage == "diagnostic":
        recovery.require(
            old["verification_result_or_error"] == {"PASS": {"total": 375, "failed": 0, "passed": 375}},
            "Diagnostic375PASS required",
        )
    # Every common predecessor reference must be byte-identical through the chain.
    for name, ref in old["source_receipt_identity"]["predecessors"].items():
        recovery.require(
            receipt["predecessors"].get(name) == ref, "Predecessor reference chain changed: " + name
        )


def pinned_inputs(w, original, h92=False, supplement=None):
    m = load(MANIFEST)
    supplement = load(SUPPLEMENT) if supplement is None else supplement
    validate_supplement(supplement, m)
    recovery.require(
        sha(MANIFEST.read_bytes()) == supplement["original_manifest_sha256"],
        "Runner precondition failed at original line 78",
    )
    for row in m["private_originals"]:
        data = (original / Path(row["private_relative"]).name).read_bytes()
        recovery.require(
            sha(data) == row["sha256"] and len(data) == row["bytes"],
            "Runner precondition failed at original line 83",
        )
    for row in m["locked_environment_inputs"]:
        recovery.require(
            sha((w / row["path"]).read_bytes()) == row["sha256"],
            "Runner precondition failed at original line 85",
        )
    lines = (original / "scanner_evidence.py").read_bytes().splitlines(keepends=True)
    chunks = {}
    for row in m["strict_verifier_function_pins"]:
        data = b"".join(lines[row["line_start"] - 1 : row["line_end"]])
        recovery.require(
            sha(data) == row["sha256"] and len(data) == row["bytes"],
            "Runner precondition failed at original line 90",
        )
        chunks[row["name"]] = data
    recovery.require(
        sha((w / "tests/test_sandbox_hostread.py").read_bytes())
        == m["blob_identities"]["fixed_test"]["sha256"],
        "Runner precondition failed at original line 92",
    )
    recovery.require(
        sha((w / ORIGINS["heterodyne.sandbox.hostread"]).read_bytes())
        == m["blob_identities"]["unchanged_product_all_three_trees"]["sha256"],
        "Runner precondition failed at original line 93",
    )
    a = m["appendix_a_test_module"]
    if not h92:
        recovery.require(
            sha((w / a["path"]).read_bytes()) == a["sha256"], "Runner precondition failed at original line 96"
        )
    expected_path = w / "docs/reviews/scanner-boundary/expected-red-results.json"
    expected = (
        git(
            w,
            "show",
            "1995977b7e0fab4d6caa8ef5d50fea2d80886fa4:" + str(expected_path.relative_to(w)),
            binary=True,
        )
        if h92
        else expected_path.read_bytes()
    )
    recovery.require(
        sha(expected) == m["preserved_scanner_protocol"]["expected_data_sha256"],
        "Runner precondition failed at original line 100",
    )
    recovery.require(not (w / ".venv").exists() and not (w / ".venv").is_symlink(), ".venv must be absent")
    return m, chunks, json.loads(expected)["stages"]


def slot_check(path, original, seconds=0):
    recovery.require(load(path) == original, "Reservation changed")
    now = dt.datetime.now(dt.UTC)
    recovery.require(instant(original["start_utc"]) <= now, "Runner precondition failed at original line 107")
    recovery.require(
        (instant(original["expires_utc"]) - now).total_seconds() > seconds, "Reservation too short/expired"
    )


def process_snapshot():
    entries = sorted((p for p in Path("/proc").iterdir() if p.name.isdecimal()), key=lambda p: int(p.name))
    rows = []
    for p in entries[:2048]:
        row = {"pid": int(p.name), "uid": None, "start_ticks": None, "read_errno": None}
        try:
            row.update(
                uid=p.stat().st_uid, start_ticks=int((p / "stat").read_text().rsplit(")", 1)[1].split()[19])
            )
        except OSError as exc:
            row["read_errno"] = exc.errno
        rows.append(row)
    return {
        "at": stamp(),
        "entries": rows,
        "truncated": len(entries) > 2048,
        "diagnostic_only_not_quiescence_or_dumpability_proof": True,
    }


def group_has_writers(group):
    """Read Linux process states; permission/read races fail closed except vanished PIDs."""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ESRCH):
                continue
            raise ValueError("Cannot inspect child process group") from error
        if int(fields[2]) == group and fields[0] not in ("Z", "X"):
            return True
    return False


def run(argv, env, cwd, folder, name, timeout, commands):
    t = time.monotonic()
    record = {
        "argv": argv,
        "cwd": str(cwd),
        "timeout_seconds": timeout,
        "started_at": stamp(),
        "process_group_quiescent": False,
        "quiescence_scope": "original-PGID-only; no containment of setsid/tmux-daemon escape",
        "global_descendant_quiescence_proven": False,
    }
    commands.append(record)
    with (folder / name).open("xb") as log:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        timed_out = False
        # Observe exit without reaping: the PID/PGID cannot be reused before killpg.
        deadline = time.monotonic() + timeout
        try:
            while os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.01)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            status = proc.wait(timeout=10)
        # Killed orphan zombies cannot write; any live group member prevents preservation.
        quiet_deadline = time.monotonic() + 5
        while group_has_writers(proc.pid):
            recovery.require(time.monotonic() < quiet_deadline, "Child process group did not quiesce")
            time.sleep(0.01)
        record["process_group_quiescent"] = True
    record.update(actual_exit_or_signal=status, timeout=timed_out, elapsed_seconds=time.monotonic() - t)
    return record


def revalidate_candidates(source, candidates, file_hashes):
    """Re-enumerate the candidate set as well as every whole source-file identity."""
    recovery.require(
        recovery.source_candidates(source) == candidates,
        "Inventory candidate set changed after planning",
    )
    recovery.require(
        {name: sha(recovery.source_bytes(source / name)) for name in file_hashes} == file_hashes,
        "Inventory candidate source files changed after planning",
    )


def inspect_source(active):
    """Replace the one assignment template before inserting any literal path bytes."""
    return INSPECT.replace(
        "root=pathlib.Path(ROOT); mods=MODS",
        f"root=pathlib.Path({str(active)!r}); mods={ORIGINS!r}",
    )


def collection_check(nodes, count, m, broad):
    recovery.require(len(nodes) == len(set(nodes)) == count, "Exact count and uniqueness required")
    if broad:
        d = m["collection_delta"]
        i = adjacency_index(nodes, d)
        minus = nodes[:i] + nodes[i + 1 :]
        recovery.require(
            sha(json.dumps(minus).encode()) == d["ordered_minus_added_legacy_sha256"],
            "Runner precondition failed at original line 148",
        )
        metadata = m["metadata19"]["nodes"]
        recovery.require(
            [n for n in nodes if n in set(metadata)] == metadata, "Exact original metadata19 order"
        )


# Passed as literal source to the private interpreter; no application import in this driver.
INSPECT = """import importlib,importlib.metadata,json,pathlib,sys,tomllib,hashlib
root=pathlib.Path(ROOT); mods=MODS
out={'python_version':sys.version,'realpath':str(pathlib.Path(sys.executable).resolve()),'imports':{},'distributions':[]}
assert sys.version_info[:2]==(3,12)
lock=tomllib.loads((root/'uv.lock').read_text()); norm=lambda s:s.lower().replace('_','-').replace('.','-')
locked={(norm(p['name']),p['version']) for p in lock['package']}
for d in importlib.metadata.distributions():
 name=d.metadata['Name']; version=d.version
 assert (norm(name),version) in locked,(name,version)
 out['distributions'].append({'name':name,'version':version,'metadata_origin':str(d._path)})
out['distributions'].sort(key=lambda d:d['name'].lower())
for name,relative in mods.items():
 p=pathlib.Path(importlib.import_module(name).__file__).resolve()
 assert p==(root/relative).resolve(),(name,str(p))
 out['imports'][name]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
import _hypothesis_pytestplugin as h
out['hypothesis_plugin']={'path':str(pathlib.Path(h.__file__).resolve()),'sha256':hashlib.sha256(pathlib.Path(h.__file__).read_bytes()).hexdigest(),'version':importlib.metadata.version('hypothesis')}
print(json.dumps(out))
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--stage", choices=STAGES, required=True)
    ap.add_argument("--worktree", type=Path, required=True)
    ap.add_argument("--private-originals", type=Path, required=True)
    ap.add_argument("--python", type=Path, required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--reservation", type=Path, required=True)
    ap.add_argument("--source-receipt", type=Path, required=True)
    ap.add_argument("--source-receipt-sha256", required=True)
    ap.add_argument("--root-release", type=Path)
    ap.add_argument("--inventory", type=Path, required=True)
    ap.add_argument("--baseline-disposition", type=Path, required=True)
    ap.add_argument("--failed-result", type=Path)
    ap.add_argument("--failed-collection", type=Path)
    a = ap.parse_args()
    w = a.worktree.resolve()
    recovery.require(
        a.worktree.is_absolute() and a.python.is_absolute() and a.python.is_file(),
        "Runner precondition failed at original line 192",
    )
    recovery.require(
        a.run_dir.is_absolute() and not a.run_dir.exists() and not a.run_dir.is_symlink(),
        "Runner precondition failed at original line 193",
    )
    recovery.require((w / ".git").is_file(), "Isolated linked worktree required")
    merge = Path(git(w, "rev-parse", "--git-path", "MERGE_HEAD").strip())
    recovery.require(not (merge if merge.is_absolute() else w / merge).exists(), "Unfinished merge")
    original = a.private_originals.resolve()
    recovery.require(
        original.is_dir() and a.private_originals.is_absolute(),
        "Runner precondition failed at original line 198",
    )
    supplement_data = SUPPLEMENT.read_bytes()
    supplement = json.loads(supplement_data)
    m, chunks, expected = pinned_inputs(w, original, a.stage == "h92", supplement)
    snapshot_paths = (
        [
            p
            for p in PATHS
            if p
            not in (
                "tests/test_secret_scan_boundary.py",
                "docs/reviews/scanner-boundary/expected-red-results.json",
            )
        ]
        if a.stage == "h92"
        else PATHS
    )
    count, expected_key, phase, required = STAGES[a.stage]
    disposition_data = a.baseline_disposition.read_bytes()
    disposition = json.loads(disposition_data)
    inventory_data = a.inventory.read_bytes()
    source_inventory = json.loads(inventory_data)
    failure_data = FAILURE_MAP.read_bytes()
    reservation_data = a.reservation.read_bytes()
    chain_identity = {
        "driver": sha(Path(__file__).read_bytes()),
        "support": sha(Path(recovery.__file__).read_bytes()),
        "supplement": sha(supplement_data),
        "disposition": sha(disposition_data),
        "failure_map": sha(failure_data),
    }
    recovery.require(
        disposition["mode"] == "FRESH" and disposition["approval"] == "btq-7k1bf",
        "Runner precondition failed at original line 204",
    )
    recovery.require(
        disposition["approved_by"] == "Liam" and disposition["via_ref"] == "marmot:id:2a9d3b2de451",
        "Runner precondition failed at original line 205",
    )
    recovery.require(
        disposition["failed_attempt"] == supplement["failed_attempt"],
        "Runner precondition failed at original line 206",
    )
    if a.stage == "h92":
        recovery.require(
            git(w, "rev-parse", "HEAD").strip() == "e22f4ed366d6ddcdc1a5d7f0c9fe30d088b12ea8",
            "Runner precondition failed at original line 208",
        )
    before = snapshot(w, snapshot_paths)
    recovery.require(a.stage != "h92" or not before["porcelain"], "H92 requires clean porcelain")
    recovery.require(before["head"] == a.head, "Runner precondition failed at original line 210")
    receipt_data = a.source_receipt.read_bytes()
    receipt = json.loads(receipt_data)
    receipt_sha = sha(receipt_data)
    recovery.require(
        receipt_sha == a.source_receipt_sha256, "Runner precondition failed at original line 213"
    )
    recovery.require(
        receipt["task"] == "btq-1wu2i" and receipt["stage"] == a.stage and receipt["phase"] == phase,
        "Runner precondition failed at original line 214",
    )
    recovery.require(receipt["source_snapshot"] == before, "Exact frozen source receipt mismatch")
    recovery.require(
        receipt["source_planning_commits"] and receipt["authority_evidence_sha256"],
        "Runner precondition failed at original line 216",
    )
    recovery.require(
        receipt["environment_provenance_sha256"] and receipt["h_delivery_evidence_sha256"],
        "Runner precondition failed at original line 217",
    )
    recovery.require(
        receipt["b1_three_file_sha256"].keys() == dict.fromkeys(B1).keys(),
        "Runner precondition failed at original line 218",
    )
    recovery.require(
        receipt["b2_target_file_sha256"].keys() == dict.fromkeys(B2).keys(),
        "Runner precondition failed at original line 219",
    )
    if phase == "baseline":
        for p in [*B1, ORIGINS["heterodyne.sandbox.creates"]]:
            recovery.require(
                (w / p).read_bytes() == git(w, "show", BASE + ":" + p, binary=True),
                "Runner precondition failed at original line 222",
            )
    else:
        recovery.require(
            {p: before["files"][p] for p in B1} == receipt["b1_three_file_sha256"],
            "Runner precondition failed at original line 224",
        )
    recovery.require(
        {p: before["files"][p] for p in B2} == receipt["b2_target_file_sha256"],
        "Runner precondition failed at original line 225",
    )
    recovery.require(
        receipt["b2_before_file_sha256"].keys() == dict.fromkeys(B2).keys(),
        "Runner precondition failed at original line 226",
    )
    if phase != "b2":
        recovery.require(
            receipt["b2_before_file_sha256"] == receipt["b2_target_file_sha256"],
            "Runner precondition failed at original line 228",
        )
    for stage in required:
        ref = receipt["predecessors"][stage]
        predecessor_data = Path(ref["path"]).read_bytes()
        recovery.require(sha(predecessor_data) == ref["sha256"], "Predecessor bytes changed")
        predecessor_check(stage, json.loads(predecessor_data), receipt, supplement, chain_identity)
    diagnostic_ref = receipt.get("predecessors", {}).get("diagnostic")
    reservation = json.loads(reservation_data)
    recovery.require(
        reservation["task"] == "btq-1wu2i"
        and reservation["source_head"] == a.head
        and reservation["stage"] == a.stage,
        "Runner precondition failed at original line 251",
    )
    recovery.require(
        reservation["exclusive"] is True and reservation["status"] == "reserved",
        "Runner precondition failed at original line 252",
    )
    recovery.require(
        isinstance(reservation["reservation_id"], str) and 1 <= len(reservation["reservation_id"]) <= 128,
        "Runner precondition failed at original line 253",
    )
    slot_check(a.reservation, reservation)
    plan = {
        "mode": "execute" if a.execute else "plan",
        "stage": a.stage,
        "phase": phase,
        "source": before,
        "count": count,
        "expected_key": expected_key,
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "support_sha256": sha(Path(recovery.__file__).read_bytes()),
        "supplement_sha256": sha(supplement_data),
        "failure_map_sha256": sha(failure_data),
        "inventory_sha256": sha(inventory_data),
        "baseline_disposition_sha256": sha(disposition_data),
        "source_receipt_sha256": receipt_sha,
        "reservation": reservation,
        "no_execution_authority_inferred": True,
    }
    planned_layout = {key: {"path": "/" + "x" * 15 + "/" + key} for key in recovery.CHILDREN}
    planned_layout["root"] = {"path": "/" + "x" * 15}
    candidates = recovery.source_candidates(w)
    candidate_files = {reference.rsplit(":", 1)[0] for reference in candidates}
    candidate_file_hashes = {name: sha(recovery.source_bytes(w / name)) for name in sorted(candidate_files)}
    recovery.validate_inventory(
        w, planned_layout, source_inventory, raw=inventory_data, measured=True, candidates=candidates
    )
    recovery.construction_probes(supplement)
    diagnostic_nodes: list[str] = []
    if a.stage == "diagnostic":
        if a.failed_result is None or a.failed_collection is None:
            raise ValueError("Failed result and collection required before release")
        diagnostic_nodes = diagnostic_selection(
            a.failed_result.read_bytes(), a.failed_collection.read_bytes(), failure_data, supplement
        )
    plan["chain_identity"] = chain_identity
    if not a.execute:
        print(json.dumps(plan, indent=2))
        return 0
    recovery.require(a.root_release, "Separate root release of reviewed exact driver/stage required")
    release = load(a.root_release)
    recovery.require(
        release
        == {
            "task": "btq-1wu2i",
            "stage": a.stage,
            "source_head": a.head,
            "source_receipt_sha256": receipt_sha,
            "driver_sha256": plan["runner_sha256"],
            "reservation_sha256": sha(reservation_data),
            "run_directory": str(a.run_dir),
            **{
                key: plan[key]
                for key in (
                    "support_sha256",
                    "supplement_sha256",
                    "failure_map_sha256",
                    "inventory_sha256",
                    "baseline_disposition_sha256",
                )
            },
        },
        "Runner precondition failed at original line 268",
    )
    slot_check(a.reservation, reservation, 3900)
    os.umask(0o077)
    a.run_dir.mkdir(mode=0o700)
    folder = a.run_dir.resolve()
    result: dict[str, Any] = {k: None for k in supplement["required_fields"]}
    result.update(
        chain_identity=chain_identity,
        run_id=folder.name,
        stage=a.stage,
        source_head=a.head,
        source_receipt_identity=receipt,
        source_planning_commits=receipt["source_planning_commits"],
        before_porcelain=before["porcelain"],
        source_file_hashes=before["files"],
        b1_three_file_sha256=receipt["b1_three_file_sha256"],
        b2_target_file_sha256=receipt["b2_target_file_sha256"],
        driver_source_sha256=plan["runner_sha256"],
        design_manifest_sha256=sha(MANIFEST.read_bytes()),
        attempt_timestamp=stamp(),
        invocation_argv=[],
        timeout_seconds=3600,
        verification_result_or_error={"HOLD": "Not completed"},
        not_applicable_reasons={},
        kernel_identity=list(os.uname()),
        venv_alias_before_target_or_absence="absent",
        broad_slot_reservation_identity_start_end={
            "reservation": reservation,
            "actual_start": stamp(),
            "actual_end": None,
        },
        root_release_sha256=sha(a.root_release.read_bytes()),
    )
    for key, path in [
        ("b1_patch_sha256", original / "b1.patch"),
        ("b2_patch_sha256", original / "b2.patch"),
        ("modules_sha256", original / "targeted-modules.json"),
        ("uv_lock_sha256", w / "uv.lock"),
        ("pyproject_sha256", w / "pyproject.toml"),
    ]:
        result[key] = sha(path.read_bytes())
    result["expected_data_sha256"] = m["preserved_scanner_protocol"]["expected_data_sha256"]
    result["hostread_test_blob"] = git(w, "hash-object", "tests/test_sandbox_hostread.py").strip()
    result["hostread_product_blob"] = git(w, "hash-object", ORIGINS["heterodyne.sandbox.hostread"]).strip()
    nodes: list[str] = []
    started = time.monotonic()
    layout = None
    try:
        construction = recovery.construction_probes(supplement)
        put(folder / "construction-probes.json", construction)
        result["construction_probes_sha256"] = sha((folder / "construction-probes.json").read_bytes())
        layout = recovery.create_layout()
        result["private_runtime_layout"] = layout
        result["environment_filesystem_preflight"] = recovery.filesystem_preflight(layout)
        revalidate_candidates(w, candidates, candidate_file_hashes)
        result["inventory_source_file_sha256"] = candidate_file_hashes
        result["socket_path_budget_inventory"] = recovery.validate_inventory(
            w, layout, source_inventory, raw=inventory_data, measured=True, candidates=candidates
        )
        result["socket_construction_probe"] = recovery.socket_construction_probe(layout)
        result["baseline_mode_disposition"] = {
            "receipt": disposition,
            "sha256": plan["baseline_disposition_sha256"],
        }
        result["diagnostic_result_reference"] = (
            diagnostic_ref if diagnostic_ref else {"not_applicable": "This is the diagnostic itself"}
        )
        for name in ["plugins"]:
            (folder / name).mkdir(mode=0o700)
        active = w
        if a.stage == "overlap":
            active = folder / "disposable-source"
            active.mkdir(mode=0o700)
            for name in ["src", "tests"]:
                shutil.copytree(w / name, active / name, ignore=shutil.ignore_patterns("__pycache__"))
            for name in ["pyproject.toml", "uv.lock"]:
                shutil.copy2(w / name, active / name)
            (active / B1[1]).write_bytes(git(w, "show", BASE + ":" + B1[1], binary=True))
        result["active_source_root"] = str(active)
        active_hashes = {p: sha((active / p).read_bytes()) for p in ORIGINS.values()}
        plugin = folder / "plugins"
        for name in ["scanner_probe.py", "probe_verifier.py"]:
            (plugin / name).write_bytes((original / name).read_bytes())
        verifier = (
            b"import re\nimport xml.etree.ElementTree as ET\nfrom pathlib import Path\nHERE=Path("
            + repr(str(folder)).encode()
            + b")\n"
            + chunks["mask"]
            + b"\n"
            + chunks["verify"]
        )
        (plugin / "scanner_evidence.py").write_bytes(verifier)
        result.update(
            verifier_function_source_sha256=sha(chunks["verify"]),
            mask_function_source_sha256=sha(chunks["mask"]),
            probe_harness_sha256=sha((plugin / "probe_verifier.py").read_bytes()),
            identity_plugin_sha256=sha((plugin / "scanner_probe.py").read_bytes()),
        )
        env = recovery.environment(layout, a.python, active, plugin, folder / "collected.json", supplement)
        result.update(
            sanitized_environment_allowlist_values=env.copy(),
            pythonpath_order=[str(active / "src"), str(plugin)],
            hypothesis_fresh_directory=layout["y"]["path"],
        )
        put(folder / "process-snapshot.json", process_snapshot())
        result["process_snapshot_sha256"] = sha((folder / "process-snapshot.json").read_bytes())
        r = run(
            [str(a.python), str(plugin / "probe_verifier.py")],
            env,
            active,
            folder,
            "verifier-probes.log",
            30,
            result["invocation_argv"],
        )
        recovery.require(
            r["actual_exit_or_signal"] == 0 and not r["timeout"],
            "Runner precondition failed at original line 331",
        )
        recovery.require(
            load(folder / "verifier-probes.json")["rejected"] == 19,
            "Runner precondition failed at original line 332",
        )
        if a.stage == "diagnostic":
            selection = diagnostic_nodes
        elif a.stage == "h92":
            selection = ["tests/test_sandbox_hostread.py"]
        elif a.stage in ["b1", "b2"]:
            selection = load(original / "targeted-modules.json")
            recovery.require(len(selection) == 81, "Runner precondition failed at original line 347")
        elif a.stage in ["create-red", "create-green"]:
            selection = ["tests/test_sandbox_scanner_integration.py"]
        else:
            selection = ["tests/test_secret_scan_boundary.py"] + (
                ["-k", "overlapping_kinds"] if a.stage == "overlap" else []
            )
        cmd = [
            str(a.python),
            "-m",
            "pytest",
            "-q",
            "-p",
            "scanner_probe",
            "-p",
            "_hypothesis_pytestplugin",
            "--basetemp=" + layout["p"]["path"],
            *selection,
        ]
        result["per_stage_import_hash_assertions"] = {}
        for phase_name in ["before_collection", "before_execution"]:
            slot_check(a.reservation, reservation, 3660 if phase_name == "before_execution" else 0)
            recovery.require(snapshot(w, snapshot_paths) == before, "Real source drift")
            recovery.require(
                {p: sha((active / p).read_bytes()) for p in ORIGINS.values()} == active_hashes,
                "Runner precondition failed at original line 357",
            )
            code = inspect_source(active)
            r = run(
                [str(a.python), "-c", code],
                env,
                active,
                folder,
                "origins-" + phase_name + ".json",
                30,
                result["invocation_argv"],
            )
            recovery.require(
                r["actual_exit_or_signal"] == 0 and not r["timeout"],
                "Runner precondition failed at original line 360",
            )
            inventory = load(folder / ("origins-" + phase_name + ".json"))
            recovery.require(
                inventory["realpath"] == str(a.python.resolve()),
                "Runner precondition failed at original line 362",
            )
            recovery.require(
                {name: row["sha256"] for name, row in inventory["imports"].items()}
                == {name: active_hashes[path] for name, path in ORIGINS.items()},
                "Runner precondition failed at original line 363",
            )
            result.update(
                interpreter_realpath=inventory["realpath"],
                python_version=inventory["python_version"],
                installed_distribution_inventory=inventory["distributions"],
                editable_import_origins=inventory["imports"],
                explicit_plugin_names_versions={
                    "scanner_probe": result["identity_plugin_sha256"],
                    "_hypothesis_pytestplugin": inventory["hypothesis_plugin"],
                },
            )
            result["per_stage_import_hash_assertions"][phase_name] = inventory["imports"]
            if phase_name == "before_collection":
                r = run(
                    cmd + ["--collect-only"],
                    env,
                    active,
                    folder,
                    "collection.log",
                    180,
                    result["invocation_argv"],
                )
                recovery.require(
                    r["actual_exit_or_signal"] == 0 and not r["timeout"],
                    "Runner precondition failed at original line 370",
                )
                nodes = load(folder / "collected.json")
                collection_check(nodes, count, m, a.stage in ["b1", "b2"])
                if a.stage == "h92":
                    h92_collection_check(nodes, m)
                if a.stage == "diagnostic":
                    recovery.require(nodes == selection, "Diagnostic original collection order changed")
                (folder / "collection-pinned.json").write_bytes((folder / "collected.json").read_bytes())
            else:
                env["SCANNER_COLLECTION"] = str(folder / "execution-collection.json")
                result["execution_environment_allowlist_values"] = env.copy()
                slot_check(a.reservation, reservation, 3660)
                r = run(
                    cmd + ["--junitxml=" + str(folder / "junit.xml")],
                    env,
                    active,
                    folder,
                    "run.log",
                    3600,
                    result["invocation_argv"],
                )
                result.update({k: r[k] for k in ["actual_exit_or_signal", "elapsed_seconds"]})
                recovery.require(not r["timeout"], "Execution timeout")
                recovery.require(
                    load(folder / "execution-collection.json") == nodes,
                    "Runner precondition failed at original line 383",
                )
                namespace = runpy.run_path(str(plugin / "scanner_evidence.py"))
                failures = expected[expected_key]["failing_nodes"] if expected_key else []
                verdict = namespace["verify"](
                    (folder / "junit.xml").read_bytes(), nodes, r["actual_exit_or_signal"], failures
                )
                recovery.require(
                    verdict == {"total": count, "failed": len(failures), "passed": count - len(failures)},
                    "Runner precondition failed at original line 388",
                )
                result["verification_result_or_error"] = {"PASS": verdict}
        recovery.require(snapshot(w, snapshot_paths) == before, "Source drift after execution")
    except BaseException as exc:
        if isinstance(exc, recovery.LayoutCreationError):
            layout = exc.layout
            result["private_runtime_layout"] = layout
            result["partial_layout_error"] = str(exc)
        result["verification_result_or_error"] = {"HOLD": type(exc).__name__, "reason": str(exc)}
    finally:
        if (folder / "junit.xml").exists():
            try:
                durations = []
                # The pinned pytest invocation wrote this report in its private evidence directory.
                report = ET.fromstring((folder / "junit.xml").read_bytes())  # noqa: S314 - pytest report
                for case in report.findall(".//testcase"):
                    props = case.findall('./properties/property[@name="scanner_nodeid"]')
                    seconds = float(case.attrib["time"])
                    recovery.require(
                        len(props) == 1 and math.isfinite(seconds) and seconds >= 0,
                        "Runner precondition failed at original line 400",
                    )
                    durations.append({"node": props[0].attrib["value"], "seconds": seconds})
                result["per_node_durations"] = durations
            except (ET.ParseError, ValueError, KeyError, AssertionError) as exc:
                result["duration_capture_error"] = type(exc).__name__ + ": " + str(exc)
        result["elapsed_seconds"] = time.monotonic() - started
        try:
            after = snapshot(w, snapshot_paths)
            result["after_porcelain"] = after["porcelain"]
            result["source_snapshot_after"] = after
        except (OSError, subprocess.SubprocessError) as exc:
            result["source_readback_error"] = type(exc).__name__
            after = None
        result["venv_alias_after_absence"] = not (w / ".venv").exists() and not (w / ".venv").is_symlink()
        result["broad_slot_reservation_identity_start_end"]["actual_end"] = stamp()
        for key, name in [
            ("log_sha256", "run.log"),
            ("junit_sha256", "junit.xml"),
            ("raw_collection_file_sha256", "collection-pinned.json"),
            ("execution_collection_sha256", "execution-collection.json"),
        ]:
            if (folder / name).exists():
                result[key] = sha((folder / name).read_bytes())
        if (folder / "collection-pinned.json").exists():
            nodes = load(folder / "collection-pinned.json")
            result["legacy_node_list_sha256"] = sha(json.dumps(nodes).encode())
            result["portable_node_list_sha256"] = sha(
                json.dumps(nodes, ensure_ascii=True, separators=(",", ":")).encode()
            )
        if layout is not None:
            if all(command.get("process_group_quiescent") is True for command in result["invocation_argv"]):
                finish_scratch(layout, folder, result)
            else:
                result["archive_error"] = "Unconfirmed child group quiescence; scratch retained in place"
                hold(result, "Scratch copy refused before quiescence", ValueError(result["archive_error"]))
        result["unavailable_fields"] = [k for k in supplement["required_fields"] if result.get(k) is None]
        try:
            slot_check(a.reservation, reservation)
            slot_ok = True
        except (OSError, ValueError, KeyError, AssertionError):
            slot_ok = False
        if (
            after != before
            or not slot_ok
            or not result["venv_alias_after_absence"]
            or result["unavailable_fields"]
        ):
            result["verification_result_or_error"] = {
                "HOLD": "Missing evidence/source drift/reservation/alias",
                "previous": result["verification_result_or_error"],
            }
        put(folder / "result.json", result)
    print(
        json.dumps(
            {"result": str(folder / "result.json"), "verification": result["verification_result_or_error"]}
        )
    )
    return 0 if "PASS" in result["verification_result_or_error"] else 1


if __name__ == "__main__":
    sys.exit(main())
