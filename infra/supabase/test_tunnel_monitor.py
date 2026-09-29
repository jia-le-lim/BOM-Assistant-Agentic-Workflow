"""Offline watchdog tests: no Docker changes, public tunnels or real emails."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from tunnel_monitor import Monitor, ProbeError, Runtime, Settings, status_server, write_json


FIRST = 'https://first-working-link.trycloudflare.com'
SECOND = 'https://new-working-link.trycloudflare.com'


class FakeRuntime:
    def __init__(self):
        self.current = {'Running': True, 'StartedAt': 'first-start', 'url': FIRST}
        self.origin = self.public = self.network = True
        self.restarts = self.stops = 0
        self.emails = []
        self.email_failure = self.docker_failure = self.restart_failure = False
        self.on_restart = None

    def container(self):
        if self.docker_failure:
            raise ProbeError('unavailable')
        return self.current

    def origin_ready(self):
        return self.origin

    def public_ready(self, url):
        return self.public

    def network_ready(self):
        return self.network

    def restart(self):
        self.restarts += 1
        if self.restart_failure:
            raise ProbeError('failed restart')
        self.current = {'Running': True, 'StartedAt': 'second-start', 'url': SECOND}
        if self.on_restart:
            self.on_restart()

    def stop(self):
        self.stops += 1

    def send_email(self, config, notification):
        if self.email_failure:
            raise RuntimeError('sensitive diagnostic which must not reach the status page')
        self.emails.append(notification.copy())


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.runtime = FakeRuntime()
        self.settings = Settings(grace=0)
        self.monitor = Monitor(self.root, self.runtime, self.settings, clock=lambda: self.now)
        write_json(self.monitor.control, {'enabled': True})
        write_json(self.monitor.email_file, {'enabled': True, 'delivery': 'outlook',
                                           'sender': 'owner@example.test', 'recipient': 'owner@example.test'})

    def tick(self, seconds=30):
        self.now += seconds
        self.monitor.step()
        return self.monitor.snapshot()

    def test_current_link_is_verified_saved_and_emailed_once(self):
        state = self.tick()
        self.assertEqual(state['status'], 'healthy')
        self.assertEqual(state['url'], FIRST)
        self.assertEqual(state['email_status'], 'submitted')
        self.assertEqual((self.root / 'runtime/pilot/url.txt').read_text().strip(), FIRST)
        self.tick()
        self.assertEqual([email['url'] for email in self.runtime.emails], [FIRST])
        self.assertEqual(self.runtime.restarts, 0)

    def test_sustained_outage_recovers_and_emails_only_verified_replacement(self):
        self.tick()
        self.runtime.public = False
        self.assertEqual(self.tick()['status'], 'degraded')
        self.tick()
        self.assertEqual(self.runtime.restarts, 0)
        self.assertEqual(self.tick()['status'], 'recovering')
        self.assertEqual(self.runtime.restarts, 1)
        self.assertEqual(len(self.runtime.emails), 1)
        self.runtime.public = True
        state = self.tick()
        self.assertEqual(state['url'], SECOND)
        self.assertEqual([email['url'] for email in self.runtime.emails], [FIRST, SECOND])

    def test_transient_failure_resets_after_success(self):
        self.tick()
        self.runtime.public = False
        self.tick()
        self.tick()
        self.runtime.public = True
        self.assertEqual(self.tick()['consecutive_failures'], 0)
        self.assertEqual(self.runtime.restarts, 0)

    def test_local_app_or_network_outage_does_not_rotate_links(self):
        self.runtime.public = False
        for name, expected in (('origin', 'origin_unavailable'), ('network', 'network_unavailable')):
            setattr(self.runtime, name, False)
            for _ in range(5):
                self.assertEqual(self.tick()['status'], expected)
            setattr(self.runtime, name, True)
        self.assertEqual(self.runtime.restarts, 0)

    def test_docker_off_is_reported_without_attempting_to_start_it(self):
        self.runtime.docker_failure = True
        self.assertEqual(self.tick()['status'], 'docker_unavailable')
        self.assertEqual(self.runtime.restarts, 0)

    def test_manual_pause_prevents_restart_and_resume_retains_control(self):
        self.runtime.public = False
        write_json(self.monitor.control, {'enabled': False})
        for _ in range(4):
            self.assertEqual(self.tick()['status'], 'paused')
        self.assertEqual(self.runtime.restarts, 0)
        write_json(self.monitor.control, {'enabled': True})
        for _ in range(3):
            self.tick()
        self.assertEqual(self.runtime.restarts, 1)

    def test_pause_during_restart_closes_the_tunnel_again(self):
        self.runtime.public = False
        self.runtime.on_restart = lambda: write_json(self.monitor.control, {'enabled': False})
        for _ in range(3):
            state = self.tick()
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(self.runtime.stops, 1)

    def test_cooldown_survives_monitor_restart_and_increases_on_repeated_failure(self):
        self.runtime.public = False
        self.runtime.restart_failure = True
        for _ in range(3):
            state = self.tick()
        self.assertEqual(state['status'], 'recovery_failed')
        deadline = state['next_restart_at']
        self.monitor = Monitor(self.root, self.runtime, self.settings, clock=lambda: self.now)
        self.assertEqual(self.tick()['status'], 'cooldown')
        self.assertEqual(self.runtime.restarts, 1)
        self.now = deadline
        state = self.tick(0)
        self.assertEqual(self.runtime.restarts, 2)
        self.assertEqual(state['next_restart_at'] - self.now, 600)

    def test_propagation_grace_does_not_rotate_a_new_url(self):
        self.monitor.settings = Settings(grace=90)
        self.runtime.public = False
        for _ in range(3):
            self.assertEqual(self.tick()['status'], 'starting')
        self.assertEqual(self.runtime.restarts, 0)
        self.assertEqual(self.tick()['status'], 'degraded')

    def test_stopped_container_is_recovered_when_monitoring_is_enabled(self):
        self.runtime.current.update(Running=False, url=None)
        for _ in range(3):
            self.tick()
        self.assertEqual(self.runtime.restarts, 1)

    def test_malformed_control_fails_closed(self):
        self.monitor.control.write_text('{broken')
        self.assertEqual(self.tick()['status'], 'configuration_error')
        self.assertEqual(self.runtime.restarts, 0)

    def test_email_is_retried_after_failure_and_survives_a_monitor_restart(self):
        self.runtime.email_failure = True
        state = self.tick()
        self.assertEqual(state['email_status'], 'retrying')
        self.assertNotIn('sensitive diagnostic', json.dumps(state))
        self.runtime.email_failure = False
        self.monitor = Monitor(self.root, self.runtime, self.settings, clock=lambda: self.now)
        self.tick(10)
        self.assertEqual(len(self.runtime.emails), 0)
        self.tick(60)
        self.assertEqual(len(self.runtime.emails), 1)
        self.tick()
        self.assertEqual(len(self.runtime.emails), 1)

    def test_unconfigured_email_keeps_only_latest_verified_link_pending(self):
        write_json(self.monitor.email_file, {'enabled': False})
        self.assertEqual(self.tick()['email_status'], 'not_configured')
        self.runtime.current.update(url=SECOND, StartedAt='second-start')
        self.tick()
        write_json(self.monitor.email_file, {'enabled': True})
        self.tick()
        self.assertEqual([email['url'] for email in self.runtime.emails], [SECOND])

    def test_stale_pending_email_is_not_sent_during_an_outage(self):
        write_json(self.monitor.email_file, {'enabled': False})
        self.tick()
        write_json(self.monitor.email_file, {'enabled': True})
        self.runtime.public = False
        self.tick()
        self.assertEqual(self.runtime.emails, [])

    def test_status_service_is_read_only_and_rejects_foreign_hosts(self):
        self.tick()
        server = status_server(self.monitor, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with opener.open(base + '/api/status', timeout=3) as response:
                self.assertEqual(json.load(response)['url'], FIRST)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with opener.open(base, timeout=3) as response:
                self.assertIn(b'Cloudflare tunnel monitor', response.read())
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            for request, expected in (
                (urllib.request.Request(base + '/api/status', headers={'Host': 'foreign.example'}), 403),
                (urllib.request.Request(base + '/api/status', method='POST', data=b'{}'), 501),
                (urllib.request.Request(base + '/email.local.json'), 404),
            ):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(request, timeout=3)
                self.assertEqual(caught.exception.code, expected)
            self.assertEqual(self.runtime.restarts, 0)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime(Path('.'), Settings())

    def test_auth_gate_and_correct_login_page_are_both_required(self):
        for responses, expected in (([(401, b'{"detail":"Sign in to continue."}'), (200, b'<title>BOM Review Assistant</title>')], True),
                                    ([(200, b'{"status":"ok"}')], False),
                                    ([(401, b'proxy authentication')], False),
                                    ([(401, b'{"detail":"Sign in to continue."}'), (200, b'Wrong app')], False),
                                    ([(302, b'')], False)):
            with patch.object(self.runtime, 'request', side_effect=responses):
                self.assertEqual(self.runtime.protected_app(FIRST, public=True), expected)

    def test_only_valid_cloudflare_hostnames_from_current_container_are_probed(self):
        for value, expected in ((FIRST, FIRST), ('https://evil.example', None),
                                ('https://ok.trycloudflare.com.evil.example', None),
                                ('https://ok.trycloudflare.com/path', None), ('http://localhost', None)):
            with patch.object(self.runtime, 'docker', side_effect=[json.dumps({'Running': True}), json.dumps({'url': value})]):
                self.assertEqual(self.runtime.container()['url'], expected)

    def test_recovery_targets_only_the_pilot_tunnel(self):
        with patch.object(self.runtime, 'docker') as docker:
            self.runtime.restart()
            docker.assert_called_once_with('restart', '--timeout', '15', 'bom-pilot-tunnel-1')

    def test_outlook_uses_a_message_file_and_does_not_store_passwords_in_it(self):
        with tempfile.TemporaryDirectory() as directory:
            self.runtime.root = Path(directory)
            with patch('tunnel_monitor.subprocess.run') as run:
                def sent(command, **kwargs):
                    payload = json.loads(Path(command[-1]).read_text())
                    self.assertEqual(payload['recipient'], 'owner@example.test')
                    self.assertEqual(payload['url'], FIRST)
                    self.assertNotIn('password', payload)
                    self.assertNotIn('shell', kwargs)
                    return type('Result', (), {'returncode': 0})()
                run.side_effect = sent
                self.runtime.send_email({'delivery': 'outlook', 'sender': 'owner@example.test',
                                         'recipient': 'owner@example.test', 'password': 'never include'},
                                        {'id': 'test', 'url': FIRST, 'created_at': 'today'})
                self.assertFalse((Path(directory) / 'runtime/pilot/monitor/outlook-message.json').exists())


if __name__ == '__main__':
    unittest.main()
