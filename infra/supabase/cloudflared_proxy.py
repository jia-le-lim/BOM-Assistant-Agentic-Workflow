"""Run a temporary Cloudflare pilot through an HTTP CONNECT corporate proxy.

Quick Tunnel provisioning uses Python's verified HTTPS client. Cloudflared's
HTTP/2 TLS connection passes unchanged through a loopback TCP relay and CONNECT.
Tunnel credentials stay in memory. The origin must require the pilot login.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--origin', default='http://127.0.0.1:13010')
    parser.add_argument('--proxy', default=os.environ.get('HTTPS_PROXY', 'http://proxy-png.intel.com:912'))
    parser.add_argument('--metrics-port', type=int, default=20242)
    parser.add_argument('--metrics-host', default='127.0.0.1')
    parser.add_argument('--cloudflared', type=Path)
    parser.add_argument('--log-stdout', action='store_true')
    args = parser.parse_args()
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        local.open(args.origin, timeout=10).close()
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise RuntimeError(f'Pilot login gate returned HTTP {error.code}') from error
    else:
        raise RuntimeError('Pilot login gate must return HTTP 401 before sharing')

    proxy = urllib.parse.urlsplit(args.proxy)
    if proxy.scheme != 'http' or not proxy.hostname or proxy.username:
        raise ValueError('Expected an HTTP proxy URL without embedded credentials')
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({'https': args.proxy}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    state_file = runtime / 'state.json'
    state = {'helper_pid': os.getpid(), 'origin': args.origin,
             'metrics_url': f'http://127.0.0.1:{args.metrics_port}/ready',
             'status': 'starting'}
    state_lock = threading.Lock()

    def save(**changes):
        with state_lock:
            state.update(changes)
            temporary = runtime / 'state.tmp'
            temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
            temporary.replace(state_file)

    class Provisioning(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/tunnel':
                self.send_error(404)
                return
            try:
                request = urllib.request.Request(
                    'https://api.trycloudflare.com/tunnel', data=b'',
                    headers={'Content-Type': 'application/json',
                             'User-Agent': 'cloudflared/2026.9.0'},
                )
                with opener.open(request, timeout=12) as response:
                    body = response.read()
                data = json.loads(body)
                if not data.get('success'):
                    raise RuntimeError('Cloudflare rejected tunnel provisioning')
                hostname = data['result']['hostname']
                url = hostname if hostname.startswith('https://') else 'https://' + hostname
                save(url=url)
                (runtime / 'url.txt').write_text(url + '\n', encoding='utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                print(f'Tunnel URL allocated: {url}', flush=True)
            except Exception as error:
                print(f'Provisioning failed: {type(error).__name__}', flush=True)
                self.send_error(502, 'Cloudflare provisioning failed through proxy')

        def log_message(self, *args):
            pass

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            upstream = None
            try:
                for edge in ('region1.v2.argotunnel.com', 'region2.v2.argotunnel.com'):
                    try:
                        upstream = socket.create_connection((proxy.hostname, proxy.port or 80), timeout=10)
                        upstream.sendall(
                            f'CONNECT {edge}:7844 HTTP/1.1\r\n'
                            f'Host: {edge}:7844\r\n\r\n'.encode('ascii'))
                        headers = b''
                        while not headers.endswith(b'\r\n\r\n'):
                            chunk = upstream.recv(1)
                            if not chunk or len(headers) >= 16384:
                                raise OSError('Invalid CONNECT response')
                            headers += chunk
                        status = headers.split(b'\r\n', 1)[0]
                        if status.split()[1] != b'200':
                            raise OSError('Proxy refused CONNECT')
                        print(f'CONNECT established to {edge}:7844', flush=True)
                        break
                    except OSError:
                        if upstream is not None:
                            upstream.close()
                        upstream = None
                if upstream is None:
                    raise OSError('Both Cloudflare endpoints failed through proxy')
                upstream.settimeout(30)
                self.request.settimeout(30)
                sockets = (self.request, upstream)
                while not stopped.is_set():
                    readable, _, _ = select.select(sockets, [], [], 1)
                    for source in readable:
                        chunk = source.recv(65536)
                        if not chunk:
                            return
                        destination = upstream if source is self.request else self.request
                        destination.sendall(chunk)
            except OSError as error:
                print(f'Edge relay closed: {error}', flush=True)
            finally:
                if upstream is not None:
                    upstream.close()

    class RelayServer(socketserver.ThreadingTCPServer):
        daemon_threads = True

    child = None
    api = ThreadingHTTPServer(('127.0.0.1', 0), Provisioning)
    relay = RelayServer(('127.0.0.1', 0), Relay)
    save()
    try:
        for server in (api, relay):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        executable = args.cloudflared or runtime / 'cloudflared.exe'
        command = [str(executable), 'tunnel', '--no-autoupdate',
                   '--no-prechecks', '--protocol', 'http2', '--edge-ip-version', '4',
                   '--edge', f'127.0.0.1:{relay.server_address[1]}',
                   '--quick-service', f'http://127.0.0.1:{api.server_address[1]}',
                   '--metrics', f'{args.metrics_host}:{args.metrics_port}', '--url', args.origin]
        output = (contextlib.nullcontext(sys.stdout) if args.log_stdout else
                  (runtime / 'cloudflared.log').open('a', encoding='utf-8'))
        with output as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            save(cloudflared_pid=child.pid)
            deadline = time.monotonic() + 60
            while (child.poll() is None and not stopped.is_set()
                   and not (runtime / 'stop.request').exists()):
                if state['status'] == 'starting':
                    try:
                        with local.open(state['metrics_url'], timeout=2) as ready:
                            if ready.status == 200:
                                save(status='running')
                                print('Tunnel connected; pilot login required.', flush=True)
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() > deadline:
                            raise RuntimeError('Tunnel did not connect within 60 seconds')
                stopped.wait(0.5)
            if child.poll() is not None and child.returncode:
                raise RuntimeError(f'Cloudflared exited with code {child.returncode}')
    finally:
        stopped.set()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for server in (api, relay):
            server.shutdown()
            server.server_close()
        save(status='stopped')


if __name__ == '__main__':
    main()
