import {defineConfig} from 'astro/config';
const site = process.env.SITE_URL;
if (site) {
  const parsed = new URL(site);
  if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('.pages.dev') || parsed.pathname !== '/' || parsed.search || parsed.hash) throw new Error('SITE_URL must be your HTTPS pages.dev origin.');
}
export default defineConfig({output: 'static', site, devToolbar: {enabled: false}, build: {inlineStylesheets: 'never'}});
