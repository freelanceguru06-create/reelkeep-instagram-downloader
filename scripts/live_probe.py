"""Opt-in live test. Only supply links you own or have permission to download."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time

from backend.app import isolated_extract
from backend.validation import Problem, normalize_url


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('urls', nargs='+')
    args = parser.parse_args()
    rows = []
    for url in args.urls:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix='reelkeep-test-') as directory:
            target = Path(directory) / 'video.mp4'
            row = {'url': url, 'normalized_url': normalize_url(url), 'tested_at': datetime.now(timezone.utc).isoformat(), 'environment': 'local Windows host; not a cloud server', 'authentication': 'none', 'success': False}
            try:
                row['media'] = await isolated_extract(row['normalized_url'], target)
                row['success'] = True
                row['verification'] = 'real source bytes inspected by FFprobe; temporary file deleted after test'
            except Problem as error:
                row['error'] = {'code': error.code, 'message': error.message, 'http_status': error.status}
            except asyncio.TimeoutError:
                row['error'] = {'code': 'EXTRACTION_TIMEOUT', 'message': 'Worker exceeded 55 seconds', 'http_status': 504}
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            rows.append(row)
            print(json.dumps(row), flush=True)
            Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')
        if url != args.urls[-1]:
            await asyncio.sleep(3)


if __name__ == '__main__':
    asyncio.run(main())
