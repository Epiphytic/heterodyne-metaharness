"""Review r1 #3 investigation: which errno each socket type/op gets, plus netns, routes and seccomp status.
Run inside a sandbox: openshell sandbox exec -n <name> --no-tty -- python3 - < netbattery.py"""

import errno
import os
import socket

A4, A6, DNS4 = ('1.1.1.1', 443), ('2606:4700:4700::1111', 443), ('1.1.1.1', 53)


def name(x: int) -> str:
    return errno.errorcode.get(x, str(x))


def t(label, fam, kind, proto, op):
    try:
        s = socket.socket(fam, kind, proto)
    except OSError as x:
        print(f'{label}: socket() -> {name(x.errno)}')
        return
    s.settimeout(3)
    try:
        op(s)
        print(f'{label}: OK')
    except TimeoutError:
        print(f'{label}: TIMEOUT')
    except OSError as x:
        print(f'{label}: {name(x.errno)}')
    finally:
        s.close()


t('tcp4 connect', socket.AF_INET, socket.SOCK_STREAM, 0, lambda s: s.connect(A4))
t('tcp6 connect', socket.AF_INET6, socket.SOCK_STREAM, 0, lambda s: s.connect(A6))
t('udp4 connect', socket.AF_INET, socket.SOCK_DGRAM, 0, lambda s: s.connect(DNS4))
t('udp4 connect then send', socket.AF_INET, socket.SOCK_DGRAM, 0, lambda s: (s.connect(DNS4), s.send(b'x')))
t('udp4 sendto', socket.AF_INET, socket.SOCK_DGRAM, 0, lambda s: s.sendto(b'x', DNS4))
t('icmp4 dgram (ping socket)', socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP,
  lambda s: s.sendto(b'\x08\0\0\0\0\0\0\0', (A4[0], 0)))
t('raw4 icmp', socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP, lambda s: s.sendto(b'x', (A4[0], 0)))
t('sctp4', socket.AF_INET, socket.SOCK_STREAM, 132, lambda s: s.connect(A4))
t('packet', socket.AF_PACKET, socket.SOCK_RAW, 0, lambda s: None)
with open('/proc/net/dev') as f:
    print('/proc/net/dev ifaces:', [line.split(':')[0].strip() for line in f.readlines()[2:]])
with open('/proc/net/route') as f:
    print('/proc/net/route entries:', len(f.readlines()) - 1)
with open('/proc/net/ipv6_route') as f:
    print('/proc/net/ipv6_route:', [line.split()[-1] for line in f])
print('netns:', os.readlink('/proc/self/ns/net'))
with open('/proc/self/status') as f:
    keys = ('Seccomp', 'NoNewPrivs', 'CapEff', 'CapBnd')
    print(*[line.strip() for line in f if line.startswith(keys)], sep='\n')
