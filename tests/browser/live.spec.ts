import {test, expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {writeFile, stat} from 'node:fs/promises';

test('authorized real browser download, separate from deterministic fixtures', async ({page}) => {
  test.skip(!process.env.LIVE_INSTAGRAM_URL, 'Set LIVE_INSTAGRAM_URL to a link you are authorized to download.');
  test.setTimeout(180000);
  const started = Date.now();
  const response = await page.goto('/');
  const navigation = await page.evaluate(() => performance.getEntriesByType('navigation').map(e => ({duration:e.duration}))[0]);
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').fill(process.env.LIVE_INSTAGRAM_URL!);
  await page.getByLabel('PASTE YOUR INSTAGRAM LINK').press('Enter');
  await expect(page.getByRole('button', {name:'Download video'})).toBeVisible({timeout:130000});
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', {name:'Download video'}).click();
  const download = await downloadPromise;
  const target = '../../work/evidence/live-browser.mp4';
  await download.saveAs(target);
  const probe = JSON.parse(execFileSync('ffprobe',['-v','error','-show_format','-show_streams','-of','json',target],{encoding:'utf8'}));
  expect(probe.streams.some((s: {codec_type:string}) => s.codec_type === 'video')).toBeTruthy();
  expect(Number(probe.format.duration)).toBeGreaterThan(0);
  execFileSync('ffmpeg',['-v','error','-i',target,'-t','2','-f','null','-']);
  const video = page.locator('video');
  await video.evaluate((element: HTMLVideoElement) => element.play());
  await expect.poll(() => video.evaluate((element:HTMLVideoElement) => element.currentTime)).toBeGreaterThan(0);
  await video.evaluate((element:HTMLVideoElement) => element.pause());
  await page.screenshot({path:'../../work/evidence/live-browser.png',fullPage:true});
  await writeFile('../../work/evidence/live-browser.json', JSON.stringify({source:process.env.LIVE_INSTAGRAM_URL,frontend:page.url(),api:await page.locator('[data-api-base]').getAttribute('data-api-base'),http_status:response?.status(),browser:'installed desktop Google Chrome',bytes:(await stat(target)).size,elapsed_seconds:(Date.now()-started)/1000,navigation,probe,decode:'FFmpeg decoded first two seconds without error',playback:'browser currentTime advanced'},null,2));
});
