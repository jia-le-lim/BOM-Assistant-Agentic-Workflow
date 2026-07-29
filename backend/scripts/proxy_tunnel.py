"""Local TCP forwarder: localhost:5432 -> HTTP CONNECT -> Supabase pooler.

Why this exists
---------------
On this network every connection must traverse proxy-png.intel.com:912
(Backend_Scaffold_Notes.md F1: a raw TCP connect to :443 fails outright).
libpq/psycopg has no HTTP-proxy support, so `DATABASE_URL` pointing straight at
the Supabase pooler times out from a developer machine.

The proxy does honour CONNECT to arbitrary ports -- verified against the pooler
on both 5432 and 6543 (F3). So we terminate that tunnel locally: point psycopg
at 127.0.0.1 and this process does the CONNECT handshake per connection.

Development only. A deployed backend with normal egress talks to the pooler
directly and does not need this.

    python backend/scripts/proxy_tunnel.py
    DATABASE_URL=postgresql://postgres.<ref>:<pw>@127.0.0.1:5432/postgres

Note the pooler requires the tenant-qualified user (`postgres.<project_ref>`),
and sslmode must stay on -- TLS runs end-to-end inside the tunnel, so the proxy
sees only ciphertext (verified: cert subject supabase.co, issuer Google Trust
Services, no corporate interception).
"""

import argparse
import os
import socket
import sys
import threading

DEFAULT_PROXY = os.environ.get("HTTPS_PROXY", "http://proxy-png.intel.com:912")
DEFAULT_REGION = os.environ.get("SUPABASE_REGION", "ap-southeast-1")


def parse_proxy(url: str) -> tuple[str, int]:
    hostport = url.split("://", 1)[-1].rstrip("/")
    host, _, port = hostport.partition(":")
    return host, int(port or 8080)


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client: socket.socket, proxy: tuple[str, int],
           target: tuple[str, int]) -> None:
    host, port = target
    try:
        upstream = socket.create_connection(proxy, timeout=15)
        upstream.sendall(
            f"CONNECT {host}:{port} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Proxy-Connection: keep-alive\r\n\r\n".encode())
        # Read exactly the CONNECT response headers -- anything after the blank
        # line is already Postgres traffic and must not be swallowed.
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = upstream.recv(1)
            if not chunk:
                raise OSError("proxy closed during CONNECT")
            buf += chunk
        status = buf.split(b"\r\n", 1)[0].decode("utf-8", "replace")
        if " 200 " not in status:
            print(f"  ! CONNECT refused: {status}", file=sys.stderr)
            client.close()
            upstream.close()
            return
    except OSError as e:
        print(f"  ! tunnel failed: {e}", file=sys.stderr)
        client.close()
        return

    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listen-port", type=int, default=5432)
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--remote-host", default=None,
                    help="defaults to aws-1-<region>.pooler.supabase.com")
    ap.add_argument("--remote-port", type=int, default=5432,
                    help="5432 session mode, 6543 transaction mode")
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    args = ap.parse_args()

    remote_host = args.remote_host or f"aws-1-{args.region}.pooler.supabase.com"
    proxy = parse_proxy(args.proxy)
    target = (remote_host, args.remote_port)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.listen_port))
    srv.listen(32)

    print(f"tunnel  127.0.0.1:{args.listen_port}"
          f"  ->  {proxy[0]}:{proxy[1]}  ->  {target[0]}:{target[1]}")
    print("Ctrl-C to stop.")
    try:
        while True:
            client, _ = srv.accept()
            threading.Thread(target=handle, args=(client, proxy, target),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
    finally:
        srv.close()


if __name__ == "__main__":
    raise SystemExit(main())
