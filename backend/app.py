import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware

from backend.validation import Problem, normalize_url

ROOT = Path(__file__).resolve().parents[1]
TTL = 600
MAX_BYTES = 50 * 1024 * 1024
DAILY_BYTES = 100 * 1024 * 1024


@dataclass
class Entry:
    path: Path
    media: dict
    expires: float
    readers: int = 0


class State:
    def __init__(self, client_limit=5):
        self.directory = Path(tempfile.mkdtemp(prefix='reelkeep-'))
        self.entries = {}
        self.busy = False
        self.clients = defaultdict(deque)
        self.global_calls = deque()
        self.client_limit = client_limit
        self.day = int(time.time() // 86400)
        self.delivered = 0

    def prune(self):
        now = time.monotonic()
        for token, entry in list(self.entries.items()):
            if entry.expires <= now and not entry.readers:
                entry.path.unlink(missing_ok=True)
                del self.entries[token]
        for key, calls in list(self.clients.items()):
            while calls and calls[0] < now - 3600:
                calls.popleft()
            if not calls:
                del self.clients[key]

    def admit(self, client):
        """Charge only jobs that can start. A shared proxy is not a visitor identity."""
        self.prune()
        if self.busy:
            raise Problem('BUSY', 'Another video is processing. Try again shortly.', 503)
        now = time.monotonic()
        calls = None
        if self.client_limit:
            key = hashlib.sha256(client.encode()).hexdigest()
            if key not in self.clients and len(self.clients) >= 1000:
                raise Problem('BUSY', 'The service is busy. Try again shortly.', 503)
            calls = self.clients[key]
        while self.global_calls and self.global_calls[0] < now - 3600:
            self.global_calls.popleft()
        waits = []
        if calls is not None and len(calls) >= self.client_limit:
            waits.append(calls[0] + 3600 - now)
        if len(self.global_calls) >= 20:
            waits.append(self.global_calls[0] + 3600 - now)
        if waits:
            seconds = max(1, math.ceil(max(waits)))
            minutes = max(1, math.ceil(seconds / 60))
            scope = 'The site has' if len(self.global_calls) >= 20 else 'Your connection has'
            raise Problem('RATE_LIMITED', f'{scope} reached its hourly processing limit. Try again in about {minutes} minute(s).', 429, retry_after=seconds)
        if len(self.entries) >= 2:
            inactive = [(token, entry) for token, entry in self.entries.items() if not entry.readers]
            if not inactive:
                raise Problem('STORAGE_FULL', 'Both video files are in use. Try again after the downloads finish.', 503)
            # Keep disk use bounded while allowing a third video without a ten-minute lockout.
            token, entry = min(inactive, key=lambda item: item[1].expires)
            try:
                entry.path.unlink(missing_ok=True)
            except OSError:
                raise Problem('STORAGE_FULL', 'Temporary storage is busy. Try again shortly.', 503)
            del self.entries[token]
        if calls is not None:
            calls.append(now)
        self.global_calls.append(now)
        self.busy = True


async def isolated_extract(url, target):
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'backend.worker', cwd=ROOT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(json.dumps({'url': url, 'target': str(target)}).encode()), 55)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode or len(stdout) > 16384:
        raise Problem('BACKEND_ERROR', 'The extraction worker could not complete.', 502)
    try:
        payload = json.loads(stdout)
    except ValueError:
        raise Problem('BACKEND_ERROR', 'The extraction worker returned an invalid response.', 502)
    if not payload.get('ok'):
        error = payload.get('error', {})
        raise Problem(error.get('code', 'EXTRACTION_FAILED'), error.get('message', 'Extraction failed.'), payload.get('status', 502))
    return payload['media']


