"""Offline account migration checks; no live accounts or Docker writes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pilot_users import render


class PilotAccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pilot = self.root / 'runtime/pilot'
        self.pilot.mkdir(parents=True)
        self.accounts = {'tester': {'password': 'private-test-password',
                                   'hash': '$2b$04$' + 'a' * 53,
                                   'email': 'Tester@example.test'}}
        self.saved = self.root / 'pilot-accounts.local.json'
        self.saved.write_text(json.dumps(self.accounts), encoding='utf-8')

    @patch('pilot_users.manage.run', return_value=b'Valid configuration')
    def test_preserves_credentials_but_mounts_only_hashes_and_aliases(self, run):
        render(self.root)
        mounted = json.loads((self.pilot / 'auth.json').read_text())
        self.assertEqual(mounted, [{'user': 'tester', 'hash': self.accounts['tester']['hash'],
                                   'email': 'tester@example.test'}])
        self.assertEqual(json.loads(self.saved.read_text()), self.accounts)
        self.assertNotIn('private-test-password', (self.pilot / 'Caddyfile').read_text())
        self.assertNotIn(self.accounts['tester']['hash'], (self.pilot / 'Caddyfile').read_text())
        self.assertEqual(run.call_count, 1)  # Existing bcrypt hashes are retained.
        self.assertIn('/api/backend/health', (self.root / 'Pilot.ps1').read_text())

    @patch('pilot_users.manage.run')
    def test_duplicate_email_is_rejected_before_replacing_gateway(self, run):
        self.accounts['other'] = dict(self.accounts['tester'])
        self.saved.write_text(json.dumps(self.accounts))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            render(self.root)
        run.assert_not_called()
        self.assertFalse((self.pilot / 'Caddyfile').exists())

    @patch('pilot_users.manage.run', side_effect=RuntimeError('Invalid config'))
    def test_failed_validation_preserves_existing_configuration(self, run):
        (self.pilot / 'Caddyfile').write_text('existing-gateway')
        with self.assertRaises(RuntimeError):
            render(self.root)
        self.assertEqual((self.pilot / 'Caddyfile').read_text(), 'existing-gateway')
        self.assertFalse((self.pilot / 'auth.json').exists())


if __name__ == '__main__':
    unittest.main()
