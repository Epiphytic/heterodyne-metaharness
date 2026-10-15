"""deploy/platform_state.py (design btq-v7sgt D6): the install record, and the order around every unit
write, so that a failure at any point leaves the intent on disk before the change it describes."""

import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
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


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", "-c", SCRIPT.read_text(), *args], input=stdin,
                          capture_output=True, text=True, timeout=60, check=False)


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


def test_record_show_names_the_release_and_each_unit_without_a_digest(p: Paths) -> None:
    out = run("record-show", "--record", str(p.record))
    assert (out.returncode, out.stdout) == (0, "release -\n")
    adopt_through_created(p)
    assert run("record-release", "--record", str(p.record), "--release", C).returncode == 0
    out = run("record-show", "--record", str(p.record))
    assert out.stdout == f"release {C}\nunit {U} written created\n"


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


# --- uninstall (D6) ---

W = W_NAME


def blocks(**states: tuple[str, str, str]) -> str:
    """systemctl show output for both units: (LoadState, ActiveState, FragmentPath)."""
    out = []
    for unit in (W, U):
        load, active, frag = states.get(unit.split("-")[1].split(".")[0], ("not-found", "inactive", ""))
        out.append(f"Id={unit}\nLoadState={load}\nActiveState={active}\nFragmentPath={frag}\n")
    return "\n".join(out)


def classify(p: Paths, show_text: str, restore: bool = False) -> dict[str, tuple[str, str]]:
    rows = P.classify(P.load_record(p.record), p.units, p.backups, show_text, restore)
    return {u: (c, why) for u, c, why in rows}


def test_nothing_installed_is_absent(p: Paths) -> None:
    assert classify(p, blocks()) == {W: ("absent", ""), U: ("absent", "")}


def test_a_written_marker_unit_is_managed(p: Paths) -> None:
    adopt_through_created(p)
    got = classify(p, blocks(admind=("loaded", "active", str(p.file))))
    assert got[U] == ("managed", "")


def test_a_hand_made_unit_never_adopted_is_not_managed(p: Paths) -> None:
    p.file.write_text(HAND)
    assert classify(p, blocks(admind=("loaded", "active", str(p.file))))[U] == ("not-managed", "")


@pytest.mark.parametrize("case", ["marker-no-entry", "entry-no-marker", "writing", "fragment-elsewhere",
                                  "loaded-no-file", "symlink"])
def test_ambiguous_cases(p: Paths, tmp_path: Path, case: str) -> None:
    show_text = blocks(admind=("loaded", "active", str(p.file)))
    if case == "marker-no-entry":
        p.file.write_text(MARKED)
    elif case == "entry-no-marker":
        adopt_through_created(p)
        p.file.write_text(HAND)
    elif case == "writing":
        run("record-set", "--unit", U, "--state", "writing", "--record", str(p.record), "--origin", "created")
        p.file.write_text(MARKED)
    elif case == "fragment-elsewhere":
        adopt_through_created(p)
        show_text = blocks(admind=("loaded", "active", "/elsewhere/x.service"))
    elif case == "loaded-no-file":
        pass
    elif case == "symlink":
        p.file.symlink_to(tmp_path / "render")
    assert classify(p, show_text)[U][0] == "ambiguous"


def test_restore_needs_a_good_backup(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "written")
    show_text = blocks(admind=("loaded", "active", str(p.file)))
    assert classify(p, show_text, restore=True)[U] == ("managed", "")
    [b] = list(p.backups.iterdir())
    b.write_text("other")
    assert classify(p, show_text, restore=True)[U][0] == "ambiguous"
    assert classify(p, show_text, restore=False)[U] == ("managed", "")


def test_classify_exits_1_when_anything_is_ambiguous(p: Paths) -> None:
    p.file.write_text(MARKED)
    out = run("classify", "--record", str(p.record), "--unit-dir", str(p.units), "--backup-dir",
              str(p.backups),
              stdin=blocks(admind=("loaded", "active", str(p.file))))
    assert out.returncode == 1 and out.stdout.splitlines()[0] == f"{W} absent"
    assert out.stdout.splitlines()[1].startswith(f"{U} ambiguous ")


def test_restore_puts_the_backup_back_0644(p: Paths) -> None:
    p.file.write_text(HAND)
    adopt_through(p, "written")
    assert run("restore", "--unit", U, "--record", str(p.record), "--unit-dir", str(p.units), "--backup-dir",
               str(p.backups)).returncode == 0
    assert p.file.read_text() == HAND and p.file.stat().st_mode & 0o777 == 0o644



def test_a_malformed_record_makes_classify_refuse(p: Paths) -> None:
    p.install.mkdir()
    p.record.write_text("{")
    out = run("classify", "--record", str(p.record), "--unit-dir", str(p.units), "--backup-dir",
              str(p.backups),
              stdin=blocks())
    assert out.returncode == 1 and out.stdout == "" and "install record" in out.stderr


