#!/usr/bin/env python3
"""Install an idempotent, explicitly authorized routing hook into Marmot's adapter."""
import argparse
import ast
import os
from pathlib import Path
import shutil
import tempfile

MARKER = "# BEGIN HERMES WORKSTREAM ROUTING"
ANCHOR = '            if not await self._should_run_turn(event):\n'
HOOK = '''            # BEGIN HERMES WORKSTREAM ROUTING
            # Ownership + explicit sender permission activates new harness groups.
            # Durable enqueue returns before the ordinary gateway starts a turn.
            if not hasattr(self, "_workstream_router"):
                import importlib.util
                routing_spec = importlib.util.spec_from_file_location(
                    "hermes_workstream_routing", Path(__file__).with_name("_workstream_routing.py")
                )
                routing_module = importlib.util.module_from_spec(routing_spec)
                routing_spec.loader.exec_module(routing_module)
                self._workstream_router = routing_module.route_with_retry
            if await self._workstream_router(
                event, account_id=self.account_id_hex,
                allowed_senders=resolve_allowed_message_senders(),
            ):
                return
            # END HERMES WORKSTREAM ROUTING
'''


def atomic_write(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        try:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
            temp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def install(adapter):
    original = adapter.read_text()
    if MARKER not in original:
        if original.count(ANCHOR) != 1:
            raise RuntimeError("adapter routing anchor changed; refusing an unverified patch")
        updated = original.replace(ANCHOR, HOOK + ANCHOR)
    elif HOOK in original:
        updated = original
    else:
        raise RuntimeError("an incompatible workstream routing hook already exists")
    ast.parse(updated)
    source = Path(__file__).parent / "harness" / "routing.py"
    routing = source.read_text()
    ast.parse(routing)
    # Install the dependency before publishing the adapter that imports it.
    atomic_write(adapter.with_name("_workstream_routing.py"), routing)
    if updated != original:
        backup = adapter.with_name(adapter.name + ".before-workstream-routing")
        if not backup.exists():
            shutil.copy2(adapter, backup)
        atomic_write(adapter, updated)
    return updated != original


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adapter", type=Path)
    args = parser.parse_args()
    print("installed" if install(args.adapter) else "already installed")
