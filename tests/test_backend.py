import asyncio
import json
import socket
import time

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app, Entry
from backend.validation import Problem, normalize_url
from backend.worker import validate_media_url, public_addresses, inspect_media, fetch_media

VALID = 'https://www.instagram.com/reels/Ab_C-123/?igsh=test'


@pytest.mark.parametrize('kind', ['reel', 'reels', 'p', 'tv'])
def test_normalize(kind):
    assert normalize_url(f'http://m.instagram.com/{kind}/Ab_C-123/?tracking=1#frag') == f'https://www.instagram.com/{"reel" if kind == "reels" else kind}/Ab_C-123/'


@pytest.mark.parametrize('value', ['', 'foo', 'https://evil.com/reel/ABCDE/', 'https://instagram.com.evil.com/reel/ABCDE/', 'https://instagram.com@evil.com/reel/ABCDE/', 'https://user@instagram.com/reel/ABCDE/', 'https://instagram.com:443/reel/ABCDE/', 'https://instagram.com:bad/reel/ABCDE/', 'ftp://instagram.com/reel/ABCDE/', 'https://instagram.com/stories/ABCDE/', 'https://instagram.com/p/x/', 'https://instagram.com/reel/ABCDE/extra', 'https://instagram.com/a/../reel/ABCDE/', 'https://instagram.com/reel/%41BCDE/', 'https://instagram.com\\@evil.com/reel/ABCDE/', 'https://instagram.com/reel/AB CDE/', 'https://instagram.com/reel/ABCDE/\n'])
def test_reject(value):
    # Outer whitespace is normalized; embedded controls are prohibited.
    if value.endswith('\n'):
        assert normalize_url(value) == 'https://www.instagram.com/reel/ABCDE/'
    else:
        with pytest.raises(Problem):
            normalize_url(value)


@pytest.mark.parametrize('url', ['http://x.cdninstagram.com/video', 'https://cdninstagram.com.evil.com/x', 'https://127.0.0.1/x', 'https://user@x.fbcdn.net/x', 'https://x.fbcdn.net:443/x', 'file:///tmp/video', 'https://x.fbcdn.net/a\nb'])
def test_reject_media_host(url):
    with pytest.raises(Problem):
        validate_media_url(url)


def test_allow_cdn():
    assert validate_media_url('https://scontent.cdninstagram.com/video.mp4?key=1').hostname == 'scontent.cdninstagram.com'


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1', '224.0.0.1', 'ff02::1'])
def test_dns_private_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    with pytest.raises(Problem):
        public_addresses('x.fbcdn.net')


def test_dns_mixed_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(Problem):
        public_addresses('x.fbcdn.net')


async def forbidden(*args):
    raise AssertionError('Extractor must not be called')


def test_invalid_requests_never_extract():
    app = create_app(forbidden)
    with TestClient(app) as client:
        cases = [({'url': ''}, 422), ({'url': 'https://evil.com/'}, 422), ({'url': VALID, 'cookie': 'forbidden'}, 422), ({'url': 42}, 422), ([], 422)]
        for body, status in cases:
            response = client.post('/api/download', json=body)
            assert response.status_code == status
            assert response.json()['ok'] is False
        assert client.post('/api/download', content='{', headers={'Content-Type': 'application/json'}).status_code == 400
        assert client.post('/api/download', content='x').status_code == 415
        assert client.post('/api/download', content=b'x' * 2049, headers={'Content-Type': 'application/json'}).status_code == 413
        assert client.get('/api/media/nonexistent').status_code == 410


@pytest.mark.parametrize('code,status', [('EXTRACTION_FAILED', 502), ('SOURCE_RATE_LIMITED', 429), ('SOURCE_TIMEOUT', 504), ('MEDIA_TOO_LARGE', 413), ('UNSUPPORTED_MEDIA', 422)])
def test_exact_extraction_errors(code, status):
    async def fails(url, target):
        target.write_bytes(b'partial')
        raise Problem(code, 'Known deterministic failure', status)
    app = create_app(fails)
    with TestClient(app) as client:
        response = client.post('/api/download', json={'url': VALID})
        assert response.status_code == status
        assert response.json()['error']['code'] == code
        assert not list(app.state.store.directory.iterdir())
        assert not app.state.store.busy


