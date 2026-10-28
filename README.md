# Reelkeep

Lightweight Instagram public Reel/video-post downloader experiment. Astro static frontend; FastAPI backend; yt-dlp anonymous metadata extraction; bounded HTTPS CDN download; FFprobe verification; temporary files. No paid API, passwords, cookies, private-account support, proxy rotation or access-control bypass.

Live frontend: **https://reelkeep-dkg.pages.dev**. Backend: **https://reelkeep-instagram-downloader.onrender.com**. Both use free hosting; no custom domain or paid API. Local downloads were verified for three authorized Reels on 28 October 2026, and two representative Reels passed cloud download and FFprobe inspection. See the delivered verification report for the production browser results and remaining device/format limitations.

## Requirements

- Node.js 22.12+ and pnpm 11.
- Python 3.12+ and FFprobe on PATH (bundled with FFmpeg). FFprobe is required; the app refuses unverified media when missing.
- No Docker needed locally. The supplied Dockerfile installs FFmpeg/FFprobe for a Linux host.

## Local setup

From this directory, in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

In another terminal:

```powershell
pnpm install --frozen-lockfile
pnpm dev
```

Open http://127.0.0.1:4321. The development frontend calls http://127.0.0.1:8000. If `python` resolves to a Microsoft Store alias on Windows, use your actual Python executable. No virtual environment or machine-specific runtime path is committed.

## Deterministic checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
pnpm test
pnpm build
# Keep pnpm dev running; uses installed Google Chrome, two viewport sizes:
pnpm test:browser
```

Browser mocks and synthetic API transport fixtures are explicitly separate from real Instagram extraction. Live test, only for URLs you may download:

```powershell
python -m scripts.live_probe --output live-results.json '<authorized URL>'
```

The probe inspects actual downloaded media and deletes its temporary copy. Do not run bulk automated source tests.

## Configuration and deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) and [API.md](API.md). Backend `ALLOWED_ORIGINS` is a comma-separated list of exact frontend origins. Frontend `PUBLIC_API_BASE` is a public HTTPS backend origin, not a secret. `SITE_URL` is the actual HTTPS pages.dev origin. Without `SITE_URL`, the build stays noindex and omits canonical/sitemap. Without `PUBLIC_API_BASE`, production shows backend unavailable instead of using a fabricated endpoint.

The deployed backend permits `https://reelkeep-dkg.pages.dev`. To reproduce the production static build in PowerShell, set `PUBLIC_API_BASE` to `https://reelkeep-instagram-downloader.onrender.com` and `SITE_URL` to `https://reelkeep-dkg.pages.dev` before `pnpm build`. Use a local backend for local development; the hosted API intentionally restricts browser origins to the production site.

The static artifact is `dist/`; it never contains videos. Cloudflare Pages does not execute the Python backend. A Worker port of this native subprocess design is unsupported.

## Resource and security boundaries

- Allowlisted Instagram URL formats and CDN hosts; destination DNS must resolve only to global addresses. CDN connection pins the checked address while verifying the original TLS hostname, and revalidates redirects.
- No shell commands constructed from URLs. Extraction runs in an isolated child process with a 55-second deadline.
- 2KiB JSON request limit; 50MiB/file; one active extraction; two stored videos; expiry after at most ten minutes. A new job replaces the oldest inactive file when storage is full; active transfers are protected.
- Twenty admitted processing attempts/hour shared across the hosted site. Busy, invalid and storage-blocked requests do not consume that quota; an admitted extraction that the source rejects does. Errors report the actual remaining wait. A separate five-attempt client limit applies only outside Render, where the direct peer can identify a connection.
- 100MiB conservative delivery allowance per process per UTC day, including previews/ranges. No distributed database or durable quota. All state resets on process restart. Use exactly one Uvicorn worker. Proxy headers remain untrusted; Render's shared connection peer is deliberately not used as a visitor identity.
- Random temporary URLs are bearer capabilities, not user accounts. No URL history, analytics or application access logs. Infrastructure providers can still retain network metadata.
- Free backend sleep/restarts may delete pending videos. In-flight transfers hold a reader reference so cleanup waits.
- These application limits do not establish a provider billing cap. Deploy only on Free, with no payment method/paid upgrades for this experiment. Monitor host usage. A heavily used public downloader is unsuitable for these tiers.
