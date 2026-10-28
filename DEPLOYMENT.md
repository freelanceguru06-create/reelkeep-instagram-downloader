# Deployment — strict ₹0 experiment

No custom domain, paid plan, database, persistent disk, paid API or trial dependency is required. Do not enable billing or add a payment method for this experiment. Account eligibility must be verified in the actual dashboard before creating resources.

## 1. Publish the source repository

Use an authorized GitHub account. Source needed by Render: `Dockerfile`, `requirements.txt`, `.dockerignore`, `backend/`. The static frontend is separate. Never upload `.env`, virtual environments, node_modules, browser profiles, media or credentials.

## 2. Render Free backend

1. Open Render Dashboard → New → Web Service. Connect the source repository, or use its public Git repository URL without granting additional GitHub access.
2. Choose Docker runtime, repository root as root directory, `./Dockerfile`, a supported free region, **Free** instance type. Do not attach a disk, database or any paid add-on. Keep one instance; the Docker command starts exactly one worker.
3. Confirm the dashboard shows $0 recurring cost and no payment method/automatic paid upgrade. If account verification requires a billable plan, stop.
4. Set `ALLOWED_ORIGINS` to the actual frontend origin once known. For local testing against this cloud backend, temporarily include `http://127.0.0.1:4321`; remove it afterward. This variable is not secret.
5. Set health-check path `/health`. Deploy. The image installs FFprobe; a failed verifier check returns 503.
6. Record the real `https://…onrender.com` URL. Request `/health` and verify `ok: true`. Request `/openapi.json`; confirm `/api/download` and `/api/media/{token}` exist.
7. Test one authorized source link from the cloud, then retrieve and inspect actual media. Local results cannot prove cloud-IP access.

Free limits checked 28 October 2026: 750 running hours/workspace/calendar month, idle sleep after 15 minutes, startup about a minute, ephemeral filesystem and no free persistent disk. Hobby includes 5GB outbound bandwidth/month; payment-method accounts may be billed overage, while accounts without one are suspended at exhaustion. Render can suspend unusually high outbound service traffic. The account dashboard confirmed 500 included pipeline minutes, a $0 build-spend limit, no card on file, and a selectable Free instance with 0.1 CPU and 512MB RAM. The free Postgres 30-day offer is excluded.

The backend has been deployed at https://reelkeep-instagram-downloader.onrender.com. Two authorized Reel downloads passed cloud verification. Its current CORS allowlist contains `http://127.0.0.1:4321` for the verified local production preview. Add the actual Pages origin before using the hosted frontend. Cloudflare Pages remains undeployed pending account sign-in; the steps below are still required.

## 3. Cloudflare Pages

The Pages frontend uses no Functions/Workers. Build locally and directly upload `dist/`; no Python executes on Pages.

```powershell
pnpm install --frozen-lockfile
pnpm exec wrangler login
pnpm exec wrangler whoami
pnpm exec wrangler pages project create reelkeep --production-branch main
```

If the name is unavailable, choose a unique name and use it in every subsequent command. Record the generated Pages origin from Cloudflare rather than assuming a URL exists. Configure both values with your actual deployment origins:

```powershell
$env:PUBLIC_API_BASE = 'https://YOUR-REAL-BACKEND.onrender.com'
$env:SITE_URL = 'https://YOUR-REAL-PROJECT.pages.dev'
pnpm build
pnpm exec wrangler pages deploy dist --project-name YOUR-REAL-PROJECT --branch main
```

These uppercase values are placeholders, never reported as deployments. Alternatively, Pages Dashboard → Create application → Pages → Direct Upload, and upload the contents of `dist/`. Build the frontend with your actual backend origin first. Use only the generated pages.dev address.

For Git integration: framework Astro, build `pnpm build`, output `dist`, Node 24, with the same two public environment values and pnpm 11. The lockfile and build-script allowlist are included. Do not mix Direct Upload and Git integration assumptions; Cloudflare documents that switching project types may require a new project.

Update Render `ALLOWED_ORIGINS` to the exact pages.dev origin, redeploy the backend if needed, then verify browser CORS. `_headers` is emitted with the exact configured backend origin for CSP.

Free Pages limits checked: 500 Git builds/month, one concurrent build, 20-minute build deadline, 20,000 files and 25MiB per static asset. Videos never pass through Pages static storage. See [Cloudflare limits](https://developers.cloudflare.com/pages/platform/limits/), [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/), [Render Free](https://render.com/docs/free), and [Render bandwidth](https://render.com/docs/outbound-bandwidth).

## 4. Production acceptance gate

- Verify HTTPS and the real pages.dev main/privacy/use pages, JS/CSS/favicon, robots and canonical/sitemap.
- Submit an authorized Reel to the deployed backend. Record source errors verbatim; no credential/cookie workaround.
- Preview, click Download video, confirm nonzero video/mp4 bytes and safe filename, and run `ffprobe -v error -show_format -show_streams <download.mp4>`. Play it in a browser/player.
- Repeat representative testing with an authorized /p/ video if available. Test expired token, bad URLs, backend unavailable/cold start, timeouts, rate/concurrency limits and mobile viewport. Physical Android Chrome requires a real device.
- Inspect Render free-tier usage; no claim of unlimited downloads. A 20MiB full download uses about 20MiB outbound, and preview/ranges add traffic; 5GB permits only roughly 238 such transfers before overhead and other workspace usage.
- If cloud extraction fails, leave the exact error visible and mark real cloud downloads unverified. Do not declare the project production-ready.
