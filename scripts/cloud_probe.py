"""Small opt-in cloud verification. No credentials; no source evasion or retries."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time

import httpx

from backend.worker import inspect_media


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('urls', nargs='+')
    args = parser.parse_args()
    rows = []
    with httpx.Client(timeout=125, follow_redirects=False) as client:
        health = client.get(args.base + '/health')
        print(json.dumps({'health_status': health.status_code, 'body': health.text[:500]}), flush=True)
        if health.status_code != 200:
            raise SystemExit('Backend not ready; no live extraction requests sent.')
        for url in args.urls:
            started = time.monotonic()
            response = client.post(args.base + '/api/download', json={'url': url}, headers={'Origin': 'http://127.0.0.1:4321'})
            row = {'source': url, 'environment': 'deployed Render Free backend; source requests originate in cloud', 'base': args.base, 'tested_at': datetime.now(timezone.utc).isoformat(), 'http_status': response.status_code, 'cors_origin': response.headers.get('access-control-allow-origin'), 'success': False}
            try:
                payload = response.json()
            except ValueError:
                payload = {'error': {'code': 'NON_JSON_RESPONSE', 'message': response.text[:1000]}}
            if response.status_code == 200 and payload.get('ok'):
                media = payload['media']
                row['media'] = {key: value for key, value in media.items() if key not in ('preview_url', 'download_url')}
                with tempfile.TemporaryDirectory(prefix='reelkeep-cloud-test-') as directory:
                    target = Path(directory) / 'cloud.mp4'
                    with client.stream('GET', args.base + media['download_url']) as download:
                        row['download_status'] = download.status_code
                        row['content_type'] = download.headers.get('content-type')
                        if download.status_code == 200 and row['content_type'] == 'video/mp4':
                            total = 0
                            with target.open('wb') as file:
                                for chunk in download.iter_bytes():
                                    total += len(chunk)
                                    if total > 50 * 1024 * 1024:
                                        raise RuntimeError('Cloud response exceeded prototype media limit')
                                    file.write(chunk)
                            row['downloaded_bytes'] = total
                            row['verified'] = inspect_media(target)
                            row['success'] = True
            else:
                row['error'] = payload.get('error', payload)
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            rows.append(row)
            Path(args.output).write_text(json.dumps(rows, indent=2), encoding='utf-8')
            print(json.dumps(row), flush=True)
            if url != args.urls[-1]:
                time.sleep(3)


if __name__ == '__main__':
    main()
