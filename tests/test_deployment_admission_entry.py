"""Mapped-root no-effect admission, not installed parent or native authority.

The ordinary fixture is synthetic. The same harness can qualify retained actual
native bytes externally, without changing their contents or claiming custody.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import msgspec
import pytest
from test_deployment_admission_binding import correlated, inputs, published  # noqa: F401

from heterodyne.actions import deployment_admission_binding as binding

_PROBE = r'''
import fcntl,hashlib,json,os,socket,sqlite3,sys
from pathlib import Path
from heterodyne.actions import deployment_admission_entry as entry
from heterodyne.actions import deployment_admission_journal as journal
from heterodyne.actions import deployment_admission_peer as peer
from heterodyne.actions.codec import encode_record
payload=json.load(sys.stdin)
raw={name:bytes.fromhex(value) for name,value in payload.items()}
consumed=json.loads(raw['consumed']);producer=json.loads(raw['producer'])
packet=json.loads(raw['packet'])
if 'synthetic_clock' in raw:
 journal.time.time_ns=lambda:int(raw['synthetic_clock'])
if 'late_parent_loss' in raw or 'late_oserror' in raw:

 original_check=peer._ParentChannel.check
 def fail_after_commit(self):
  original_check(self)
  dbpath=journal._ROOT/'admission.db'
  if dbpath.exists():
   db=sqlite3.connect(dbpath)
   try:exists=db.execute('SELECT count(*) FROM admissions').fetchone()[0]
   finally:db.close()
   if exists and attempt==0:
    if 'late_oserror' in raw:raise OSError('causal-postcommit-pidfd-fault')
    raise entry.InvalidAction('admission-parent-ceased')
 peer._ParentChannel.check=fail_after_commit
root=Path('/tmp/admission-entry-probe');root.mkdir(mode=0o700)
journal._ROOT=root/'journal';journal._ROOT.mkdir(mode=0o700)
binary='/tmp/admission-parent-python'
profile=peer._ParentProfile('heterodyne-deployment-parent-profile.v1',binary,
 hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
 consumed['installation_index_sha256'],consumed['installation_source_sha256'],
 packet['semantic_factory_source_sha256'],producer['packet_sha256'],
 hashlib.sha256(raw['producer']).hexdigest())
raw['parent']=encode_record(profile)
fds={}
for name in ('parent','process','producer','packet'):
 path=root/name;path.write_bytes(raw[name]);path.chmod(0o400)
 fds[name]=os.open(path,os.O_RDONLY|os.O_CLOEXEC)
lease=os.open(root/'launch.lock',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
admission_nonce=os.urandom(32)
assert admission_nonce.hex()!=consumed['nonce_hex']
nonce=os.memfd_create('admission-only-nonce',os.MFD_ALLOW_SEALING|os.MFD_CLOEXEC)
os.write(nonce,admission_nonce);os.fchmod(nonce,0o400)
fcntl.fcntl(nonce,fcntl.F_ADD_SEALS,peer._SEALS)
nonce_read=os.open('/proc/self/fd/'+str(nonce),os.O_RDONLY|os.O_CLOEXEC)
handoff=peer._Handoff('heterodyne-owned-deployment-admission.v1',admission_nonce.hex(),consumed['nonce_hex'],
 hashlib.sha256(raw['process']).hexdigest(),producer['packet_sha256'],
 hashlib.sha256(raw['producer']).hexdigest())
# Every invocation is a new process/channel over the SAME retained launch OFD.
# Duplicate admission is refused even though the first target is no longer active.
for attempt in range(3):
 parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
 child.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
 parent.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,peer._MAX_FRAME)
 parent.settimeout(10)
 source=(fds['parent'],child.fileno(),lease,nonce_read,fds['process'],fds['producer'],fds['packet'])
 holders=[fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,64) for fd in source]
 pid=os.fork()
 if pid==0:
  parent.close()
  for original,target in zip(holders,(3,4,5,6,7,13,18)):os.dup2(original,target)
  sys.argv=['owned-admission-no-effect']
  try:os._exit(entry._entry())
  except BaseException:
   import traceback;traceback.print_exc();os._exit(79)
 child.close()
 for fd in holders:os.close(fd)
 parent.send(encode_record(handoff));parent.send(raw['consumed'])
 try:reply=parent.recv(peer._MAX_FRAME)
 except ConnectionResetError:reply=b''
 parent.close()
 _,status=os.waitpid(pid,0)
 expected_success=attempt==0 and not any(k in raw for k in
  ('late_parent_loss','expired','late_oserror'))
 assert os.waitstatus_to_exitcode(status)==(0 if expected_success else 2),(attempt,status)
 if expected_success:
  assert json.loads(reply)=={'schema':'heterodyne-owned-deployment-admission-result.v1',
    'nonce_hex':admission_nonce.hex(),'status':'failed-no-effect'},reply
 else:assert reply==b'',(attempt,reply)
 # Independent SQLite reopen after child cessation: no effect issue/completion.
 db=sqlite3.connect(journal._ROOT/'admission.db')
 rows=db.execute('SELECT phase,original,consumed FROM admissions').fetchall()
 assert rows==[('failed-no-effect',raw['packet'],raw['consumed'])],rows
 assert db.execute('SELECT count(*) FROM admission_events').fetchone()==(0,)
 assert db.execute('PRAGMA integrity_check').fetchone()==('ok',)
 db.close()
 if 'expired' in raw:
  journal.time.time_ns=lambda:packet['outage']['not_before_ns']+1
 other=os.open(root/'launch.lock',os.O_RDWR)
 try:
  try:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:pass
  else:raise AssertionError('original parent lifetime lock lost')
 finally:os.close(other)
assert sorted(p.name for p in journal._ROOT.iterdir())==['admission.db']
print('actual-mapped-root-entry-failed-no-effect-three-process-one-use-pass')
'''


def qualify_payload(payload: dict[str, bytes]) -> subprocess.CompletedProcess:
    """Disposable kernel mechanics only; payload is test data, never authority."""
    prelude = (
        "import os,shutil,subprocess\n"
        "subprocess.run(['mount','-t','tmpfs','-o','mode=755','tmpfs','/tmp'],check=True)\n"
        "shutil.copyfile('/usr/bin/python3.12','/tmp/admission-parent-python')\n"
        "os.chmod('/tmp/admission-parent-python',0o500)\n"
        f"os.execve('/tmp/admission-parent-python',['admission-parent-python','-c',{_PROBE!r}],"
        "dict(os.environ,PYTHONHOME='/usr'))\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((
        str(Path.cwd() / 'src'), str(Path(sys.prefix) / 'lib/python3.12/site-packages'),
    )))
    return subprocess.run(
        ['unshare', '--user', '--map-auto', '--map-root-user', '--mount', sys.executable,
         '-c', prelude], input=json.dumps({k: v.hex() for k, v in payload.items()}),
        env=env, capture_output=True, text=True, timeout=40,
    )


@pytest.mark.parametrize('mode', ('normal', 'late_parent_loss', 'late_oserror', 'expired'))
def test_mapped_root_no_effect_entry_retains_one_use_after_child_restart(correlated, mode):  # noqa: F811
    _, consumed = correlated
    payload = dict(
        consumed=msgspec.json.encode(consumed), process=binding._read(7, 65536),
        producer=binding._read(13, 32768), packet=binding._read(18, 32768),
        synthetic_clock=str(json.loads(binding._read(18, 32768))['outage']['not_before_ns'] + 1).encode(),
    )
    if mode != 'normal':
        payload[mode] = b'causal fault injection, not authority'
    if mode == 'expired':
        payload['synthetic_clock'] = str(json.loads(payload['packet'])['outage']['deadline_ns']).encode()
    result = qualify_payload(payload)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'three-process-one-use-pass' in result.stdout
