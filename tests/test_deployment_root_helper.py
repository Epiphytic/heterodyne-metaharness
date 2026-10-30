"""Real namespace custody/effect probes; no installed factory or real permit claim."""

import hashlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne.actions import deployment_root_helper as helper
from heterodyne.actions.codec import InvalidAction


def test_nonroot_and_environment_alone_cannot_authorize(monkeypatch):
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    with pytest.raises(InvalidAction, match="sudo-identity"):
        helper._sudo_parent(os.getuid(), "a" * 64)
    with pytest.raises(InvalidAction, match="root-required"):
        helper._RootInstaller(Path.cwd())


def test_process_identity_ignores_cpu_but_retains_start():
    fields = [b"0"] * 21
    fields[19] = b"1234"
    raw = b"41 (a name) with brackets) " + b" ".join(fields)
    assert helper._process_identity(raw) == (b"41", b"1234")
    changed = raw.replace(b"1234", b"1235")
    assert helper._process_identity(changed) != helper._process_identity(raw)


def test_bad_digest_cannot_select_files():
    instance = object.__new__(helper._RootInstaller)
    for name in ("../profile.json", "a" * 63, "A" * 64, "a" * 64 + "\n"):
        with pytest.raises(InvalidAction, match="request-digest"):
            instance._retained(name)


_PROBE = r'''
import hashlib, json, os, shutil, subprocess
from pathlib import Path
import msgspec
from test_deployment_outage import packet
from test_deployment_step1 import TARGET
from heterodyne.actions import deployment_root_helper as h
from heterodyne.actions.codec import InvalidAction, encode_record
from heterodyne.actions.deployment import digest
from heterodyne.actions.deployment_outage import packet_digest
subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", "/tmp"], check=True)
subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", "/run"], check=True)
base = Path("/tmp/helper-probe"); base.mkdir(mode=0o755)
store = base / "store"; store.mkdir(mode=0o700)
marker = base / "marker"
# Genuine root UID in this disposable namespace, fixture source/permit only.
# Sudo topology is not claimed by this mechanics test.
h._sudo_parent = lambda uid, pin: None
h.time.time_ns = lambda: 10
bash = base / "bash"; shutil.copyfile("/usr/bin/bash", bash); bash.chmod(0o500)
launcher = base / "helper"; launcher.write_bytes(b"#!/bin/sh\nexit 1\n"); launcher.chmod(0o500)
h._BASH, h._HELPER = bash, launcher
sha = lambda raw: hashlib.sha256(raw).hexdigest()
profile = h._Profile("heterodyne-root-install-profile.v1", 1, TARGET, "/fixed/checkout",
                     "a" * 64, sha(bash.read_bytes()), sha(launcher.read_bytes()))
profile_raw = encode_record(profile)
script = b'#!/bin/bash\nprintf "%s\\n" "$@" > /tmp/helper-probe/marker\nexit 7\n'
p = packet()
r = msgspec.structs.replace(p.release, artifacts=msgspec.structs.replace(
    p.release.artifacts, installer_sha256=sha(script), bootstrap_sha256=sha(script)))
p = msgspec.structs.replace(p, release=r, root_profile_sha256=sha(profile_raw),
                           root_helper_sha256=profile.helper_sha256, sudo_sha256=profile.sudo_sha256,
                           outage=msgspec.structs.replace(p.outage, release_sha256=digest(r)))
key = packet_digest(p)
permit = h._Permit("heterodyne-root-install-permit.v1", key, encode_record(p).decode(),
                  2, p.outage.transitions[2].before_sha256, 89)
def put(name, raw):
    file = store / name; file.write_bytes(raw); file.chmod(0o400)
put("profile.json", profile_raw)
put(key + ".permit", encode_record(permit))
put("installer-" + sha(script), script)
root = h._RootInstaller(store)
# Kernel credentials, not mocked access() or a caller boolean: a separate UID
# cannot read, replace or add a permit/issued record in the protected root.
pid = os.fork()
if pid == 0:
    os.setgroups([]); os.setgid(1); os.setuid(1)
    denied = 0
    for operation in (
        lambda: (store / "profile.json").read_bytes(),
        lambda: (store / (key + ".permit")).write_bytes(b"forged"),
        lambda: (store / "forged.permit").write_bytes(b"forged"),
        lambda: h._RootInstaller(store),
    ):
        try: operation()
        except (PermissionError, InvalidAction): denied += 1
    os._exit(0 if denied == 4 else 70)
assert os.waitpid(pid, 0)[1] == 0
# Malformed preflight cannot reserve/issue an effect.
for wrong in (msgspec.structs.replace(permit, ordinal=1),
              msgspec.structs.replace(permit, deadline_ns=5),
              msgspec.structs.replace(permit, packet_sha256="b" * 64),
              msgspec.structs.replace(permit, expected_before_sha256="b" * 64)):
    put(key + ".permit", encode_record(wrong))
    try: root.run(key)
    except InvalidAction as e: assert "permit-binding" in str(e)
    else: raise AssertionError("malformed admission accepted")
    assert not (store / (key + ".issued")).exists() and not marker.exists()
put(key + ".permit", encode_record(permit))
put("installer-" + sha(script), b"tamper")
try: root.run(key)
except InvalidAction as e: assert "installer-drift" in str(e)
else: raise AssertionError("tampered installer accepted")
assert not (store / (key + ".issued")).exists() and not marker.exists()
put("installer-" + sha(script), script)
# Installer bytes are retained by private fd, fixed argv, completely scrubbed env.
assert root.run(key) == 7
args = marker.read_text().splitlines()
assert args == ["--no-wsd", "--no-verity", "--service-user", TARGET.service_user,
                "--checkout", "/fixed/checkout", "--commit", r.artifacts.source_commit,
                "--expect-sha256", r.artifacts.release_sha256]
issued = json.loads((store / (key + ".issued")).read_bytes())
assert issued["outcome"] == "unknown" and issued["packet_sha256"] == key
marker.unlink()
try: root.run(key)
except InvalidAction as e: assert "already-issued-unknown" in str(e)
else: raise AssertionError("uncertain effect reissued")
assert not marker.exists()
# Post-intent process-launch exceptions are equally retained UNKNOWN.
(store / (key + ".issued")).unlink()  # root-only fixture RESET, never runtime recovery
original_run = h.subprocess.run
def fail(*args, **kwargs): raise OSError("synthetic launch failure")
h.subprocess.run = fail
try:
    try: root.run(key)
    except OSError: pass
    else: raise AssertionError("synthetic launch failure missing")
finally: h.subprocess.run = original_run
assert (store / (key + ".issued")).exists()
try: root.run(key)
except InvalidAction as e: assert "already-issued-unknown" in str(e)
else: raise AssertionError("launch failure retried")
root.close()
print(json.dumps({"namespace_uid": os.getuid(), "kernel_denials": 4,
                  "fixed_effect": True, "unknown_retained": True}))
'''


