import re
from urllib.parse import urlsplit


class Problem(Exception):
    def __init__(self, code: str, message: str, status: int = 422, retry_after: int | None = None):
        self.code, self.message, self.status = code, message, status
        self.retry_after = retry_after
        super().__init__(message)


def normalize_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Problem('EMPTY_URL', 'Paste an Instagram Reel or video-post URL.')
    value = value.strip()
    if len(value) > 1024 or re.search(r'[\x00-\x20\x7f\\]', value):
        raise Problem('INVALID_URL', 'Enter a complete, valid Instagram URL.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise Problem('INVALID_URL', 'Enter a complete, valid Instagram URL.')
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or port is not None:
        raise Problem('INVALID_URL', 'Use an HTTP or HTTPS Instagram URL without a port or credentials.')
    if parsed.hostname not in ('instagram.com', 'www.instagram.com', 'm.instagram.com'):
        raise Problem('UNSUPPORTED_HOST', 'Only Instagram links are supported.')
    match = re.fullmatch(r'/(reels?|p|tv)/([A-Za-z0-9_-]{5,64})/?', parsed.path)
    if not match:
        raise Problem('UNSUPPORTED_URL', 'Use a link to one Reel or video post. Profiles, stories and collections are unsupported.')
    kind, shortcode = match.groups()
    return f'https://www.instagram.com/{"reel" if kind == "reels" else kind}/{shortcode}/'
