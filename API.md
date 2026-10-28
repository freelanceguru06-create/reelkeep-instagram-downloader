# API contract

API base is a separately configured HTTPS backend origin (`PUBLIC_API_BASE`). Development defaults to `http://127.0.0.1:8000`. An unconfigured production build shows backend unavailable and sends no extraction request. No cookies, authentication, browser credentials or paid API keys.

## POST /api/download

Content-Type: application/json; maximum actual body 2,048 bytes. Exactly one field: `{"url":"<authorized Instagram URL>"}`. Accepts HTTP/HTTPS on instagram.com, www.instagram.com and m.instagram.com, paths /reel/, /reels/, /p/, /tv/. Strips tracking query/fragment, upgrades HTTPS, canonicalizes reels to reel. Rejects userinfo, ports, backslashes, control characters, other hosts and URL types.

200 response only after downloading MP4 and inspecting it with FFprobe: `ok: true`, `normalized_url`, `processing_seconds`, `media` containing `title`, `filename`, `size_bytes`, `content_type`, `width`, `height`, `duration_seconds`, `video_codec`, `preview_url`, `download_url`, `expires_at`. Both media URLs are relative to the API origin, with an opaque random capability token. They are not Instagram CDN URLs. Do not share them with anyone who should not see the video. Files expire after at most ten minutes, may be replaced earlier by a newer job when inactive, and may be retained briefly for an active transfer. The two-file store is ephemeral.

Media also includes `has_audio`, `audio_codec` and `combined_streams`. Direct MP4 is preferred; where the source offers separate direct MP4 video and M4A audio, the worker downloads both through the same CDN/DNS checks and uses local FFmpeg stream copy before FFprobe verification. No re-encoding or network access by FFmpeg. Split-stream inputs share the file budget (with 1MiB reserved for output-container overhead) and processing deadline. Protected formats and streaming manifests remain unsupported. When a source genuinely offers video without audio, `has_audio` is false and the UI labels it. If source audio exists only in an unsupported format, extraction fails rather than silently stripping it.

Failure response: `{"ok":false,"error":{"code":"MACHINE_CODE","message":"Human-readable evidence"}}`. Statuses: 400 malformed JSON; 408 body not received within five seconds; 413 body/media too large; 415 non-JSON; 422 invalid URL/request or unsupported media; 429 request/source/delivery limit; 502 extraction, network, invalid media or unsafe destination; 503 busy/storage/verifier/backend unavailable; 504 timeout. A generic source extraction error remains EXTRACTION_FAILED, with the extractor message, unless explicit rate-limit/timeout evidence supports a narrower code.

## GET /api/media/{token}[?download=1]

Returns stored verified video/mp4 bytes with inline or attachment Content-Disposition and a safe filename. Supports HTTP byte ranges through Starlette. Unknown/expired tokens return 410 JSON. No arbitrary remote URLs or filesystem paths accepted. No directory listings. Content is no-store. Tokens and signed CDN URLs are not access-logged. A request reserves whole-file size against a 100MiB per-process UTC-day allowance even if requesting a range; this deliberately conservative quota includes preview requests. Restart resets limits and loses all files. This is not a provider billing cap.

## GET /health

200 `{"ok":true,"service":"reelkeep","verifier_available":true}` when FFprobe exists, otherwise 503. This checks local readiness, not Instagram availability. Interactive schema at /docs, machine schema at /openapi.json.

One process, one extraction job at a time; 55-second hard deadline; 50MiB/file; 2 retained files. On Render (`RENDER=true`, a documented platform variable), only the shared site-wide 20 admitted jobs/hour limit applies: its proxy peer is not treated as an end-user identity. Outside Render, an additional 5 admitted jobs/direct connection/hour applies. Forwarded headers are never trusted. Invalid, busy and storage-blocked requests are not charged; source failures after admission are charged. RATE_LIMITED includes a measured `Retry-After` header and `error.retry_after_seconds`, rather than always asserting one hour. When storage is full, the oldest inactive entry is evicted; if both files are actively sending, the request gets 503 without consuming quota. CORS is not authentication: the global limits apply to direct clients too.