def test_actual_namespace_root_custody_oneuse_and_uncertainty():
    env = dict(os.environ, PYTHONPATH=str(Path.cwd() / "src") + ":" + str(Path.cwd() / "tests"),
               PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", sys.executable,
         "-c", _PROBE], capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"kernel_denials": 4' in result.stdout


def test_actual_sudo_monitor_provenance_readonly():
    # Execute ONLY the inspected read-only provenance functions under the actual
    # system sudo/python. No Product imports or helper/installer effects as root.
    # This qualifies this host topology, not enrolled code/package custody.
    source = (
        "import contextlib, hashlib, os, re, stat\nfrom pathlib import Path\n"
        "InvalidAction = ValueError\n_SUDO = Path('/usr/bin/sudo')\n"
        "_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC\n"
        "_HASH = re.compile(r'[0-9a-f]{64}')\n"
        + inspect.getsource(helper._stat_key) + "\n"
        + inspect.getsource(helper._binary) + "\n"
        + inspect.getsource(helper._process_identity) + "\n"
        + inspect.getsource(helper._sudo_parent) + "\n"
        + f"_sudo_parent({os.getuid()}, {hashlib.sha256(Path('/usr/bin/sudo').read_bytes()).hexdigest()!r})\n"
        + "os.environ['SUDO_UID'] = '123456789'\n"
        + f"try: _sudo_parent({os.getuid()}, 'a' * 64)\n"
        + "except ValueError: print('actual-sudo-monitor-pass-and-forged-env-refused')\n"
        + "else: raise AssertionError('forged env accepted')\n"
    )
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-S", "-c", source],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "actual-sudo-monitor-pass-and-forged-env-refused"
