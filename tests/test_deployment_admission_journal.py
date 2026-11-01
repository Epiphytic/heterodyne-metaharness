"""Actual root storage constraints/reopen; synthetic rows confer no consent."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne.actions import deployment_admission_journal as ledger
from heterodyne.actions.codec import InvalidAction


def test_root_admission_ledger_refuses_nonroot():
    if os.geteuid() == 0:
        pytest.skip('nonroot refusal requires unprivileged process')
    with pytest.raises(InvalidAction, match='admission-journal-root-required'):
        ledger._AdmissionJournal()


_SCRIPT = r'''
import os,sqlite3
from pathlib import Path
from heterodyne.actions import deployment_admission_journal as m
from heterodyne.actions.codec import InvalidAction
root=Path('/tmp/root-ledger');root.mkdir(mode=0o700);m._ROOT=root
journal=m._AdmissionJournal()
try:
    journal.admit(object())
    raise AssertionError('unowned channel accepted')
except InvalidAction as e:
    assert str(e)=='admission-owned-parent-required'
# Rows are deliberately synthetic and inserted only to test actual SQL schema.
# No native consent, entry, permit or effect is invoked by this storage test.
row=('p','e','a','n','u','target',b'original',b'not-consent')
with journal._transaction():
    journal.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,'ready')",row)
for index in range(6):
    other=['p2','e2','a2','n2','u2','other-target',b'original2',b'not-consent2']
    other[index]=row[index]
    try:
        with journal._transaction():
            journal.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,'ready')",other)
        raise AssertionError(('uniqueness missing',index))
    except InvalidAction as e:
        assert str(e)=='admission-one-use-or-target-conflict'
journal.close()
journal=m._AdmissionJournal()
assert journal.db.execute('SELECT packet,phase FROM admissions').fetchall()==[('p','ready')]
# A transient real competing writer must not discard an already-consumed handoff.
import threading
held=sqlite3.connect(root/'admission.db',check_same_thread=False)
held.execute('BEGIN IMMEDIATE')
timer=threading.Timer(.1,held.commit);timer.start()
with journal._transaction():
    assert journal.db.execute('SELECT count(*) FROM admissions').fetchone()==(1,)
timer.join();held.close()

# A settled genuinely no-effect job may release the target while retaining ALL
# its unique approval/execution/nonce/use identifiers across reopen.
with journal._transaction():
    journal.db.execute("UPDATE admissions SET phase='failed-no-effect' WHERE packet='p'")
    journal.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,'ready')",
                       ('p2','e2','a2','n2','u2','target',b'o',b'c'))
assert journal.db.execute('SELECT COUNT(*) FROM admissions').fetchone()==(2,)
# Recovery retains every key and never clears an actual issued event.
assert journal.recover_never_issued()==('p2',)
assert journal.recover_never_issued()==()
with journal._transaction():
    journal.db.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,'ready')",
                       ('p3','e3','a3','n3','u3','target',b'o',b'c'))
    journal.db.execute("INSERT INTO admission_events VALUES('p3',0,'issued','hash')")
assert journal.recover_never_issued()==()
journal.close()
# sqlite_ is a literal reserved prefix, not a LIKE wildcard.
db=sqlite3.connect(root/'admission.db')
db.execute("CREATE TRIGGER sqlitextrg AFTER INSERT ON admissions BEGIN DELETE FROM admissions; END")
db.close()
try:
    m._AdmissionJournal()
    raise AssertionError('hidden trigger accepted')
except InvalidAction as e:
    assert str(e)=='admission-journal-schema'
db=sqlite3.connect(root/'admission.db');db.execute('DROP TRIGGER sqlitextrg');db.close()
# An authentic file with a weakened partial index is not an authentic schema.
db=sqlite3.connect(root/'admission.db')
db.execute('DROP INDEX admission_active_target')
db.execute("CREATE UNIQUE INDEX admission_active_target ON admissions(target) WHERE phase='ready'")
before_schema=db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()
db.close()
try:
    m._AdmissionJournal()
    raise AssertionError('weakened schema accepted')
except InvalidAction as e:
    assert str(e)=='admission-journal-schema'
db=sqlite3.connect(root/'admission.db')
assert db.execute('SELECT type,name,sql FROM sqlite_master ORDER BY type,name').fetchall()==before_schema
db.close()
(root/'admission.db').chmod(0o640)
try:
    m._AdmissionJournal()
    raise AssertionError('foreign-readable database accepted')
except InvalidAction as e:
    assert str(e)=='admission-journal-custody'
print('actual-root-global-journal-storage-pass')
'''


def test_actual_root_global_storage_oneuse_target_and_reopen():
    script = (
        "import subprocess\n"
        "subprocess.run(['mount','-t','tmpfs','-o','mode=755','tmpfs','/tmp'],check=True)\n"
        + _SCRIPT
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((
        str(Path.cwd()/'src'), str(Path(sys.prefix)/'lib/python3.12/site-packages'),
    )))
    result = subprocess.run(
        ['unshare', '--user', '--map-auto', '--map-root-user', '--mount', sys.executable, '-c', script],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'actual-root-global-journal-storage-pass' in result.stdout
