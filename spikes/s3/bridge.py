"""Inside the sandbox: listen on the loopback proxy port, forward to the bound proxy socket, run the agent."""
import os
import socket
import subprocess
import sys
import threading

LISTEN = ('127.0.0.1', 3128)   # the only place the in-sandbox proxy address is defined
PROXY_URL = f'http://{LISTEN[0]}:{LISTEN[1]}'


def forward(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    except OSError:   # deviation: peer reset/close is normal teardown, not an error
        pass
    finally:
        b.close()


def serve(unix_path: str) -> None:
    listener = socket.create_server(LISTEN)
    while True:
        client, _ = listener.accept()
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.connect(unix_path)
        except OSError:   # fix round 1: proxy down -> refuse this client, keep the bridge alive
            client.close()
            upstream.close()
            continue
        threading.Thread(target=forward, args=(client, upstream), daemon=True).start()
        threading.Thread(target=forward, args=(upstream, client), daemon=True).start()


if __name__ == '__main__':
    threading.Thread(target=serve, args=(sys.argv[1],), daemon=True).start()
    env = dict(os.environ, HTTPS_PROXY=PROXY_URL, HTTP_PROXY=PROXY_URL,
               NO_PROXY='')
    sys.exit(subprocess.call(sys.argv[3:], env=env))   # argv: bridge.py <sock> -- <cmd...>
