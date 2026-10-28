"""Deterministic format/merge fixtures, never real Instagram download evidence."""
from pathlib import Path
import shutil
import subprocess

import pytest

from backend import worker
from backend.validation import Problem


def fmt(**changes):
    return {'url': 'https://video.cdninstagram.com/fixture', 'protocol': 'https', 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none', 'height': 720, **changes}


def test_selects_direct_split_streams_but_not_manifests_or_drm():
    video, audio = worker.select_formats({'formats': [fmt(), fmt(height=2160), fmt(vcodec='vp09', height=1080), fmt(ext='m4a', vcodec='none', acodec='mp4a.40.2', tbr=64)]})
    assert video['vcodec'] == 'avc1' and video['height'] == 720
    assert audio['ext'] == 'm4a'
    with pytest.raises(Problem):
        worker.select_formats({'formats': [fmt(protocol='m3u8_native'), fmt(has_drm=True)]})
    with pytest.raises(Problem):
        worker.select_formats({'has_drm': True, 'formats': [fmt()]})


def test_prefers_combined_mp4_and_accepts_unknown_codec_metadata():
    video, audio = worker.select_formats({'formats': [fmt(height=1080), fmt(vcodec=None, acodec=None, format_id='progressive')]})
    assert video['format_id'] == 'progressive'


@pytest.fixture
def streams(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/FFprobe required for real container fixture test')
    video, audio = tmp_path / 'fixture-video.mp4', tmp_path / 'fixture-audio.m4a'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=5', '-t', '1', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, timeout=10)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '1', '-vn', '-c:a', 'aac', str(audio)], check=True, timeout=10)
    return video, audio


def test_ffmpeg_combines_valid_video_and_audio_without_network(streams, tmp_path):
    video, audio = streams
    output = tmp_path / 'merged.mp4'
    worker.merge_streams(video, audio, output)
    info = worker.inspect_media(output)
    assert info['video_codec'] == 'h264'
    assert info['audio_codec'] == 'aac' and info['has_audio'] is True
    assert info['duration_seconds'] >= 1
    assert worker.inspect_media(video)['has_audio'] is False


def configure_extractor(monkeypatch, streams):
    video, audio = streams
    class Downloader:
        def __init__(self, options):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            return {'formats': [fmt(), fmt(url='https://audio.cdninstagram.com/fixture', ext='m4a', vcodec='none', acodec='mp4a.40.2')], 'title': 'Explicit synthetic fixture'}
    monkeypatch.setattr(worker.yt_dlp, 'YoutubeDL', Downloader)
    def fetch(url, target, **kwargs):
        source = audio if kwargs.get('kind') == 'audio' else video
        assert source.stat().st_size <= kwargs.get('max_bytes', worker.MAX_BYTES)
        shutil.copyfile(source, target)
        return target.stat().st_size
    monkeypatch.setattr(worker, 'fetch_media', fetch)


def test_split_extraction_produces_audio_and_cleans_parts(monkeypatch, streams, tmp_path):
    configure_extractor(monkeypatch, streams)
    target = tmp_path / 'job.mp4'
    result = worker.extract('https://instagram.com/p/ABCDE/', target)
    assert result['combined_streams'] is True and result['has_audio'] is True
    assert not target.with_suffix('.video.mp4').exists()
    assert not target.with_suffix('.audio.m4a').exists()
    assert target.is_file()


def test_merge_failure_cleans_all_partial_files(monkeypatch, streams, tmp_path):
    configure_extractor(monkeypatch, streams)
    def fail(video, audio, target, **kwargs):
        target.write_bytes(b'partial output')
        raise Problem('MERGE_FAILED', 'fixture', 502)
    monkeypatch.setattr(worker, 'merge_streams', fail)
    target = tmp_path / 'failed.mp4'
    with pytest.raises(Problem):
        worker.extract('https://instagram.com/p/ABCDE/', target)
    assert not target.exists()
    assert not target.with_suffix('.video.mp4').exists()
    assert not target.with_suffix('.audio.m4a').exists()
