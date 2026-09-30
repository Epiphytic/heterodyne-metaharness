"""Allowlisting HTTP CONNECT proxy on a unix socket. Spike S3; not package code."""
import asyncio
import json
import sys


def allowed(host: str, allow: list[str]) -> bool:
    return any(host == a or (a.startswith('.') and host.endswith(a)) for a in allow)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def handle(reader, writer, allow):
    line = (await reader.readline()).decode('latin-1').strip()
    while (await reader.readline()) not in (b'\r\n', b'\n', b''):
        pass
    method, _, rest = line.partition(' ')
    target = rest.split(' ')[0]
    host, _, port = target.rpartition(':')
    ok = method == 'CONNECT' and port == '443' and allowed(host, allow)
    print(json.dumps({'method': method, 'target': target, 'allowed': ok}), file=sys.stderr, flush=True)
    if not ok:
        writer.write(b'HTTP/1.1 403 Forbidden\r\n\r\n')
        await writer.drain()
        writer.close()
        return
    up_r, up_w = await asyncio.open_connection(host, int(port))
    writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
    await writer.drain()
    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))


async def main(path: str, allow: list[str]) -> None:
    server = await asyncio.start_unix_server(lambda r, w: handle(r, w, allow), path=path)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1], sys.argv[2].split(',')))