def test_timeout_cleanup():
    async def timeout(url, target):
        target.write_bytes(b'partial')
        target.with_suffix('.video.mp4').write_bytes(b'partial video')
        target.with_suffix('.audio.m4a').write_bytes(b'partial audio')
        raise asyncio.TimeoutError
    app = create_app(timeout)
    with TestClient(app) as client:
        assert client.post('/api/download', json={'url': VALID}).status_code == 504
        assert not list(app.state.store.directory.iterdir())


def test_rate_and_busy_limits():
    async def fails(*args):
        raise Problem('EXTRACTION_FAILED', 'fixture', 502)
    app = create_app(fails)
    with TestClient(app) as client:
        app.state.store.busy = True
        assert client.post('/api/download', json={'url': VALID}).status_code == 503
        app.state.store.busy = False
        assert not app.state.store.global_calls
        for _ in range(5):
            assert client.post('/api/download', json={'url': VALID}).status_code == 502
        response = client.post('/api/download', json={'url': VALID})
        assert response.status_code == 429
        assert 3590 <= int(response.headers['retry-after']) <= 3600
        assert response.json()['error']['retry_after_seconds'] == int(response.headers['retry-after'])


def test_media_contract_ranges_expiry_and_cleanup():
    # Explicit synthetic transport fixture. This is never counted as Instagram extraction.
    fixture = b'\0\0\0\x20ftypisom' + bytes(range(100))
    async def fixture_extract(url, target):
        target.write_bytes(fixture)
        return {'title': 'Transport fixture', 'filename': 'instagram-Ab_C-123.mp4', 'size_bytes': len(fixture), 'content_type': 'video/mp4'}
    app = create_app(fixture_extract)
    with TestClient(app) as client:
        response = client.post('/api/download', json={'url': VALID})
        assert response.status_code == 200
        data = response.json()
        assert data['normalized_url'] == 'https://www.instagram.com/reel/Ab_C-123/'
        assert data['media']['size_bytes'] == len(fixture)
        url = data['media']['download_url']
        media = client.get(url)
        assert media.content == fixture
        assert media.headers['content-type'] == 'video/mp4'
        assert 'attachment;' in media.headers['content-disposition']
        partial = client.get(url, headers={'Range': 'bytes=4-7'})
        assert partial.status_code == 206 and partial.content == b'ftyp'
        assert next(iter(app.state.store.entries.values())).readers == 0
        entry = next(iter(app.state.store.entries.values()))
        entry.expires = time.monotonic() - 1
        assert client.get(url).status_code == 410
        assert not entry.path.exists()
    assert not app.state.store.directory.exists()


def test_cors_exact_origin():
    app = create_app(forbidden)
    with TestClient(app) as client:
        response = client.options('/api/download', headers={'Origin': 'http://127.0.0.1:4321', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:4321'
        response = client.options('/api/download', headers={'Origin': 'https://evil.com', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400
        assert 'access-control-allow-origin' not in response.headers


def test_html_cannot_pass_container_check(tmp_path):
    target = tmp_path / 'fake.mp4'
    target.write_text('<html>not media</html>')
    with pytest.raises(Problem, match='verification'):
        inspect_media(target)


def test_delivery_cap():
    app = create_app(forbidden)
    with TestClient(app) as client:
        path = app.state.store.directory / 'fixture.mp4'
        path.write_bytes(b'fixture')
        app.state.store.entries['test'] = Entry(path, {'filename': 'instagram-fixture.mp4'}, time.monotonic() + 600)
        app.state.store.delivered = 100 * 1024 * 1024
        assert client.get('/api/media/test').status_code == 429
