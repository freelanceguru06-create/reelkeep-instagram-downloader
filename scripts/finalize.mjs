import {writeFile, readdir, stat, readFile} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
const site = process.env.SITE_URL;
const base = process.env.PUBLIC_API_BASE;
await writeFile('dist/robots.txt', site ? `User-agent: *\nAllow: /\nSitemap: ${new URL('/sitemap.xml', site)}\n` : 'User-agent: *\nDisallow: /\n');
if (site) {
  const urls = ['/', '/privacy/', '/acceptable-use/'].map(path => `<url><loc>${new URL(path, site)}</loc></url>`).join('');
  await writeFile('dist/sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`);
}
// Restrict production requests to the actual API origin instead of a provider-wide wildcard.
if (base) {
  const headers = await readFile('dist/_headers', 'utf8');
  await writeFile('dist/_headers', headers.replaceAll('https://*.onrender.com', new URL(base).origin));
}
let total = 0; let gzip = 0; const files = [];
async function walk(dir) {
  for (const file of await readdir(dir)) {
    const path = `${dir}/${file}`; const info = await stat(path);
    if (info.isDirectory()) await walk(path);
    else { const data = await readFile(path); total += data.length; gzip += gzipSync(data).length; files.push({path, bytes: data.length}); }
  }
}
await walk('dist');
console.log(JSON.stringify({files: files.length, total_bytes: total, gzip_bytes_individual_files: gzip, assets: files}, null, 2));
