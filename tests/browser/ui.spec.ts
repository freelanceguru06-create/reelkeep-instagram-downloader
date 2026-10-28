import {test, expect} from '@playwright/test';

test('accessible form, validation, keyboard and responsive layout', async ({page}) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/Reelkeep/);
  await page.getByRole('button', {name: 'Find my video'}).click();
  await expect(page.getByRole('status')).toContainText('Paste an Instagram');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill('https://evil.com/reel/ABCDE/');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').press('Enter');
  await expect(page.getByRole('status')).toContainText('Only Instagram');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByText('Which links can I use?').click();
  await expect(page.getByText('Links to one public Instagram Reel')).toBeVisible();
});

test('source errors are shown honestly; stale result hidden', async ({page}) => {
  await page.route('**/api/download', route => route.fulfill({status: 502, contentType: 'application/json', body: JSON.stringify({ok: false, error: {code: 'EXTRACTION_FAILED', message: 'Deterministic source failure fixture'}})}));
  await page.goto('/');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill('https://www.instagram.com/reels/DfAO9K-I0ip/');
  await page.getByRole('button', {name: 'Find my video'}).click();
  await expect(page.getByRole('status')).toContainText('Deterministic source failure fixture');
  await expect(page.getByRole('button', {name: 'Download video'})).toBeHidden();
  await expect(page.getByRole('button', {name: 'Find my video'})).toBeEnabled();
});

test('unreachable backend and non-JSON cold-start response', async ({page}) => {
  await page.route('**/api/download', route => route.abort());
  await page.goto('/');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill('https://www.instagram.com/reel/DfAO9K-I0ip/');
  await page.getByRole('button', {name: 'Find my video'}).click();
  await expect(page.getByRole('status')).toContainText('could not be reached');
  await page.unroute('**/api/download');
  await page.route('**/api/download', route => route.fulfill({status: 503, contentType: 'text/html', body: '<h1>Waking up</h1>'}));
  await page.getByRole('button', {name: 'Find my video'}).click();
  await expect(page.getByRole('status')).toContainText('waking up or unavailable');
});

test('rejects an HTML download in a deterministic success-response fixture', async ({page}) => {
  await page.route('**/api/download', route => route.fulfill({contentType: 'application/json', body: JSON.stringify({ok: true, media: {title: 'Test fixture, not Instagram evidence', filename: 'instagram-fixture.mp4', size_bytes: 100, content_type: 'video/mp4', width: 320, height: 240, duration_seconds: 1, preview_url: '/api/media/fixture', download_url: '/api/media/fixture?download=1'}})}));
  await page.route('**/api/media/fixture?download=1', route => route.fulfill({contentType: 'text/html', body: '<html>error</html>'}));
  await page.goto('/');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill('https://www.instagram.com/reel/DfAO9K-I0ip/');
  await page.getByRole('button', {name: 'Find my video'}).click();
  await page.getByRole('button', {name: 'Download video'}).click();
  await expect(page.getByRole('status')).toContainText('did not return a video file');
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill('changed');
  await expect(page.getByRole('button', {name: 'Download video'})).toBeHidden();
});

test('privacy and use routes render', async ({page}) => {
  for (const path of ['/privacy/', '/acceptable-use/']) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole('heading', {level: 1})).toBeVisible();
  }
});
