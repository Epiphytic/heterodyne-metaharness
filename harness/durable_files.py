"""Private crash-durable JSON snapshots shared by harness projections."""
import json
import os
from pathlib import Path


def write_json(path, data):
    target = Path(path)
    temp = target.with_suffix('.tmp')
    with temp.open('w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(target)
    fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

