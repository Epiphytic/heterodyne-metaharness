"""Optional bounded hook enrichment, isolated from durable identity registration."""
import json
from pathlib import Path
import sys

from .beads import Beads
from .memory import memory_context


def main():
    home = Path(sys.argv[1])
    path = home / 'workstreams/harness-config.json'
    config = json.loads(path.read_text()) if path.exists() else {}
    run = json.load(sys.stdin)
    print(Beads(config.get('beads', {})).context(run))
    print(memory_context(run))


if __name__ == '__main__':
    main()
