export function normalizeUrl(value) {
  const raw = value.trim();
  if (!raw) throw new Error('Paste an Instagram Reel or video-post URL.');
  if (raw.length > 1024 || /[\x00-\x20\x7f\\]/.test(raw)) throw new Error('Enter a complete, valid Instagram URL.');
  let parsed;
  try { parsed = new URL(raw); } catch { throw new Error('Enter a complete, valid Instagram URL.'); }
  // URL.port hides a default explicit port, so also inspect the supplied authority.
  const authority = raw.match(/^https?:\/\/([^/\?#]+)/i)?.[1] || '';
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || authority.includes(':') || authority.includes('@')) throw new Error('Use an HTTP or HTTPS Instagram URL without a port or credentials.');
  if (!['instagram.com', 'www.instagram.com', 'm.instagram.com'].includes(parsed.hostname)) throw new Error('Only Instagram links are supported.');
  const match = parsed.pathname.match(/^\/(reels?|p|tv)\/([A-Za-z0-9_-]{5,64})\/?$/);
  if (!match || /%|\/\.\.?\//.test(raw.split(/[?#]/)[0])) throw new Error('Use a link to one Reel or video post. Profiles, stories and collections are unsupported.');
  return `https://www.instagram.com/${match[1] === 'reels' ? 'reel' : match[1]}/${match[2]}/`;
}

export function mediaUrl(path, base) {
  const url = new URL(path, base);
  if (url.origin !== new URL(base).origin || !/^\/api\/media\/[A-Za-z0-9_-]+$/.test(url.pathname)) throw new Error('The backend returned an invalid media URL.');
  return url.href;
}
