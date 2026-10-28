import io
import time

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app, Entry
from backend.validation import Problem
from backend import worker


class Response:
    def __init__(self, status=200, body=b'', headers=None):
        self.status = status
        self.stream = io.BytesIO(body)
        self.headers = headers or {}
    def getheader(self, key, default=None):
        return self.headers.get(key, default)
    def read(self, count):
        return self.stream.read(count)


def connection(monkeypatch, responses):
    class Connection:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return responses.pop(0)
        def close(self):
            pass
    monkeypatch.setattr(worker, 'PinnedHTTPS', Connection)


@pytest.mark.parametrize('body,headers,code', [
    (b'<html>error</html>', {'Content-Type': 'text/html'}, 'INVALID_MEDIA'),
    (b'not an mp4' * 10, {'Content-Type': 'video/mp4'}, 'INVALID_MEDIA'),
    (b'', {'Content-Type': 'video/mp4', 'Content-Length': str(51 * 1024 * 1024)}, 'MEDIA_TOO_LARGE'),
    (b'abc', {'Content-Type': 'video/mp4', 'Content-Length': '50'}, 'INVALID_MEDIA')
])
def test_reject_bad_media_delivery(monkeypatch, tmp_path, body, headers, code):
    connection(monkeypatch, [Response(body=body, headers=headers)])
    with pytest.raises(Problem) as error:
        worker.fetch_media('https://x.fbcdn.net/video.mp4', tmp_path / 'file.mp4')
    assert error.value.code == code


def test_redirect_to_private_host_is_rejected(monkeypatch, tmp_path):
    connection(monkeypatch, [Response(status=302, headers={'Location': 'https://127.0.0.1/private'})])
    with pytest.raises(Problem) as error:
        worker.fetch_media('https://x.fbcdn.net/video.mp4', tmp_path / 'file.mp4')
    assert error.value.code == 'UNSAFE_MEDIA'


def test_active_downloads_prevent_eviction_without_charging_a_request():
    async def forbidden(*args):
        raise AssertionError('storage limit must prevent work')
    app = create_app(forbidden)
    with TestClient(app) as client:
        for name in ('a', 'b'):
            path = app.state.store.directory / name
            path.write_bytes(b'fixture')
            app.state.store.entries[name] = Entry(path, {}, time.monotonic() + 100, readers=1)
        response = client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'})
        assert response.status_code == 503 and response.json()['error']['code'] == 'STORAGE_FULL'
        assert not app.state.store.global_calls


def test_third_video_evicts_oldest_inactive_file():
    async def fixture(url, target):
        target.write_bytes(b'transport fixture only')
        return {'filename': 'instagram-fixture.mp4', 'content_type': 'video/mp4', 'size_bytes': target.stat().st_size}
    app = create_app(fixture)
    with TestClient(app) as client:
        oldest = app.state.store.directory / 'oldest.mp4'
        recent = app.state.store.directory / 'recent.mp4'
        oldest.write_bytes(b'old fixture')
        recent.write_bytes(b'recent fixture')
        app.state.store.entries['oldest'] = Entry(oldest, {}, time.monotonic() + 100)
        app.state.store.entries['recent'] = Entry(recent, {}, time.monotonic() + 200)
        response = client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'})
        assert response.status_code == 200
        assert len(app.state.store.entries) == 2
        assert not oldest.exists() and recent.exists()
        assert client.get('/api/media/oldest').status_code == 410


def test_shared_render_proxy_has_no_five_request_visitor_bucket():
    calls = []
    async def fails(url, target):
        calls.append(url)
        raise Problem('EXTRACTION_FAILED', 'deterministic source fixture', 502)
    app = create_app(fails, client_limit=0)
    with TestClient(app) as client:
        for _ in range(20):
            assert client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'}).status_code == 502
        assert len(calls) == 20
        assert not app.state.store.clients
        response = client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'})
        assert response.status_code == 429
        assert 'The site has' in response.json()['error']['message']
        assert len(calls) == 20


def test_render_detection_does_not_trust_spoofed_forwarded_headers(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    app = create_app()
    with TestClient(app) as client:
        assert app.state.store.client_limit == 0
        app.state.store.global_calls.extend([time.monotonic() - 3500] * 20)
        response = client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'}, headers={'X-Forwarded-For': '8.8.8.8'})
        assert response.status_code == 429
        assert 90 <= int(response.headers['retry-after']) <= 100


def test_six_consecutive_render_jobs_keep_cache_bounded(monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    async def fixture(url, target):
        target.write_bytes(b'transport fixture only; not Instagram evidence')
        return {'filename': 'instagram-fixture.mp4', 'content_type': 'video/mp4', 'size_bytes': target.stat().st_size}
    app = create_app(fixture)
    with TestClient(app) as client:
        for _ in range(6):
            assert client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'}).status_code == 200
            assert len(app.state.store.entries) <= 2
            assert len(list(app.state.store.directory.iterdir())) <= 2
        assert len(app.state.store.global_calls) == 6


def test_concurrent_requests_do_not_start_two_jobs():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered = threading.Event()
    release = threading.Event()
    calls = []
    async def blocked(url, target):
        calls.append(url)
        entered.set()
        await asyncio.to_thread(release.wait, 3)
        raise Problem('EXTRACTION_FAILED', 'deterministic fixture', 502)
    app = create_app(blocked)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, '/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'})
        assert entered.wait(2)
        try:
            second = client.post('/api/download', json={'url': 'https://instagram.com/reel/FGHIJ/'})
            assert second.status_code == 503 and second.json()['error']['code'] == 'BUSY'
        finally:
            release.set()
        assert first.result().status_code == 502
        assert len(calls) == 1
