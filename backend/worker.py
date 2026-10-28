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
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
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


def remaining_time(deadline, maximum=10):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Problem('EXTRACTION_TIMEOUT', 'The media processing deadline was reached.', 504)
    return min(maximum, remaining)


def fetch_media(url, target, *, kind='video', max_bytes=MAX_BYTES, deadline=None):
    deadline = deadline or time.monotonic() + 35
    for _ in range(4):
        p = validate_media_url(url)
        conn = PinnedHTTPS(p.hostname, timeout=remaining_time(deadline), context=ssl.create_default_context())
        try:
            conn.request('GET', p.path + ('?' + p.query if p.query else ''), headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'audio/mp4' if kind == 'audio' else 'video/mp4'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200:
                raise Problem('MEDIA_UNAVAILABLE', f'Media delivery returned HTTP {response.status}. The source link may have expired.', 502)
            content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            allowed_types = ('audio/mp4', 'audio/x-m4a', 'video/mp4', 'application/octet-stream') if kind == 'audio' else ('video/mp4', 'application/octet-stream')
            if content_type not in allowed_types:
                raise Problem('INVALID_MEDIA', 'The source did not return MP4 media bytes.', 502)
            length = response.getheader('Content-Length')
            if length and (not length.isdigit() or int(length) > max_bytes):
                raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
            total = 0
            with open(target, 'xb') as out:
                while True:
                    if getattr(conn, 'sock', None) is not None:
                        conn.sock.settimeout(remaining_time(deadline))
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
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


def inspect_media(target, *, timeout=8):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'stream=codec_type,codec_name,width,height:format=duration,format_name', '-of', 'json', str(target)], capture_output=True, text=True, timeout=timeout, check=True)
        info = json.loads(result.stdout)
    except FileNotFoundError:
        raise Problem('VERIFIER_UNAVAILABLE', 'FFprobe is required to verify video files on this backend.', 503)
    except subprocess.TimeoutExpired:
        raise Problem('EXTRACTION_TIMEOUT', 'Video verification timed out.', 504)
    except (subprocess.SubprocessError, ValueError):
        raise Problem('INVALID_MEDIA', 'The downloaded file failed video-container verification.', 502)
    video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not video or float(info.get('format', {}).get('duration', 0)) <= 0:
        raise Problem('INVALID_MEDIA', 'The file does not contain a playable video stream.', 502)
    audio = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    return {'width': video.get('width'), 'height': video.get('height'), 'duration_seconds': float(info['format']['duration']), 'video_codec': video.get('codec_name'), 'has_audio': audio is not None, 'audio_codec': audio.get('codec_name') if audio else None}


def select_formats(info):
    if info.get('has_drm'):
        raise Problem('UNSUPPORTED_MEDIA', 'Protected media is unsupported.', 422)
    formats = [f for f in info.get('formats', []) if f.get('protocol') == 'https' and f.get('url') and not f.get('has_drm')]
    combined = [f for f in formats if f.get('ext') == 'mp4' and f.get('vcodec') != 'none' and f.get('acodec') != 'none']
    videos = [f for f in formats if f.get('ext') == 'mp4' and f.get('vcodec') not in ('none', None) and f.get('acodec') == 'none']
    # M4A URLs can omit codec metadata, just like progressive MP4 URLs. Verify bytes
    # and the final audio stream instead of requiring optional metadata to be present.
    audios = [f for f in formats if f.get('ext') in ('m4a', 'mp4') and f.get('vcodec') in ('none', None)
              and f.get('acodec') != 'none' and (f.get('ext') == 'm4a' or f.get('vcodec') == 'none')]
    if combined:
        chosen = max(combined, key=lambda f: (f.get('height') or 0, f.get('tbr') or 0))
    elif videos:
        # Prefer widely supported AVC; limit split-stream quality to 1080p when offered.
        candidates = [f for f in videos if 0 < (f.get('height') or 0) <= 1080] or videos
        chosen = max(candidates, key=lambda f: (str(f.get('vcodec')).startswith(('avc', 'h264')), f.get('height') or 0, f.get('tbr') or 0))
    else:
        raise Problem('UNSUPPORTED_MEDIA', 'No supported direct MP4 video was available. Photos, carousel posts, protected media and streaming manifests are unsupported.', 422)
    audio = max(audios, key=lambda f: f.get('tbr') or 0) if audios else None
    return chosen, audio


