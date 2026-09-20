"""Marmot status interception before routing/model dispatch; spec/channel-status.md."""
import asyncio
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


async def response(group):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-B', '-m', 'harness.channel_status', '--group', group,
        cwd=str(ROOT), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=3.5)
        if process.returncode:
            return 'Workstream status unavailable; retry /status later.'
        return output.decode().strip()
    except asyncio.TimeoutError:
        return 'Workstream status timed out; retry /status later.'
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def handle(adapter, event, allowed_senders):
    text = str(event.get('text') or '').strip()
    if not text or text.split()[0] != '/status':
        return False
    # Consume even unauthorized commands: no routing or model fallback.
    account = str(adapter.account_id_hex).lower()
    sender = str(event.get('sender_account_id_hex') or '').lower()
    if (not sender or sender == account or sender not in {s.lower() for s in allowed_senders}
            or str(event.get('account_id_hex') or '').lower() != account):
        return True
    group, message = event.get('group_id_hex'), event.get('message_id_hex')
    if not group or not message:
        return True
    reply = ('Use /status without arguments in this channel.' if text != '/status'
             else await response(group))
    # Direct connector RPC, no gateway session or model. One stable request identity.
    key = 'workstream-status:' + hashlib.sha256((account+'\0'+group+'\0'+message).encode()).hexdigest()
    await asyncio.wait_for(adapter.client.send_final(
        account, group, reply, reply_to_message_id_hex=message, idempotency_key=key), timeout=1)
    return True
