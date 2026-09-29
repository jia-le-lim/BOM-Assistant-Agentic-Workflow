"""Read-only local pilot gate checks. Never prints the password or BOM rows."""
import argparse
import http.cookiejar
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

from manage import read_env


def verify(root):
    credentials = read_env(root / 'pilot-login.local.env')
    base = 'http://127.0.0.1:13010'
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                        urllib.request.HTTPCookieProcessor(jar), NoRedirect())

    def request(path, headers=None, data=None):
        try:
            req = urllib.request.Request(base + path,
                                         data=data, headers=headers or {})
            with opener.open(req, timeout=15) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, b'', exc.headers

    status, _, headers = request('/')
    assert status == 303 and headers['Location'].startswith('/login'), 'Page did not redirect to login'
    assert request('/login')[0] == 200, 'Login form is not public'
    for path in ('/api/backend/health', '/api/backend/batches', '/api/pilot-session'):
        status, _, headers = request(path)
        assert status == 401, 'Anonymous API request was not denied'
        assert not headers.get('WWW-Authenticate'), 'Browser credential popup can still appear'
    assert request('/api/backend/health', {'X-Role': 'admin', 'X-User': 'root'}, b'{}')[0] == 401
    login_headers = {'Origin': base, 'Content-Type': 'application/json'}
    invalid = json.dumps({'identifier': credentials['PILOT_USERNAME'], 'password': 'wrong-password'}).encode()
    assert request('/api/auth/login', login_headers, invalid)[0] == 401
    valid = json.dumps({'identifier': credentials['PILOT_USERNAME'], 'password': credentials['PILOT_PASSWORD']}).encode()
    assert request('/api/auth/login', login_headers, valid)[0] == 200, 'Form login failed'
    assert any(cookie.name == 'bom-pilot-session' and cookie.has_nonstandard_attr('HttpOnly') for cookie in jar)
    headers = {'X-Role': 'engineer', 'X-User': 'forged-user'}
    status, body, _ = request('/api/pilot-session', headers)
    assert status == 200 and json.loads(body)['user'] == credentials['PILOT_USERNAME']
    assert json.loads(body)['role'] == 'admin', 'Signed-in account does not have Administrator access'
    status, body, response_headers = request('/api/backend/batches', headers)
    assert status == 200, 'Authenticated request failed'
    batches = json.loads(body)
    assert isinstance(batches, list)
    assert 'no-store' in response_headers.get('Cache-Control', '')
    assert all(batch['uploaded_by'] == credentials['PILOT_USERNAME'] for batch in batches)
    assert request('/api/backend/health', {**headers, 'Origin': 'https://unrelated.example'}, b'{}')[0] == 403
    assert request('/api/backend/health', {**headers, 'Origin': 'https://127.0.0.1:13010'})[0] == 200
    assert request('/api/backend/health', {**headers, 'Origin': 'http://127.0.0.1:13010'})[0] == 200
    assert request('/api/auth/logout', {'Origin': 'https://unrelated.example'}, b'')[0] == 403
    saved_cookie = '; '.join(f'{cookie.name}={cookie.value}' for cookie in jar)
    assert request('/api/auth/logout', {'Origin': base}, b'')[0] == 200
    assert request('/api/backend/health', {'Cookie': saved_cookie})[0] == 401, 'Logout did not revoke the session'
    report = {'verified_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'local gateway only; does not verify Cloudflare connectivity',
              'anonymous_denied': True, 'wrong_password_denied': True,
              'form_login_verified': True, 'logout_revokes_session': True,
              'administrator_access': True,
              'no_browser_popup': True,
              'forged_role_without_login_denied': True, 'cross_origin_denied': True,
              'authenticated_batch_count': len(batches)}
    (root / 'runtime/pilot/validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Local gate verified: anonymous/incorrect logins and cross-origin requests denied; '
          f'role headers cannot bypass login; authenticated access shows {len(batches)} batches.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment-root', type=Path, default=Path('C:/ProgramData/BOM-Supabase'))
    verify(parser.parse_args().deployment_root)
