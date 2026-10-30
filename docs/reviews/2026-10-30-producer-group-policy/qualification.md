# Producer group policy repair after the third review

The actual third review of PR149 cleared PoC source and retained the group-width mismatch as a pre-enrollment finding. The current Marmot group uses 32 hexadecimal characters. The producer previously required exactly 64 while the existing authority binder accepts 32–128. Share the binder's existing lower-case hexadecimal regular expression, retaining exact 64-character SHA256 validation independently. Actual isolated child tests now prove all three widths 32/64/128 produce output that passes exact correlation; malformed widths, uppercase and nonhex values refuse before publication.

Also add O_NOCTTY to root's no-follow/nonblocking file opens, as requested in the same report. This prevents acquiring a controlling terminal; it is not a claim to remove every possible device-open side effect. Regular-file, ownership and inode custody checks remain, and protected production owning-factory enrollment stays HOLD.

140 producer/publication/card/cessation/root-helper/authority tests passed in 15.04s. Ruff and strict Pyright passed. No live configuration, queue, service or deployment changed. The review is an authorized repair follow-up using MEDIUM and the operator's exact PoC question; prior PR149 reports and review budget are preserved, not reset.

The first actual MEDIUM follow-up found two missed O_NOCTTY open sites despite PoC-level clearance. Its conditional findings are preserved in full; both the root-helper constant and the cgroup.events reader now include the flag. A second authorized MEDIUM repair review addresses that concrete incomplete repair. No prior verdict is rewritten.

Final repaired source qualification: ........................................................................ [ 71%]
.............F...............                                            [100%]
=================================== FAILURES ===================================
_______ test_real_cgroup_populated_empty_and_removed_retained_descriptor _______

    def test_real_cgroup_populated_empty_and_removed_retained_descriptor():
        # Only the inspected descriptor observer runs as root, never Product imports
        # or host service operations. One unique disposable cgroup owns one harmless
        # fixture child, then both are removed. No existing task is reassigned.
        import inspect
        source = (
            "import os, pathlib, subprocess, signal, uuid, time, json\nfrom pathlib import Path\n"
            "InvalidAction = ValueError\nclass Probe:\n"
            + inspect.getsource(cessation._AdmindInvocation._empty_group)
            + '''
    p = pathlib.Path('/sys/fs/cgroup') / ('heterodyne-proof-' + uuid.uuid4().hex)
    p.mkdir()
    child = None
    fd = -1
    try:
        fd = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        observer = Probe(); observer.group_fd = fd; observer.before = {'ControlGroup': '/' + p.name}
        child = subprocess.Popen(['/usr/bin/python3', '-I', '-S', '-c', 'import time; time.sleep(30)'])
        (p / 'cgroup.procs').write_text(str(child.pid))
        try: observer._empty_group()
        except ValueError as error: assert 'populated' in str(error)
        else: raise AssertionError('live cgroup accepted')
        child.kill(); child.wait(timeout=3)
        deadline = time.monotonic() + 3
        while 'populated 0' not in (p / 'cgroup.events').read_text():
            assert time.monotonic() < deadline
            time.sleep(.01)
        observer._empty_group()
        p.rmdir()
        assert os.fstat(fd).st_nlink == 2
        observer._empty_group()
        print(json.dumps({'populated_refused': True, 'empty_passed': True,
                          'removed_retained_passed': True, 'removed_nlink': os.fstat(fd).st_nlink}))
    finally:
        if child is not None and child.poll() is None: child.kill(); child.wait(timeout=3)
        if fd >= 0: os.close(fd)
        if p.exists(): p.rmdir()
    '''
        )
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/python3", "-I", "-S", "-c", source],
            capture_output=True, text=True, timeout=15,
        )
>       assert result.returncode == 0, result.stderr + result.stdout
E       AssertionError: Traceback (most recent call last):
E           File "<string>", line 35, in <module>
E           File "<string>", line 8, in _empty_group
E         NameError: name '_READ_FILE' is not defined
E         
E       assert 1 == 0
E        +  where 1 = CompletedProcess(args=['/usr/bin/sudo', '-n', '/usr/bin/python3', '-I', '-S', '-c', 'import os, pathlib, subprocess, s...>", line 35, in <module>\n  File "<string>", line 8, in _empty_group\nNameError: name \'_READ_FILE\' is not defined\n').returncode

tests/test_deployment_cessation.py:233: AssertionError
=========================== short test summary info ============================
FAILED tests/test_deployment_cessation.py::test_real_cgroup_populated_empty_and_removed_retained_descriptor
1 failed, 100 passed in 14.16s Ruff and strict Pyright passed. The earlier mistyped nonexistent test path ran no tests and is not used as qualification.
