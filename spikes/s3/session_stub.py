"""Per-session socket: accepts only hook_event and ws_request carrying the launch token."""
import asyncio
import json
import sys

ALLOWED = {'hook_event', 'ws_request'}


async def handle(reader, writer, token, log):
    line = await reader.readline()
    try:
        req = json.loads(line)
        ok = req.get('token') == token and req.get('type') in ALLOWED
        reply = {'ok': ok} if ok else {'ok': False, 'error': 'forbidden'}
    except ValueError:
        reply = {'ok': False, 'error': 'malformed'}
    log.write(json.dumps({'req': line.decode(errors='replace').strip(), 'reply': reply}) + '\n')
    log.flush()
    writer.write((json.dumps(reply) + '\n').encode())
    await writer.drain()
    writer.close()


async def main(path, token, log_path):
    with open(log_path, 'a') as log:
        server = await asyncio.start_unix_server(lambda r, w: handle(r, w, token, log), path=path)
        async with server:
            await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main(*sys.argv[1:4]))
