import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeUrl, mediaUrl} from '../src/lib/validation.mjs';
test('normalizes supported URLs and strips tracking', () => {
  for (const kind of ['reel','reels','p','tv']) assert.equal(normalizeUrl(`http://m.instagram.com/${kind}/Ab_C-123/?igsh=abc#foo`), `https://www.instagram.com/${kind === 'reels' ? 'reel' : kind}/Ab_C-123/`);
});
test('rejects unsafe, malformed and unsupported URLs', () => {
  for (const value of ['', 'hello', 'https://evil.com/reel/ABCDE/', 'https://instagram.com.evil.com/reel/ABCDE/', 'https://instagram.com@evil.com/reel/ABCDE/', 'https://user@instagram.com/reel/ABCDE/', 'https://instagram.com:443/reel/ABCDE/', 'https://instagram.com/stories/ABCDE/', 'https://instagram.com/reel/x/', 'https://instagram.com/reel/ABCDE/extra', 'https://instagram.com/a/../reel/ABCDE/', 'https://instagram.com/reel/%41BCDE/', 'https://instagram.com\\@evil.com/reel/ABCDE/', 'https://instagram.com/reel/AB CDE/']) assert.throws(() => normalizeUrl(value), value);
});
test('media URL must stay at the backend', () => {
  assert.equal(mediaUrl('/api/media/Ab_C-123?download=1', 'https://api.example.com'), 'https://api.example.com/api/media/Ab_C-123?download=1');
  for (const path of ['https://evil.com/api/media/abc', '//evil.com/api/media/abc', '/admin', 'javascript:alert(1)']) assert.throws(() => mediaUrl(path, 'https://api.example.com'));
});
