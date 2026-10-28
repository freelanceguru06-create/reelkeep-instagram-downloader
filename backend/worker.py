"""Isolated anonymous extraction. Never loads yt-dlp CLI config or browser cookies."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit

import yt_dlp

from backend.validation import Problem, normalize_url

MAX_BYTES = 50 * 1024 * 1024


def public_addresses(host):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise Problem('UNSAFE_MEDIA', 'The source returned an unsafe media destination.', 502)
    return addresses


def validate_media_url(url):
    try:
        p = urlsplit(url)
        valid = p.scheme == 'https' and p.port is None and not p.username and not p.password
    except ValueError:
        valid = False
    if not valid or not p.hostname or not any(p.hostname.endswith('.' + suffix) for suffix in ('cdninstagram.com', 'fbcdn.net')):
        raise Problem('UNSAFE_MEDIA', 'The source returned an unsupported media host.', 502)
    if re.search(r'[\x00-\x20\x7f\\]', url):
        raise Problem('UNSAFE_MEDIA', 'The source returned an unsafe media URL.', 502)
    return p


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # Connect to an already checked address; preserve hostname for TLS verification.
        address = public_addresses(self.host)[0]
        raw = socket.create_connection((address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def fetch_media(url, target):
    deadline = time.monotonic() + 35
    for _ in range(4):
        p = validate_media_url(url)
        conn = PinnedHTTPS(p.hostname, timeout=10, context=ssl.create_default_context())
        try:
            conn.request('GET', p.path + ('?' + p.query if p.query else ''), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'video/mp4'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise Problem('MEDIA_UNAVAILABLE', f'Media delivery returned HTTP {response.status}. The source link may have expired.', 502)
            content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if content_type not in ('video/mp4', 'application/octet-stream'):
                raise Problem('INVALID_MEDIA', 'The source did not return MP4 media bytes.', 502)
            length = response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
            total = 0
            with open(target, 'xb') as out:
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
                    if time.monotonic() > deadline:
                        raise Problem('EXTRACTION_TIMEOUT', 'The media download timed out. Try again later.', 504)
                    out.write(chunk)
            if length and total != int(length):
                raise Problem('INVALID_MEDIA', 'The media transfer was incomplete.', 502)
            with open(target, 'rb') as file:
                header = file.read(32)
            if total < 32 or header[4:8] != b'ftyp':
                raise Problem('INVALID_MEDIA', 'The source response is not an MP4 container.', 502)
            return total
        finally:
            conn.close()
    raise Problem('UNSAFE_MEDIA', 'Too many media redirects.', 502)


def inspect_media(target):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height:format=duration,format_name', '-of', 'json', str(target)], capture_output=True, text=True, timeout=8, check=True)
        info = json.loads(result.stdout)
    except FileNotFoundError:
        raise Problem('VERIFIER_UNAVAILABLE', 'FFprobe is required to verify video files on this backend.', 503)
    except (subprocess.SubprocessError, ValueError):
        raise Problem('INVALID_MEDIA', 'The downloaded file failed video-container verification.', 502)
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video or float(info.get('format', {}).get('duration', 0)) <= 0:
        raise Problem('INVALID_MEDIA', 'The file does not contain a playable video stream.', 502)
    return {'width': video.get('width'), 'height': video.get('height'), 'duration_seconds': float(info['format']['duration']), 'video_codec': video.get('codec_name')}


class QuietLog:
    def __init__(self):
        self.lines = []

    def debug(self, message):
        pass

    def warning(self, message):
        self.lines.append(str(message)[-1000:])

    def error(self, message):
        self.lines.append(str(message)[-1000:])


def extract(url, target):
    normalized = normalize_url(url)
    logger = QuietLog()
    options = {'quiet': True, 'no_warnings': True, 'logger': logger, 'skip_download': True,
               'noplaylist': True, 'socket_timeout': 10, 'retries': 0, 'extractor_retries': 0,
               'cachedir': False, 'usenetrc': False, 'allowed_extractors': ['Instagram'],
               'playlistend': 1, 'ignoreerrors': False, 'geo_bypass': False}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(normalized, download=False)
    except yt_dlp.utils.DownloadError as error:
        detail = re.sub(r'\x1b\[[0-9;]*m', '', str(error))[-1800:]
        if '429' in detail or 'rate-limit' in detail.lower() or 'rate limit' in detail.lower():
            raise Problem('SOURCE_RATE_LIMITED', detail, 429)
        if 'timed out' in detail.lower() or 'timeout' in detail.lower():
            raise Problem('SOURCE_TIMEOUT', detail, 504)
        # Preserve the extractor's evidence; do not assert that every failure is an access block.
        raise Problem('EXTRACTION_FAILED', detail, 502)
    if not info or info.get('_type') in ('playlist', 'multi_video'):
        raise Problem('UNSUPPORTED_MEDIA', 'Only one video per link is supported; carousel posts are unsupported.', 422)
    formats = [f for f in info.get('formats', []) if f.get('ext') == 'mp4' and f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('protocol') in ('https', 'http') and f.get('url')]
    if not formats:
        raise Problem('UNSUPPORTED_MEDIA', 'No single-file MP4 video with audio was available. Photos, split streams and manifests are unsupported.', 422)
    chosen = max(formats, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    if (chosen.get('filesize') or 0) > MAX_BYTES:
        raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
    size = fetch_media(chosen['url'], target)
    verified = inspect_media(target)
    return {'title': str(info.get('title') or 'Instagram video')[:240], 'filename': f"instagram-{normalized.rstrip('/').split('/')[-1]}.mp4", 'size_bytes': size, 'content_type': 'video/mp4', **verified}


def main():
    try:
        request = json.loads(sys.stdin.read(2048))
        result = extract(request['url'], Path(request['target']))
        print(json.dumps({'ok': True, 'media': result}))
    except Problem as error:
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': error.message}, 'status': error.status}))
    except Exception as error:
        # No traceback or paths in public output.
        print(json.dumps({'ok': False, 'error': {'code': 'BACKEND_ERROR', 'message': f'Backend failed during extraction ({type(error).__name__}).'}, 'status': 502}))


if __name__ == '__main__':
    main()
