"""Create individual pilot logins and render the gateway; passwords stay local."""
import argparse
import json
from pathlib import Path
import re
import secrets
import shutil

import manage

IMAGE = 'caddy:2-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648'
HERE = Path(__file__).resolve().parent


def render(root, users=()):
    accounts_file = root / 'pilot-accounts.local.json'
    if accounts_file.exists():
        accounts = json.loads(accounts_file.read_text(encoding='utf-8'))
    else:
        original = manage.read_env(root / 'pilot-login.local.env')
        accounts = {original['PILOT_USERNAME']: {'password': original['PILOT_PASSWORD']}}
    for user in users:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', user):
            raise ValueError('Use lowercase letters, digits and hyphens for pilot usernames.')
        if user not in accounts:
            accounts[user] = {'password': secrets.token_urlsafe(18)}
    lines = []
    for user, account in accounts.items():
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', user):
            raise ValueError('Invalid saved pilot username.')
        if not account.get('hash'):
            account['hash'] = manage.run(['docker', 'run', '--rm', '-i', IMAGE, 'caddy', 'hash-password'],
                input=(account['password'] + '\n').encode()).decode().strip()
        if not account['hash'].startswith('$2'):
            raise ValueError('Invalid saved password hash.')
        lines.append(f"            {user} {account['hash']}")
    config = (HERE / 'Caddyfile.pilot').read_text().replace('__PILOT_USERS__', '\n'.join(lines))
    pilot = root / 'runtime/pilot'
    candidate = pilot / 'Caddyfile.candidate'
    candidate.write_text(config, encoding='utf-8')
    manage.run(['docker', 'run', '--rm', '--mount',
                f'type=bind,source={candidate},target=/etc/caddy/Caddyfile,readonly',
                IMAGE, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile'])
    accounts_file.write_text(json.dumps(accounts, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(candidate, pilot / 'Caddyfile')
    candidate.unlink()
    print('Pilot accounts configured: ' + ', '.join(accounts))
    print(f'Private login details: {accounts_file}. Restart only the pilot gateway to apply.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-root', type=Path, default=Path('C:/ProgramData/BOM-Supabase'))
    parser.add_argument('--add', nargs='*', default=[])
    args = parser.parse_args()
    manage.ERROR_ROOT = args.deployment_root / 'runtime/logs'
    render(args.deployment_root, args.add)
