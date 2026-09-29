"""Local Cloudflare watchdog and status server. Python standard library only.

Checks the live container's current URL, preserves the pilot authentication gate,
and restarts only that tunnel after sustained failures. Does not start Docker or
the BOM app. Email settings and credentials stay in the private deployment folder.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import smtplib
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request


CONTAINER = 'bom-pilot-tunnel-1'
URL_PATTERN = re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\Z')


def utc(timestamp=None):
    return datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), timezone.utc).isoformat()


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


@dataclass(frozen=True)
class Settings:
    interval: int = 30
    failures: int = 3
    timeout: int = 10
    grace: int = 90
    cooldown: int = 300
    max_cooldown: int = 1800
    gateway: str = 'http://127.0.0.1:13010'
    backend: str = 'http://127.0.0.1:8011'


class ProbeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Runtime:
    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.docker_path = shutil.which('docker') or 'docker'

    def docker(self, *arguments):
        try:
            result = subprocess.run([self.docker_path, *arguments], capture_output=True,
                text=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbeError('Docker is unavailable or did not respond.') from exc
        if result.returncode:
            # Docker diagnostics may include environment details. Do not expose
            # them through the local status page or email.
            raise ProbeError('Docker or the configured tunnel container is unavailable.')
        return result.stdout

    def container(self):
        state = json.loads(self.docker('inspect', '--format', '{{json .State}}', CONTAINER))
        if state.get('Running'):
            try:
                helper = json.loads(self.docker('exec', CONTAINER, 'cat', '/runtime/state.json'))
            except (ProbeError, ValueError):
                helper = {}  # A starting/stuck helper has no URL yet.
            candidate = helper.get('url')
            state['url'] = candidate if isinstance(candidate, str) and URL_PATTERN.fullmatch(candidate) else None
        else:
            state['url'] = None
        return state

    def request(self, url, *, public=False):
        proxies = {}
        if public:
            env_file = self.root / 'runtime/pilot/proxy.env'
            if env_file.exists():
                values = {}
                for line in env_file.read_text(encoding='utf-8-sig').splitlines():
                    if '=' in line and not line.lstrip().startswith('#'):
                        key, value = line.split('=', 1)
                        values[key.strip()] = value.strip().strip('"\'')
                proxy = values.get('HTTPS_PROXY') or values.get('https_proxy')
                if proxy:
                    proxies = {'https': proxy}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache',
            'User-Agent': 'BOM-Tunnel-Monitor/1.0'})
        try:
            with opener.open(request, timeout=self.settings.timeout) as response:
                return response.status, response.read(262144)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(4096)
        except (OSError, urllib.error.URLError):
            return 0, b''

    def protected_app(self, base, *, public=False):
        code, body = self.request(base + '/api/backend/health', public=public)
        if code != 401:
            return False
        try:
            if json.loads(body).get('detail') != 'Sign in to continue.':
                return False
        except (ValueError, AttributeError):
            return False
        code, body = self.request(base + '/login', public=public)
        return code == 200 and b'BOM Review Assistant' in body

    def origin_ready(self):
        code, body = self.request(self.settings.backend + '/health')
        try:
            healthy = code == 200 and json.loads(body).get('status') == 'ok'
        except (ValueError, AttributeError):
            healthy = False
        return healthy and self.protected_app(self.settings.gateway)

    def public_ready(self, url):
        return self.protected_app(url, public=True)

    def network_ready(self):
        code, body = self.request('https://www.cloudflare.com/cdn-cgi/trace', public=True)
        return code == 200 and b'colo=' in body

    def restart(self):
        # No shell and no Docker socket exposed through the HTTP status service.
        self.docker('restart', '--timeout', '15', CONTAINER)

    def stop(self):
        self.docker('stop', '--timeout', '15', CONTAINER)

    def send_email(self, config, notification):
        if config.get('delivery') == 'outlook':
            payload = self.root / 'runtime/pilot/monitor/outlook-message.json'
            write_json(payload, {'recipient': config.get('recipient'), 'sender': config.get('sender'),
                                 **notification})
            try:
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                    '-WindowStyle', 'Hidden', '-File', str(Path(__file__).with_name('Send-TunnelEmail.ps1')),
                    '-MessageFile', str(payload)], capture_output=True, text=True, timeout=45,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode:
                    raise ProbeError('Outlook did not accept the notification.')
            finally:
                payload.unlink(missing_ok=True)
            return
        for field in ('host', 'sender', 'recipient'):
            if not isinstance(config.get(field), str) or not config[field].strip():
                raise ValueError(f'Email configuration needs {field}.')
            if '\n' in config[field] or '\r' in config[field]:
                raise ValueError('Invalid email configuration.')
        mode = config.get('tls', 'starttls')
        if mode not in ('starttls', 'ssl'):
            raise ValueError('Email TLS must be starttls or ssl.')
        message = EmailMessage()
        message['From'], message['To'] = config['sender'], config['recipient']
        message['Subject'] = 'BOM Assistant: new Cloudflare link'
        message['Message-ID'] = f"<bom-tunnel-{notification['id']}@localhost>"
        message.set_content('The BOM Assistant public link is ready:\n\n' + notification['url'] +
            '\n\nVerified at: ' + notification['created_at'] +
            '\nUse your existing pilot login. Previous temporary links may no longer work.\n')
        context = ssl.create_default_context()
        smtp_class = smtplib.SMTP_SSL if mode == 'ssl' else smtplib.SMTP
        options = {'timeout': 20}
        if mode == 'ssl':
            options['context'] = context
        with smtp_class(config['host'], int(config.get('port', 465 if mode == 'ssl' else 587)), **options) as smtp:
            smtp.ehlo()
            if mode == 'starttls':
                smtp.starttls(context=context)
                smtp.ehlo()
            if config.get('username'):
                if not config.get('password'):
                    raise ValueError('Email authentication needs a password in the private config.')
                smtp.login(config['username'], config['password'])
            smtp.send_message(message)


class Monitor:
    def __init__(self, root, runtime, settings=Settings(), clock=time.time):
        self.root, self.runtime, self.settings, self.clock = root, runtime, settings, clock
        self.directory = root / 'runtime/pilot/monitor'
        self.control = self.directory / 'control.json'
        self.email_file = self.directory / 'email.local.json'
        self.state_file = self.directory / 'status.json'
        self.lock = threading.RLock()
        try:
            self.state = read_json(self.state_file, {})
        except (ValueError, OSError):
            self.state = {}
        if not isinstance(self.state, dict):
            self.state = {}
        self.state.update(status='starting', message='Monitor starting.', interval_seconds=settings.interval,
                          failure_threshold=settings.failures, grace_seconds=settings.grace)
        self.state.setdefault('events', [])
        self.state.setdefault('consecutive_failures', 0)
        self.state.setdefault('restart_count', 0)
        self.state.setdefault('recovery_attempts', 0)
        self.state.setdefault('notification_sequence', 0)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def save(self, **changes):
        with self.lock:
            self.state.update(changes, updated_at=utc(self.clock()))
            write_json(self.state_file, self.state)

    def event(self, kind, message, url=None):
        entry = {'time': utc(self.clock()), 'kind': kind, 'message': message}
        if url:
            entry['url'] = url
        with self.lock:
            self.state['events'] = (self.state['events'] + [entry])[-40:]

    def status(self, kind, message, **changes):
        if self.state.get('status') != kind:
            self.event(kind, message)
        self.save(status=kind, message=message, **changes)

    def enabled(self):
        control = read_json(self.control, {'enabled': False})
        return isinstance(control, dict) and control.get('enabled') is True

    def email(self):
        notification = self.state.get('pending_notification')
        if not notification or self.state.get('status') != 'healthy':
            return
        # Never email a link that has since failed or been superseded.
        if notification['url'] != self.state.get('candidate_url'):
            return
        try:
            config = read_json(self.email_file, {})
            if not isinstance(config, dict) or config.get('enabled') is not True:
                self.save(email_status='not_configured', email_message='Recipient and sending service need configuration.')
                return
            if self.clock() < self.state.get('email_retry_at', 0):
                return
            self.runtime.send_email(config, notification)
        except Exception:
            attempt = min(self.state.get('email_attempts', 0) + 1, 6)
            self.save(email_status='retrying', email_message='Email submission failed. Open Outlook and sign in, or check the private email settings.',
                      email_attempts=attempt, email_retry_at=self.clock() + min(60 * 2 ** (attempt - 1), 1800))
            return
        self.event('email_submitted', 'Replacement link submitted for email delivery.', notification['url'])
        self.save(email_status='submitted', email_message='Latest link submitted for email delivery.',
                  emailed_url=notification['url'], email_sent_at=utc(self.clock()),
                  pending_notification=None, email_attempts=0, email_retry_at=0)

    def recover(self):
        if not self.enabled():
            self.status('paused', 'Recovery is paused.', enabled=False)
            return
        # Recheck immediately before the mutation; the app may have gone down
        # since the preceding health check. Never rotate an unprotected origin.
        if not self.runtime.origin_ready():
            self.status('origin_unavailable', 'The local app or login gate is unavailable; waiting without rotating the link.')
            return
        now = self.clock()
        if now < self.state.get('next_restart_at', 0):
            self.status('cooldown', 'Tunnel remains unavailable; waiting before another recovery attempt.')
            return
        attempt = min(self.state.get('recovery_attempts', 0) + 1, 8)
        delay = min(self.settings.cooldown * 2 ** (attempt - 1), self.settings.max_cooldown)
        # Persist before Docker is called, so restarting this watchdog cannot
        # bypass the cooldown when the network or Docker fails halfway through.
        self.save(next_restart_at=now + delay, last_restart_at=utc(now), recovery_attempts=attempt)
        try:
            if not self.enabled():
                self.status('paused', 'Recovery is paused.', enabled=False)
                return
            self.runtime.restart()
            if not self.enabled():
                self.runtime.stop()
                self.status('paused', 'Tunnel stopped because recovery was paused.', enabled=False)
                return
        except ProbeError:
            self.status('recovery_failed', 'Tunnel restart failed. Recovery will retry after the cooldown.')
            return
        self.event('restart', 'Restarted the tunnel; waiting for a new verified public link.')
        self.status('recovering', 'Waiting for the replacement link to connect.',
                    restart_count=self.state['restart_count'] + 1, consecutive_failures=0,
                    candidate_url=None, candidate_since=self.clock())

    def step(self):
        now = self.clock()
        try:
            enabled = self.enabled()
        except (OSError, ValueError):
            self.status('configuration_error', 'Cannot read the recovery control file; recovery is paused.', enabled=False)
            return
        self.save(enabled=enabled, last_checked_at=utc(now))
        if not enabled:
            self.status('paused', 'Monitoring recovery is paused. Use Pilot.ps1 monitor-start to resume.')
            return
        try:
            container = self.runtime.container()
        except (ProbeError, ValueError, KeyError, TypeError):
            self.status('docker_unavailable', 'Docker or the tunnel container is unavailable. Start Docker and the pilot to resume.')
            return
        started = container.get('StartedAt')
        candidate = container.get('url')
        if started != self.state.get('container_started_at') or candidate != self.state.get('candidate_url'):
            self.save(container_started_at=started, candidate_url=candidate,
                      candidate_since=now, consecutive_failures=0)
        if not self.runtime.origin_ready():
            self.status('origin_unavailable', 'The local app or login gate is unavailable; waiting without rotating the link.',
                        consecutive_failures=0)
            return
        healthy = bool(container.get('Running') and candidate and self.runtime.public_ready(candidate))
        if healthy:
            if self.state.get('url') != candidate:
                sequence = self.state['notification_sequence'] + 1
                notification = {'id': f'{int(now)}-{sequence}', 'url': candidate, 'created_at': utc(now)}
                self.event('link_ready', 'A new public link was verified.', candidate)
                self.save(url=candidate, notification_sequence=sequence, pending_notification=notification,
                          email_status='pending', email_attempts=0, email_retry_at=0)
                (self.root / 'runtime/pilot/url.txt').write_text(candidate + '\n', encoding='utf-8')
            self.status('healthy', 'Public link is reachable and requires a pilot login.',
                        last_success_at=utc(self.clock()), consecutive_failures=0, recovery_attempts=0)
            self.email()
            return
        if container.get('Running') and now - self.state.get('candidate_since', now) < self.settings.grace:
            self.status('starting', 'Waiting for the current tunnel URL to propagate and connect.')
            return
        if not self.runtime.network_ready():
            self.status('network_unavailable', 'Cloudflare cannot be reached through this PC\'s network/proxy; waiting without rotating the link.',
                        consecutive_failures=0)
            return
        failures = self.state['consecutive_failures'] + 1
        self.save(consecutive_failures=failures)
        if failures >= self.settings.failures:
            self.recover()
        else:
            self.status('degraded', 'Public tunnel check failed; checking again before recovery.')


def status_server(monitor, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Loopback binding plus an exact Host allowlist resists DNS rebinding.
            bound_port = self.server.server_port
            if self.headers.get('Host') not in (f'127.0.0.1:{bound_port}', f'localhost:{bound_port}'):
                self.send_error(403)
                return
            if self.path == '/api/status':
                body = json.dumps(monitor.snapshot()).encode()
                content_type = 'application/json'
            elif self.path == '/':
                body = Path(__file__).with_suffix('.html').read_bytes()
                content_type = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


@contextmanager
def instance_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'monitor.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('A tunnel monitor is already running for this deployment.') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-root', type=Path, default=Path('C:/ProgramData/BOM-Supabase'))
    parser.add_argument('--port', type=int, default=13011)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval < 5 or not 1024 <= args.port <= 65535:
        parser.error('Use an interval of at least 5 seconds and a port from 1024 to 65535.')
    root = args.deployment_root.resolve()
    if not (root / 'runtime/pilot/compose.yml').is_file():
        parser.error('The existing pilot deployment was not found.')
    settings = Settings(interval=args.interval)
    monitor = Monitor(root, Runtime(root, settings), settings)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    with instance_lock(monitor.directory):
        server = None
        try:
            if not args.once:
                server = status_server(monitor, args.port)
                threading.Thread(target=server.serve_forever, daemon=True).start()
            while not stop.is_set():
                try:
                    monitor.step()
                except Exception:
                    monitor.status('monitor_error', 'Monitoring encountered an error and will retry. Check the local deployment settings.')
                if args.once:
                    print(json.dumps(monitor.snapshot()))
                    break
                stop.wait(settings.interval)
        finally:
            if server:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    main()
