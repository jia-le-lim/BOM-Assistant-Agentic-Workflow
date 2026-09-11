"""Read-only local pilot gate checks. Never prints the password or BOM rows."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

from manage import read_env


def verify(root):
    credentials = read_env(root / 'pilot-login.local.env')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, headers=None, data=None):
        try:
            req = urllib.request.Request('http://127.0.0.1:13010' + path,
                                         data=data, headers=headers or {})
            with opener.open(req, timeout=15) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, b'', exc.headers

    for path in ('/', '/api/backend/health', '/api/backend/batches', '/_next/static/not-real.js'):
        assert request(path)[0] == 401, 'Anonymous request was not denied'
    assert request('/api/backend/health', {'X-Role': 'admin', 'X-User': 'root'}, b'{}')[0] == 401
    invalid = base64.b64encode(b'pilot:wrong-password').decode()
    assert request('/', {'Authorization': 'Basic ' + invalid})[0] == 401
    encoded = base64.b64encode((credentials['PILOT_USERNAME'] + ':' + credentials['PILOT_PASSWORD']).encode()).decode()
    headers = {'Authorization': 'Basic ' + encoded}
    status, body, response_headers = request('/api/backend/batches', headers)
    assert status == 200, 'Authenticated request failed'
    batches = json.loads(body)
    assert isinstance(batches, list)
    assert response_headers.get('Cache-Control') == 'no-store'
    assert request('/api/backend/health', {**headers, 'Origin': 'https://unrelated.example'}, b'{}')[0] == 403
    assert request('/api/backend/health', {**headers, 'Origin': 'https://127.0.0.1:13010'})[0] == 200
    assert request('/api/backend/health', {**headers, 'Origin': 'http://127.0.0.1:13010'})[0] == 200
    report = {'verified_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'local gateway only; does not verify Cloudflare connectivity',
              'anonymous_denied': True, 'wrong_password_denied': True,
              'forged_role_without_login_denied': True, 'cross_origin_denied': True,
              'authenticated_batch_count': len(batches)}
    (root / 'runtime/pilot/validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Local gate verified: anonymous/incorrect logins and cross-origin requests denied; '
          f'role headers cannot bypass login; authenticated access shows {len(batches)} batches.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-root', type=Path, default=Path('C:/ProgramData/BOM-Supabase'))
    verify(parser.parse_args().deployment_root)
