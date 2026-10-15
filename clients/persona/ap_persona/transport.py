from __future__ import annotations
import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def secure_url(url):
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('AP URL must be an origin without credentials or a path')
    loopback = parsed.hostname == 'localhost'
    try:
        loopback |= ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
        raise ValueError('use HTTPS or a loopback URL through an authenticated SSH tunnel')
    return url.rstrip('/')


class APIError(RuntimeError):
    def __init__(self, status, detail):
        self.status = status
        super().__init__(f'AP {status}: {detail}')


def request(origin, token, method, path, body=None):
    url = secure_url(origin) + '/api/local-persona' + path
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(url, data=raw, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect).open(req, timeout=3) as response:
            return json.load(response)
    except HTTPError as exc:
        # Server detail is bounded, and headers/request credentials are never
        # rendered. Refuse redirects so a bearer cannot follow to another host.
        try:
            detail = json.loads(exc.read(2048)).get('detail', 'request refused')
        except (ValueError, AttributeError):
            detail = 'request refused'
        raise APIError(exc.code, detail) from None
    except (URLError, TimeoutError, OSError):
        raise APIError(503, 'unreachable; captured records remain in the local spool') from None
