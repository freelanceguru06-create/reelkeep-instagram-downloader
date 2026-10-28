import {normalizeUrl, mediaUrl} from '../lib/validation.mjs';

const form = document.querySelector<HTMLFormElement>('#download-form')!;
const input = document.querySelector<HTMLInputElement>('#instagram-url')!;
const submit = document.querySelector<HTMLButtonElement>('#submit')!;
const save = document.querySelector<HTMLButtonElement>('#save')!;
const paste = document.querySelector<HTMLButtonElement>('#paste')!;
const status = document.querySelector<HTMLDivElement>('#status')!;
const result = document.querySelector<HTMLElement>('#result')!;
const video = document.querySelector<HTMLVideoElement>('#preview')!;
const base = document.querySelector<HTMLElement>('[data-api-base]')!.dataset.apiBase!;
let downloadUrl = '';
let filename = '';
let processing = false;

function message(text: string, error = false) {
  status.hidden = false;
  status.textContent = text;
  status.classList.toggle('error', error);
}
function clearResult() {
  result.hidden = true;
  video.pause();
  video.removeAttribute('src');
  video.load();
  downloadUrl = '';
}
function busy(value: boolean) {
  processing = value;
  submit.disabled = value;
  input.disabled = value;
  paste.disabled = value;
  form.setAttribute('aria-busy', String(value));
  submit.querySelector('span')!.textContent = value ? 'Checking & preparing…' : 'Find my video';
}
paste.addEventListener('click', async () => {
  try {
    input.value = await navigator.clipboard.readText();
    input.dispatchEvent(new Event('input'));
    input.focus();
  } catch {
    message('Clipboard access is unavailable. Paste your link into the field using your device’s paste option.');
    input.focus();
  }
});
input.addEventListener('input', () => {
  clearResult();
  status.hidden = true;
  input.removeAttribute('aria-invalid');
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (processing) return;
  clearResult();
  let url: string;
  try { url = normalizeUrl(input.value); }
  catch (error) {
    message((error as Error).message, true);
    input.setAttribute('aria-invalid', 'true');
    input.focus();
    return;
  }
  if (!base) { message('The download backend is not configured for this deployment. Please try again after the service is connected.', true); return; }
  input.value = url;
  busy(true);
  message('Checking public access and preparing a verified video. A sleeping backend may take about a minute to wake.');
  try {
    const response = await fetch(`${base}/api/download`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url}), signal: AbortSignal.timeout(125000), credentials: 'omit'});
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The backend is waking up or unavailable. Please try again in a minute.');
    const data = await response.json();
    if (!response.ok || data.ok !== true) throw new Error(data.error?.message || `The request failed (HTTP ${response.status}).`);
    const media = data.media;
    if (media.content_type !== 'video/mp4' || !Number.isFinite(media.size_bytes) || media.size_bytes <= 0 || media.size_bytes > 50 * 1024 * 1024) throw new Error('The backend returned unsupported media information.');
    downloadUrl = mediaUrl(media.download_url, base);
    video.src = mediaUrl(media.preview_url, base);
    filename = /^instagram-[A-Za-z0-9_-]+\.mp4$/.test(media.filename) ? media.filename : 'instagram-video.mp4';
    document.querySelector('#video-title')!.textContent = media.title;
    document.querySelector('#video-details')!.textContent = `${media.width} × ${media.height} · ${Math.round(media.duration_seconds)} seconds · MP4${media.has_audio === false ? ' · No audio track provided' : ''}`;
    document.querySelector('#media-size')!.textContent = `${(media.size_bytes / 1024 / 1024).toFixed(1)} MiB`;
    result.hidden = false;
    message('Your video is ready. You can preview it or save the MP4.');
  } catch (error) {
    clearResult();
    const err = error as Error;
    message(err.name === 'TimeoutError' ? 'The request timed out. The backend may be waking up; try again later.' : err instanceof TypeError ? 'The backend could not be reached. Check your connection and try again later.' : err.message, true);
  } finally { busy(false); }
});
video.addEventListener('error', () => {
  if (video.getAttribute('src')) message('The preview could not load. The video may have expired or reached the delivery limit.', true);
});
save.addEventListener('click', async () => {
  if (!downloadUrl || save.disabled) return;
  save.disabled = true;
  message('Receiving the video file…');
  try {
    const response = await fetch(downloadUrl, {credentials: 'omit', signal: AbortSignal.timeout(180000)});
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.error?.message || `Download failed (HTTP ${response.status}).`);
    }
    if (response.headers.get('content-type')?.split(';')[0] !== 'video/mp4') throw new Error('The server did not return a video file.');
    const blob = await response.blob();
    const header = new Uint8Array(await blob.slice(0, 32).arrayBuffer());
    if (blob.size < 32 || blob.size > 50 * 1024 * 1024 || String.fromCharCode(...header.slice(4, 8)) !== 'ftyp') throw new Error('The received file is not a supported MP4.');
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    message('Video received. Your browser has been asked to save the MP4; check its downloads.');
  } catch (error) { message((error as Error).message || 'The download failed. Please try again.', true); }
  finally { save.disabled = false; }
});
