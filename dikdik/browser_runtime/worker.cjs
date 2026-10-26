// Original Dikdik browser worker. Only runs inside the isolated container.
const readline = require('node:readline');
const { chromium } = require('playwright-core');
const lines = readline.createInterface({ input: process.stdin });
const pending = new Map();
let begin, sequence = 0;
const initial = new Promise(resolve => { begin = resolve; });
lines.on('line', line => {
  try {
    const message = JSON.parse(line);
    if (begin) { const ready = begin; begin = null; ready(message); }
    else if (pending.has(message.id)) { const resolve = pending.get(message.id); pending.delete(message.id); resolve(message); }
  } catch { process.exit(2); }
});
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const timer = setTimeout(() => process.exit(3), 60000);

(async () => {
  let browser;
  try {
    const config = await initial;
    browser = await chromium.launch({ headless: true, chromiumSandbox: false,
      args: ['--disable-dev-shm-usage', '--disable-background-networking', '--disable-extensions'] });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 },
      serviceWorkers: 'block', acceptDownloads: false, permissions: [], javaScriptEnabled: true });
    await context.routeWebSocket('**/*', socket => socket.close());
    await context.route('**/*', async route => {
      const request = route.request();
      if (request.method() !== 'GET' || !/^https?:\/\//.test(request.url()) || ++sequence > 64) return route.abort();
      const id = sequence;
      const reply = new Promise(resolve => pending.set(id, resolve));
      send({ kind: 'request', id, url: request.url() });
      const response = await reply;
      if (response.error) return route.abort();
      await route.fulfill({ status: response.status, headers: response.headers, body: Buffer.from(response.body, 'base64') });
    });
    const page = await context.newPage();
    page.on('dialog', dialog => dialog.dismiss());
    context.on('page', popup => { if (popup !== page) popup.close(); });
    send({ kind: 'ready' });
    const response = await page.goto(config.url, { waitUntil: 'domcontentloaded', timeout: 40000 });
    if (!response || response.status() >= 400) throw new Error('Page failed');
    await page.screenshot({ type: 'png', timeout: 10000 }).then(png => {
      if (png.length > 2 * 1024 * 1024) throw new Error('Screenshot too large');
      return Promise.all([page.title(), page.locator('body').innerText({ timeout: 3000 })]).then(([title, text]) => {
        send({ kind: 'result', url: page.url(), title: title.slice(0, 256), text: text.slice(0, 12000), png: png.toString('base64') });
      });
    });
  } catch {
    send({ kind: 'error', error: 'Browser could not capture this page' });
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close().catch(() => {});
    clearTimeout(timer);
    lines.close();
    process.stdin.destroy();
  }
})();
