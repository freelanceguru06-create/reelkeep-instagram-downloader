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


def test_disk_limit_prevents_extraction():
    async def forbidden(*args):
        raise AssertionError('storage limit must prevent work')
    app = create_app(forbidden)
    with TestClient(app) as client:
        for name in ('a', 'b'):
            path = app.state.store.directory / name
            path.write_bytes(b'fixture')
            app.state.store.entries[name] = Entry(path, {}, time.monotonic() + 100)
        response = client.post('/api/download', json={'url': 'https://instagram.com/reel/ABCDE/'})
        assert response.status_code == 503 and response.json()['error']['code'] == 'STORAGE_FULL'
