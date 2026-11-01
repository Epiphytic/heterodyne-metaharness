"""Actual private-channel mechanics, not installed runtime or native consent."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne.actions import deployment_admission_peer as peer
from heterodyne.actions.codec import InvalidAction


def test_nonroot_refuses_before_reading_inherited_fds():
    with pytest.raises(InvalidAction, match="admission-root-required"):
        peer._ParentChannel()


_PROBE = r'''
import array,fcntl,hashlib,json,os,socket,sys
from pathlib import Path
from heterodyne.actions import deployment_admission_peer as p
from heterodyne.actions.codec import InvalidAction,encode_record
root=Path('/tmp/admission-probe');root.mkdir(mode=0o700)
binary='/tmp/admission-parent-python'
profile=p._ParentProfile('heterodyne-deployment-parent-profile.v1',binary,
    hashlib.sha256(Path(binary).read_bytes()).hexdigest(),*(['a'*64]*5))
profile_path=root/'profile';profile_path.write_bytes(encode_record(profile));profile_path.chmod(0o400)
profile_fd=os.open(profile_path,os.O_RDONLY)
lease=os.open(root/'launch.lock',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
nonce=os.memfd_create('private-native-nonce',os.MFD_ALLOW_SEALING|os.MFD_CLOEXEC)
os.write(nonce,b'n'*32);os.fchmod(nonce,0o400)
fcntl.fcntl(nonce,fcntl.F_ADD_SEALS,p._SEALS)
nonce_read=os.open('/proc/self/fd/'+str(nonce),os.O_RDONLY|os.O_CLOEXEC)
base=p._Handoff('heterodyne-owned-deployment-admission.v1',(b'n'*32).hex(),'b'*64,'a'*64,'a'*64)
for case in ('valid','maximum','bad-nonce','wrong-schema','oversize-meta',
             'oversize-raw','foreign-sender','rights','unsealed'):
    parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    child.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
    parent.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,p._MAX_FRAME)
    holders=[fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,64) for fd in (profile_fd,child.fileno(),lease,nonce_read)]
    if case=='unsealed':
        fresh=os.memfd_create('unsealed',os.MFD_ALLOW_SEALING);os.write(fresh,b'n'*32)
        os.close(holders[3]);holders[3]=fcntl.fcntl(fresh,fcntl.F_DUPFD_CLOEXEC,64);os.close(fresh)
    pid=os.fork()
    if pid==0:
        parent.close()
        for original,target in zip(holders,(3,4,5,6)):os.dup2(original,target)
        try:
            channel=p._ParentChannel()
            assert case in ('valid','maximum'),case+' accepted'
            assert channel.consumed_raw==(b'x'*p._MAX_FRAME if case=='maximum' else b'{}')
            channel.check();channel.close();os._exit(0)
        except (InvalidAction,OSError) as error:
            if case in ('valid','maximum'):os.write(2,(repr(error)+'\n').encode())
            os._exit(0 if case not in ('valid','maximum') else 77)
        except BaseException:
            os._exit(78)
    child.close()
    for fd in holders:os.close(fd)
    value=json.loads(encode_record(base))
    if case=='bad-nonce':value['nonce_hex']='b'*64
    if case=='wrong-schema':value['schema']='claimed-consent'
    metadata=json.dumps(value,separators=(',',':')).encode()
    if case=='oversize-meta':metadata=b'x'*4097
    size=p._MAX_FRAME+1 if case=='oversize-raw' else p._MAX_FRAME
    raw=b'x'*size if case in ('maximum','oversize-raw') else b'{}'
    def send():
        try:
            if case=='rights':
                parent.sendmsg([metadata],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[profile_fd]))])
            else:parent.send(metadata)
            parent.send(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    if case=='foreign-sender':
        sender=os.fork()
        if sender==0:send();os._exit(0)
        assert os.waitpid(sender,0)[1]==0
    else:send()
    parent.close()
    _,status=os.waitpid(pid,0)
    assert status==0,(case,status)
print('actual-root-channel-positive-maximum-and-negative-pass')
'''


def test_actual_root_parent_channel_and_sealed_nonce():
    # Copy/exec the parent ELF in private tmpfs so its actual /proc/exe inode
    # has namespace-root custody. Imports are fixture-controlled, not a claimed
    # installed source closure. No root helper, permit or service is invoked.
    prelude = (
        "import os,shutil,subprocess\n"
        "subprocess.run(['mount','-t','tmpfs','-o','mode=755','tmpfs','/tmp'],check=True)\n"
        "shutil.copyfile('/usr/bin/python3.12','/tmp/admission-parent-python')\n"
        "os.chmod('/tmp/admission-parent-python',0o500)\n"
        f"os.execve('/tmp/admission-parent-python',['admission-parent-python','-c',{_PROBE!r}],"
        "dict(os.environ,PYTHONHOME='/usr'))\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((
        str(Path.cwd() / "src"), str(Path(sys.prefix) / "lib/python3.12/site-packages"),
    )))
    result = subprocess.run(
        ["unshare", "--user", "--map-auto", "--map-root-user", "--mount", sys.executable,
         "-c", prelude], env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "actual-root-channel-positive-maximum-and-negative-pass" in result.stdout
