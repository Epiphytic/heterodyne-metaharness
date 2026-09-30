"""Outside the sandbox: log inotify events on credential dirs (inotifywait stand-in). Spike S3.

Usage: watch.py <log> <dir>...  Writes one JSON line per event until killed.
"""
import ctypes
import json
import os
import struct
import sys
import time

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x002, 0x004, 0x008
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x040, 0x080, 0x100, 0x200
MASK = IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
NAMES = {IN_MODIFY: 'MODIFY', IN_ATTRIB: 'ATTRIB', IN_CLOSE_WRITE: 'CLOSE_WRITE', IN_MOVED_FROM: 'MOVED_FROM',
         IN_MOVED_TO: 'MOVED_TO', IN_CREATE: 'CREATE', IN_DELETE: 'DELETE'}


def main(log_path: str, dirs: list[str]) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init()
    wds = {libc.inotify_add_watch(fd, d.encode(), MASK): d for d in dirs}
    with open(log_path, 'a') as log:
        while True:
            buf = os.read(fd, 65536)
            i = 0
            while i < len(buf):
                wd, mask, _, length = struct.unpack_from('iIII', buf, i)
                name = buf[i + 16:i + 16 + length].rstrip(b'\0').decode()
                i += 16 + length
                events = [n for bit, n in NAMES.items() if mask & bit]
                log.write(json.dumps({'t': round(time.time(), 3), 'dir': os.path.basename(wds.get(wd, '?')),
                                      'name': name, 'events': events}) + '\n')
                log.flush()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