def create_app(extractor=isolated_extract, *, client_limit=None):
    # Render's connection peer is its proxy. Do not give all visitors a shared five-job bucket
    # or trust caller-supplied forwarding headers. The site-wide twenty-job cap still applies.
    if client_limit is None:
        client_limit = 0 if os.environ.get('RENDER') == 'true' else 5
    state = State(client_limit=client_limit)

    async def janitor():
        while True:
            await asyncio.sleep(30)
            state.prune()

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(janitor())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        shutil.rmtree(state.directory, ignore_errors=True)

    api = FastAPI(title='Reelkeep API', version='1.0.0', lifespan=lifespan)
    api.state.store = state

    @api.exception_handler(Problem)
    async def problem_handler(request, error):
        detail = {'code': error.code, 'message': error.message}
        headers = {}
        if error.retry_after is not None:
            detail['retry_after_seconds'] = error.retry_after
            headers['Retry-After'] = str(error.retry_after)
        return JSONResponse({'ok': False, 'error': detail}, status_code=error.status, headers=headers)

    @api.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @api.get('/health')
    async def health():
        ready = shutil.which('ffprobe') is not None
        return JSONResponse({'ok': ready, 'service': 'reelkeep', 'verifier_available': ready}, status_code=200 if ready else 503)

    @api.post('/api/download')
    async def download(request: Request):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise Problem('INVALID_CONTENT_TYPE', 'Send a JSON request body.', 415)
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 2048:
                        raise Problem('REQUEST_TOO_LARGE', 'The request body exceeds 2 KiB.', 413)
        except TimeoutError:
            raise Problem('REQUEST_TIMEOUT', 'The request body was not received within five seconds.', 408)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise Problem('INVALID_JSON', 'The request body must be valid JSON.', 400)
        if not isinstance(payload, dict) or set(payload) != {'url'} or not isinstance(payload['url'], str):
            raise Problem('INVALID_REQUEST', 'Send one string field named url.', 422)
        url = normalize_url(payload['url'])
        token = secrets.token_urlsafe(32)
        target = state.directory / f'{token}.mp4'
        state.admit(request.client.host if request.client else 'unknown')
        started = time.monotonic()
        try:
            media = await extractor(url, target)
            if not target.is_file() or not 0 < target.stat().st_size <= MAX_BYTES:
                raise Problem('INVALID_MEDIA', 'The backend did not produce a supported video file.', 502)
            state.entries[token] = Entry(target, media, time.monotonic() + TTL)
            return {'ok': True, 'normalized_url': url, 'media': {**media,
                'preview_url': f'/api/media/{token}', 'download_url': f'/api/media/{token}?download=1',
                'expires_at': datetime.fromtimestamp(time.time() + TTL, timezone.utc).isoformat()},
                'processing_seconds': round(time.monotonic() - started, 3)}
        except asyncio.TimeoutError:
            raise Problem('EXTRACTION_TIMEOUT', 'Extraction exceeded the 55-second limit. Try again later.', 504)
        finally:
            state.busy = False
            if token not in state.entries:
                target.unlink(missing_ok=True)

    @api.get('/api/media/{token}')
    async def media(token: str, request: Request):
        state.prune()
        entry = state.entries.get(token)
        if not entry or entry.expires <= time.monotonic():
            raise Problem('MEDIA_EXPIRED', 'This video is unavailable or expired. Submit the original link again.', 410)
        today = int(time.time() // 86400)
        if state.day != today:
            state.day, state.delivered = today, 0
        size = entry.path.stat().st_size
        # Reserve a whole-file allowance even for ranges, preventing range-request quota evasion.
        if state.delivered + size > DAILY_BYTES:
            raise Problem('DELIVERY_LIMIT', 'The prototype has reached its daily delivery limit.', 429)
        state.delivered += size
        entry.readers += 1
        entry.expires = max(entry.expires, time.monotonic() + 60)
        class ManagedFile(FileResponse):
            async def __call__(self, scope, receive, send):
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    entry.readers -= 1
        return ManagedFile(entry.path, media_type='video/mp4', filename=entry.media['filename'],
            content_disposition_type='attachment' if request.query_params.get('download') == '1' else 'inline')

    origins = [o.strip() for o in os.environ.get('ALLOWED_ORIGINS', 'http://localhost:4321,http://127.0.0.1:4321').split(',') if o.strip()]
    api.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
        allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Range'], expose_headers=['Content-Disposition', 'Content-Length', 'Content-Range', 'Retry-After'])
    return api


app = create_app()