def merge_streams(video, audio, target, *, timeout=8):
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-v', 'error', '-xerror', '-n',
               '-protocol_whitelist', 'file,pipe', '-i', str(video),
               '-protocol_whitelist', 'file,pipe', '-i', str(audio),
               '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-movflags', '+faststart', str(target)]
    try:
        subprocess.run(command, capture_output=True, timeout=timeout, check=True)
    except FileNotFoundError:
        raise Problem('MERGER_UNAVAILABLE', 'FFmpeg is required to combine this video and audio.', 503)
    except subprocess.TimeoutExpired:
        raise Problem('EXTRACTION_TIMEOUT', 'Combining video and audio timed out.', 504)
    except subprocess.CalledProcessError:
        raise Problem('MERGE_FAILED', 'The source video and audio could not be combined into a verified MP4.', 502)


class QuietLog:
    def __init__(self):
        self.lines = []

    def debug(self, message):
        pass

    def warning(self, message):
        self.lines.append(str(message)[-1000:])

    def error(self, message):
        self.lines.append(str(message)[-1000:])


def extract(url, target, *, deadline=None):
    deadline = deadline or time.monotonic() + 50
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
    chosen, audio = select_formats(info)
    if (chosen.get('filesize') or 0) > MAX_BYTES:
        raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
    video_part = target.with_suffix('.video.mp4')
    audio_part = target.with_suffix('.audio.m4a')
    combined_streams = False
    completed = False
    try:
        video_size = fetch_media(chosen['url'], video_part, deadline=deadline)
        verified = inspect_media(video_part, timeout=remaining_time(deadline, 8))
        if not verified['has_audio'] and audio:
            audio_budget = MAX_BYTES - video_size - 1024 * 1024
            if audio_budget <= 0:
                raise Problem('MEDIA_TOO_LARGE', 'The combined video and audio exceed this prototype’s 50 MiB limit.', 413)
            fetch_media(audio['url'], audio_part, kind='audio', max_bytes=audio_budget, deadline=deadline)
            merge_streams(video_part, audio_part, target, timeout=remaining_time(deadline, 8))
            verified = inspect_media(target, timeout=remaining_time(deadline, 8))
            if not verified['has_audio']:
                raise Problem('INVALID_MEDIA', 'The combined file is missing its expected audio stream.', 502)
            combined_streams = True
        else:
            if not verified['has_audio'] and any(f.get('vcodec') == 'none' and f.get('acodec') not in (None, 'none') for f in info.get('formats', [])):
                raise Problem('UNSUPPORTED_MEDIA', 'The source audio requires an unsupported streaming format. It was not silently removed.', 422)
            video_part.replace(target)
        size = target.stat().st_size
        if not 0 < size <= MAX_BYTES:
            raise Problem('MEDIA_TOO_LARGE', 'This prototype supports videos up to 50 MiB.', 413)
        completed = True
    finally:
        video_part.unlink(missing_ok=True)
        audio_part.unlink(missing_ok=True)
        if not completed:
            target.unlink(missing_ok=True)
    return {'title': str(info.get('title') or 'Instagram video')[:240], 'filename': f"instagram-{normalized.rstrip('/').split('/')[-1]}.mp4", 'size_bytes': size, 'content_type': 'video/mp4', 'combined_streams': combined_streams, **verified}


def main():
    try:
        request = json.loads(sys.stdin.read(2048))
        result = extract(request['url'], Path(request['target']), deadline=request.get('deadline'))
        print(json.dumps({'ok': True, 'media': result}))
    except Problem as error:
        print(json.dumps({'ok': False, 'error': {'code': error.code, 'message': error.message}, 'status': error.status}))
    except Exception as error:
        # No traceback or paths in public output.
        print(json.dumps({'ok': False, 'error': {'code': 'BACKEND_ERROR', 'message': f'Backend failed during extraction ({type(error).__name__}).'}, 'status': 502}))


if __name__ == '__main__':
    main()
