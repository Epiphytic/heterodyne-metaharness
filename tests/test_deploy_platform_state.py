"""deploy/platform_state.py (design btq-v7sgt D6): the install record, and the order around every unit
write, so that a failure at any point leaves the intent on disk before the change it describes."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "platform_state.py"
U = "heterodyne-admind.service"
C = "a" * 40
MARKED = (f"# Rendered by heterodyne's deploy/install.sh for release {C}; the next install replaces it.\n"
          "[Unit]\n")
W_NAME = "heterodyne-wsd.service"
HAND = "[Unit]\nDescription=by hand\n"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("platform_state", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = load()


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", "-c", SCRIPT.read_text(), *args], capture_output=True,
                          text=True, timeout=60, check=False)


class Paths:
    def __init__(self, tmp: Path) -> None:
        self.units = tmp / "units"
        self.units.mkdir()
        self.install = tmp / "install"
        self.record = self.install / "record.json"
        self.backups = self.install / "backup"
        self.file = self.units / U
        self.render = tmp / "render"
        self.render.write_text(MARKED + "rev 2\n")

    def plan(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return run("plan", "--unit", U, "--record", str(self.record), "--file", str(self.file), "--render",
                   str(self.render), "--backup-dir", str(self.backups), *extra)

    def entry(self) -> dict[str, str] | None:
        return json.loads(self.record.read_text())["units"].get(U) if self.record.exists() else None


@pytest.fixture
def p(tmp_path: Path) -> Paths:
    return Paths(tmp_path)


# --- the record ---

def test_a_missing_record_is_empty(p: Paths) -> None:
    assert P.load_record(p.record) == {"version": 1, "release": None, "units": {}}


@pytest.mark.parametrize("text", ["", "{", "[]", '{"version": 2, "release": null, "units": {}}',
                                  '{"version": 1, "release": null, "units": {"x": {"state": "odd"}}}'])
def test_a_malformed_record_refuses(p: Paths, text: str) -> None:
    p.install.mkdir()
    p.record.write_text(text)
    with pytest.raises(P.RecordError):
        P.load_record(p.record)
    out = p.plan()
    assert out.returncode == 1 and out.stdout == "" and "install record" in out.stderr


def test_the_record_is_written_atomically_0600_in_a_0700_directory(p: Paths) -> None:
    P.save_record(p.record, {"version": 1, "release": C, "units": {}})
    assert (p.record.stat().st_mode & 0o777, p.install.stat().st_mode & 0o777) == (0o600, 0o700)
    assert [x.name for x in p.install.iterdir()] == ["record.json"]


# --- file kinds ---

def test_file_kinds(p: Paths, tmp_path: Path) -> None:
    assert P.file_kind(p.file) == "absent"
    p.file.write_text(MARKED)
    assert P.file_kind(p.file) == "marker"
    p.file.write_text(HAND)
    assert P.file_kind(p.file) == "hand-made"
    p.file.unlink()
    p.file.symlink_to(tmp_path / "render")
    assert P.file_kind(p.file).startswith("refuse:")
    p.file.unlink()
    p.file.mkdir()
    assert P.file_kind(p.file).startswith("refuse:")


# --- plan, for every row of D6 ---

def test_no_file_no_entry_creates(p: Paths) -> None:
    assert p.plan().stdout == "create\n"


def test_a_hand_made_file_needs_adopt(p: Paths) -> None:
    p.file.write_text(HAND)
    out = p.plan()
    assert out.returncode == 1 and "--adopt" in out.stderr and U in out.stderr
    assert p.plan("--adopt").stdout == "adopt\n"


def test_a_marker_file_without_an_entry_refuses(p: Paths) -> None:
    p.file.write_text(MARKED)
    out = p.plan("--adopt")
    assert out.returncode == 1 and "not in the install record" in out.stderr


def adopt_through(p: Paths, stop_after: str) -> None:
    """The four D6 steps for an adoption, stopping after `stop_after`."""
    name = run("backup", "--unit", U, "--file", str(p.file), "--backup-dir", str(p.backups)).stdout.strip()
    if stop_after == "backup":
        return
    assert run("record-set", "--unit", U, "--state", "writing", "--record", str(p.record), "--origin",
               "adopted", "--backup", name, "--backup-dir", str(p.backups)).returncode == 0
    if stop_after == "writing":
        return
    assert run("write", "--unit", U, "--file", str(p.file), "--render", str(p.render)).returncode == 0
    if stop_after == "write":
        return
    assert run("record-set", "--unit", U, "--state", "written", "--record", str(p.record)).returncode == 0


def test_a_failure_after_the_backup_needs_adopt_again_and_backs_up_again(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "backup")
    assert p.plan().returncode == 1
    assert p.plan("--adopt").stdout == "adopt\n"


def test_a_failure_after_writing_resumes_with_the_same_backup_and_no_adopt(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "writing")
    entry = p.entry()
    assert entry and entry["state"] == "writing" and entry["origin"] == "adopted"
    assert p.plan().stdout == "resume\n"
    assert len(list(p.backups.iterdir())) == 1


def test_a_failure_after_the_write_marks_it_written(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "write")
    assert p.plan().stdout == "mark-written\n"


def test_a_hand_made_file_changed_since_its_backup_refuses(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "writing")
    p.file.write_text(HAND + "changed\n")
    out = p.plan()
    assert out.returncode == 1 and "changed since its backup" in out.stderr


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_a_missing_or_changed_backup_refuses(p: Paths, damage: str) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "written")
    [b] = list(p.backups.iterdir())
    if damage == "missing":
        b.unlink()
    else:
        b.write_text("other")
    out = p.plan()
    assert out.returncode == 1 and "backup" in out.stderr


def test_written_and_in_place_is_ok_and_a_new_render_rewrites(p: Paths) -> None:
    adopt_through_created(p)
    assert p.plan().stdout == "ok\n"
    p.render.write_text(MARKED + "rev 3\n")
    assert p.plan().stdout == "rewrite\n"


def adopt_through_created(p: Paths) -> None:
    run("record-set", "--unit", U, "--state", "writing", "--record", str(p.record), "--origin", "created")
    run("write", "--unit", U, "--file", str(p.file), "--render", str(p.render))
    run("record-set", "--unit", U, "--state", "written", "--record", str(p.record))


def test_an_upgrade_keeps_the_adopted_origin_and_backup(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "written")
    before = p.entry()
    run("record-set", "--unit", U, "--state", "writing", "--record", str(p.record), "--origin", "created")
    after = p.entry()
    assert before and after and (after["origin"], after["backup"]) == ("adopted", before["backup"])


def test_a_written_entry_whose_file_became_hand_made_refuses(p: Paths) -> None:
    adopt_through_created(p)
    p.file.write_text(HAND)
    assert p.plan("--adopt").returncode == 1


def test_write_is_atomic_and_0644(p: Paths) -> None:
    assert run("write", "--unit", U, "--file", str(p.file), "--render", str(p.render)).returncode == 0
    assert p.file.read_text() == p.render.read_text() and p.file.stat().st_mode & 0o777 == 0o644
    assert sorted(x.name for x in p.units.iterdir()) == [U]


def test_the_backup_is_0600_and_its_sum_is_recorded_never_printed(p: Paths) -> None:
    p.file.write_text(HAND)
    name = run("backup", "--unit", U, "--file", str(p.file), "--backup-dir", str(p.backups)).stdout.strip()
    assert (p.backups / name).stat().st_mode & 0o777 == 0o600
    out = run("record-set", "--unit", U, "--state", "writing", "--record", str(p.record), "--origin",
              "adopted", "--backup", name, "--backup-dir", str(p.backups))
    digest = hashlib.sha256(HAND.encode()).hexdigest()
    entry = p.entry()
    assert entry and entry["backup_sha256"] == digest and digest not in out.stdout + out.stderr


def test_release_and_drop(p: Paths) -> None:
    adopt_through_created(p)
    assert run("record-release", "--record", str(p.record), "--release", C).returncode == 0
    assert json.loads(p.record.read_text())["release"] == C
    assert run("record-drop", "--unit", U, "--record", str(p.record)).returncode == 0
    assert p.entry() is None


def test_the_marker_is_the_renderers() -> None:
    from heterodyne import units
    assert P.MARKER_PREFIX == units.MARKER_PREFIX.encode() and MARKED.startswith(units.MARKER_PREFIX)


@pytest.mark.parametrize("name", ["../record.json", "x/y", f"{W_NAME}.20261014T000000Z", ""])
def test_a_record_whose_backup_is_not_this_units_file_in_the_backup_dir_refuses(p: Paths, name: str) -> None:
    p.install.mkdir()
    p.record.write_text(json.dumps({"version": 1, "release": None, "units": {U: {
        "state": "written", "origin": "adopted", "backup": name, "backup_sha256": "0" * 64}}}))
    with pytest.raises(P.RecordError):
        P.load_record(p.record)


def test_a_release_that_is_not_a_full_commit_refuses(p: Paths) -> None:
    out = run("record-release", "--record", str(p.record), "--release", "HEAD")
    assert out.returncode != 0 and not p.record.exists()


def test_an_unmanaged_unit_name_is_a_usage_error(p: Paths) -> None:
    out = run("record-drop", "--unit", "heterodyne-admind-selftest.service", "--record", str(p.record))
    assert out.returncode == 2 and not p.record.exists()


def test_the_script_runs_isolated_and_reads_no_environment(p: Paths) -> None:
    out = subprocess.run([sys.executable, "-I", "-c", SCRIPT.read_text(), "plan", "--unit", U, "--record",
                          str(p.record), "--file", str(p.file), "--render", str(p.render), "--backup-dir",
                          str(p.backups)], capture_output=True, text=True, timeout=60, check=False,
                         env={"PATH": "/usr/bin:/bin"})
    assert (out.returncode, out.stdout) == (0, "create\n")


# --- identity (D4) ---

RENDER = (MARKED + "[Service]\nExecStart=/r/.venv/bin/python3 -m heterodyne.admind run\n"
          "Environment=PATH=/r/.venv/bin:/usr/bin\nEnvironment=HETERODYNE_CONFIG_DIR=/h/c\n"
          "Environment=HETERODYNE_STATE_DIR=/h/s\nEnvironment=HETERODYNE_RELEASE=" + C + "\n"
          "Environment=HETERODYNE_UNIT_REV=0123456789ab\n"
          "UnsetEnvironment=BTQ_REPO XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME\n")
ENV = ("PATH=/r/.venv/bin:/usr/bin HETERODYNE_CONFIG_DIR=/h/c HETERODYNE_STATE_DIR=/h/s "
       f"HETERODYNE_RELEASE={C} HETERODYNE_UNIT_REV=0123456789ab")


def show(fragment: str = "/u/heterodyne-admind.service",
         argv: str = "/r/.venv/bin/python3 -m heterodyne.admind run",
         env: str = ENV, dropins: str = "") -> str:
    return (f"FragmentPath={fragment}\nDropInPaths={dropins}\n"
            f"ExecStart={{ path=/r/.venv/bin/python3 ; argv[]={argv} ; ignore_errors=no ; start_time=[n/a] ; "
            "stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }\n"
            f"Environment={env}\nUnsetEnvironment=BTQ_REPO XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME\n"
            "NeedDaemonReload=no\n")


def test_a_loaded_unit_that_matches_has_no_diff() -> None:
    assert P.loaded_diff(show(), P.expected(RENDER), "/u/heterodyne-admind.service") == []


@pytest.mark.parametrize(("kw", "prop"), [
    ({"fragment": "/elsewhere/heterodyne-admind.service"}, "FragmentPath"),
    ({"argv": "/r/.venv/bin/python3 -m heterodyne.other run"}, "ExecStart"),
    ({"env": ENV.replace("/usr/bin", "/opt/bin")}, "Environment"),
    ({"env": ENV + " BTQ_REPO=/x"}, "Environment"),
])
def test_a_loaded_unit_that_differs_names_the_property(kw: dict[str, str], prop: str) -> None:
    diff = P.loaded_diff(show(**kw), P.expected(RENDER), "/u/heterodyne-admind.service")
    assert prop in diff


def test_a_second_exec_start_differs() -> None:
    two = show().replace("NeedDaemonReload",
                         "ExecStart={ path=/bin/true ; argv[]=/bin/true ; }\nNeedDaemonReload")
    assert "ExecStart" in P.loaded_diff(two, P.expected(RENDER), "/u/heterodyne-admind.service")


def test_a_drop_in_is_named_when_the_unit_differs() -> None:
    diff = P.loaded_diff(show(argv="/x -m y", dropins="/u/heterodyne-admind.service.d/10-x.conf"),
                         P.expected(RENDER), "/u/heterodyne-admind.service")
    assert "drop-in 10-x.conf" in diff


def proc(tmp: Path, argv: list[str], env: dict[str, str]) -> Path:
    d = tmp / "proc" / "41"
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "environ").write_bytes(b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items()))
    return tmp / "proc"


GOOD_ENV = ({k: v for k, _, v in (e.partition("=") for e in ENV.split())}
            | {"HOME": "/h", "SSH_AUTH_SOCK": "/s"})
GOOD_ARGV = ["/r/.venv/bin/python3", "-m", "heterodyne.admind", "run"]


def test_a_matching_process_has_no_diff(tmp_path: Path) -> None:
    assert P.process_diff(proc(tmp_path, GOOD_ARGV, GOOD_ENV), 41, P.expected(RENDER)) == []


@pytest.mark.parametrize(("argv", "env", "line"), [
    (GOOD_ARGV[:3] + ["other"], GOOD_ENV, "argv"),
    (GOOD_ARGV, GOOD_ENV | {"HETERODYNE_UNIT_REV": "ffffffffffff"}, "HETERODYNE_UNIT_REV"),
    (GOOD_ARGV, GOOD_ENV | {"HETERODYNE_STATE_DIR": "/elsewhere"}, "HETERODYNE_STATE_DIR"),
    (GOOD_ARGV, GOOD_ENV | {"BTQ_REPO": "/b"}, "BTQ_REPO"),
    (GOOD_ARGV, {k: v for k, v in GOOD_ENV.items() if k != "HETERODYNE_RELEASE"}, "HETERODYNE_RELEASE"),
])
def test_a_process_that_differs_names_the_key_not_the_value(tmp_path: Path, argv: list[str],
                                                            env: dict[str, str], line: str) -> None:
    diff = P.process_diff(proc(tmp_path, argv, env), 41, P.expected(RENDER))
    assert diff == [line]


def test_an_unreadable_process_is_not_a_match(tmp_path: Path) -> None:
    assert P.process_diff(tmp_path / "proc", 41, P.expected(RENDER)) == ["unreadable"]


def test_process_diff_prints_no_value(tmp_path: Path) -> None:
    root = proc(tmp_path, GOOD_ARGV, GOOD_ENV | {"HETERODYNE_STATE_DIR": "/secret-ish"})
    (tmp_path / "render").write_text(RENDER)
    out = run("process-diff", "--render", str(tmp_path / "render"), "--proc", str(root), "--pid", "41")
    assert out.stdout == "HETERODYNE_STATE_DIR\n" and "/secret-ish" not in out.stdout + out.stderr


def test_a_drop_in_that_changes_nothing_compared_is_no_diff() -> None:
    shown = show(dropins="/u/heterodyne-admind.service.d/10-nice.conf")
    assert P.loaded_diff(shown, P.expected(RENDER), "/u/heterodyne-admind.service") == []


def test_the_expected_identity_unescapes_percent() -> None:
    exp = P.expected(RENDER.replace("/r/.venv/bin:/usr/bin", "/r/.venv/bin:/opt/50%%/bin"))
    assert exp.env["PATH"] == "/r/.venv/bin:/opt/50%/bin" and exp.argv == GOOD_ARGV
    assert exp.unset == ["BTQ_REPO", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"]


def test_a_render_without_exec_start_refuses() -> None:
    with pytest.raises(P.Refusal):
        P.expected(MARKED)