def test_a_unit_systemd_did_not_report_is_ambiguous(p: Paths) -> None:
    only_wsd = blocks().split("\n\n")[0]
    assert classify(p, only_wsd)[U] == ("ambiguous", "systemd's state can't be read")


def test_a_not_found_but_active_unit_with_no_file_is_ambiguous(p: Paths) -> None:
    assert classify(p, blocks(admind=("not-found", "active", "")))[U][0] == "ambiguous"


def test_restore_refuses_without_a_verified_backup(p: Paths) -> None:
    adopt_through_created(p)
    out = run("restore", "--unit", U, "--record", str(p.record), "--unit-dir", str(p.units), "--backup-dir",
              str(p.backups))
    assert out.returncode == 1 and "no verified backup" in out.stderr
    assert p.file.read_text() == p.render.read_text()


def test_classify_compares_the_fragment_with_the_unit_dir_systemd_names(p: Paths) -> None:
    adopt_through_created(p)
    out = run("classify", "--record", str(p.record), "--unit-dir", str(p.units), "--backup-dir",
              str(p.backups), "--loaded-unit-dir", "/srv/u/.config/systemd/user",
              stdin=blocks(admind=("loaded", "active", f"/srv/u/.config/systemd/user/{U}")))
    assert (out.returncode, out.stdout.splitlines()[1]) == (0, f"{U} managed")


def test_wsd_lock_absent_free_held_and_refused(tmp_path: Path) -> None:
    lock = tmp_path / "wsd" / "wsd.lock"
    assert P.wsd_lock(lock) == "absent"
    lock.parent.mkdir()
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert P.wsd_lock(lock) == "free"
        fcntl.flock(fd, fcntl.LOCK_EX)      # another open file description, as another process holds it
        assert P.wsd_lock(lock) == "held"
    finally:
        os.close(fd)
    assert P.wsd_lock(lock) == "free"
    link = tmp_path / "wsd" / "link.lock"
    link.symlink_to(lock)
    with pytest.raises(P.Refusal):
        P.wsd_lock(link)
    (tmp_path / "wsd" / "dir.lock").mkdir()
    with pytest.raises(P.Refusal):
        P.wsd_lock(tmp_path / "wsd" / "dir.lock")


def test_user_dir_makes_checks_and_reports_what_it_made(tmp_path: Path) -> None:
    d = tmp_path / "a" / "b" / "user"
    assert P.user_dir(d, "units", make=False) is None and not d.exists()
    assert P.user_dir(d, "units", make=True) == tmp_path / "a"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700 and P.user_dir(d, "units", make=True) is None
    d.chmod(0o775)
    with pytest.raises(P.Refusal, match="can be written by others"):
        P.user_dir(d, "units", make=False)
    assert P.user_dir(d, "private", make=False) is None      # private dirs are narrowed when written
    d.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(d)
    for kind in ("units", "private"):
        with pytest.raises(P.Refusal, match="not the service user's directory"):
            P.user_dir(link, kind, make=False)


def test_the_scratch_dir_is_walked_from_the_home_and_never_made_through_a_link(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    d = home / ".cache" / "heterodyne"
    assert P.user_dir(d, "scratch", make=False, home=home) is None and not d.exists()
    assert P.user_dir(d, "scratch", make=True, home=home) == home / ".cache"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    d.chmod(0o755)
    with pytest.raises(P.Refusal, match="can be read by others"):
        P.user_dir(d, "scratch", make=True, home=home)
    assert stat.S_IMODE(d.stat().st_mode) == 0o755          # refused, never narrowed
    shutil.rmtree(home / ".cache")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / ".cache").symlink_to(elsewhere)
    for make in (False, True):
        with pytest.raises(P.Refusal, match="not the service user's directory"):
            P.user_dir(d, "scratch", make=make, home=home)
    assert list(elsewhere.iterdir()) == []
    with pytest.raises(P.Refusal, match="not under the service user's home"):
        P.user_dir(tmp_path / "other", "scratch", make=True, home=home)


@pytest.mark.parametrize("writable", ["home", ".cache"])
def test_a_scratch_ancestor_others_can_write_refuses_before_anything_is_made_under_it(tmp_path: Path,
                                                                                    writable: str) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    level = home if writable == "home" else home / ".cache"
    level.mkdir(mode=0o700, exist_ok=True)
    level.chmod(0o777)
    d = home / ".cache" / "heterodyne"
    for make in (False, True):
        with pytest.raises(P.Refusal, match="is under a directory others can write"):
            P.user_dir(d, "scratch", make=make, home=home)
    assert not d.exists() and (writable == ".cache" or not (home / ".cache").exists())
