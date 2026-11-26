// HTML attachment previews keep the page's own CSS but run none of its scripts and fetch
// nothing outside, under the Console's real CSP and html-frame.html's own policy.
// NODE_PATH=<playwright node_modules> node --test tests/console_html_preview.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const playwright = require('playwright');
const engine = playwright[process.env.CONSOLE_TEST_ENGINE || 'chromium'];
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const server = fs.readFileSync(path.resolve(__dirname, '../claude_discord/console/server.py'), 'utf8');
// The policies exactly as server.py sends them (adjacent Python string literals joined).
const policy = (block) => block.split('"Content-Security-Policy": (')[1].split('\n    ),')[0]
  .split('\n').filter(l => l.trim().startsWith('"')).map(l => (l.match(/"([^"]*)"/) || [])[1]).join('');
const CSP = policy(server.split('_SECURITY_HEADERS = {')[1]);
const FRAME_CSP = policy(server.split('_HTML_FRAME_HEADERS = {')[1]);
const PAGE = '<!doctype html><html><head><style>@import url("https://evil.example/import.css");' +
  'p#x{color:rgb(255,0,0)}body{background:url("https://evil.example/bg.png")}' +
  '@font-face{font-family:x;src:url("https://evil.example/f.woff")}p{font-family:x}</style>' +
  '<link rel="stylesheet" href="https://evil.example/link.css"></head><body>' +
  '<p id="x">styled</p><img src="https://evil.example/pixel.png" alt="">' +
  '<a id="out" href="https://example.org/source" target="_blank" rel="noopener">source</a>' +
  '<form action="https://evil.example/post"><button id="send">send</button></form>' +
  '<script>document.title="ran";parent.postMessage("pwned","*")</script></body></html>';

async function open(t, width, html = PAGE, name = 'report.html') {
  const browser = await engine.launch(engine === playwright.chromium ?
    {headless: true, executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox']} : {headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width, height: 844}});
  const seen = [];
  await page.route('**/*', async (route) => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    seen.push(req.url());
    if (u.host !== 'console.test') return route.abort();
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)),
        headers: {'Content-Security-Policy': file === 'html-frame.html' ? FRAME_CSP : CSP},
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    const data = p.endsWith('/board') ? {items: [], slots: {}, places: []} : {};
    return route.fulfill({body: JSON.stringify(data), contentType: 'application/json'});
  });
  await page.goto('http://console.test/?lang=ja#focus');
  await page.waitForFunction(() => window.ConsolePreview);
  await page.evaluate(() => window.addEventListener('message', (e) => { if (e.data === 'pwned') window.pwned = 1; }));
  await page.evaluate(({html, name}) => window.ConsolePreview.open(name, () => Promise.resolve(new Blob([html])), null,
    {url: '/console/api/files/42/' + 'a'.repeat(32) + '/' + name, attachments: []}), {html, name});
  const viewer = await (await page.waitForSelector('iframe.pv-frame')).contentFrame();
  await viewer.waitForLoadState();
  const inner = await (await viewer.waitForSelector('#page')).contentFrame();
  await inner.waitForSelector('body *', {state: 'attached'});
  return {page, seen, viewer, inner};
}

test('the page CSS applies; its scripts and outside requests do not', async (t) => {
  assert.match(CSP, /style-src 'self'; script-src 'self'/);
  assert.match(FRAME_CSP, /^sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; default-src 'none'/);
  const {page, seen, viewer, inner} = await open(t, 1280);
  assert.equal(await inner.evaluate(() => getComputedStyle(document.getElementById('x')).color), 'rgb(255, 0, 0)');
  await page.waitForTimeout(300);
  assert.notEqual(await inner.evaluate(() => document.title), 'ran');
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.ok(!seen.some((u) => u.includes('evil.example')), seen.join('\n'));
  assert.equal(await viewer.evaluate(() => { try { return parent.document ? 'reachable' : 'blocked'; } catch { return 'blocked'; } }), 'blocked');
  assert.equal(await viewer.evaluate(() => { try { return localStorage.length >= 0 ? 'reachable' : 'blocked'; } catch { return 'blocked'; } }), 'blocked');
  // A clicked link opens in a new tab; a form never submits.
  await page.context().route('https://example.org/**', (r) => r.fulfill({body: 'source'}));
  const popup = page.waitForEvent('popup', {timeout: 5000});
  await inner.click('#out');
  const tab = await popup;
  await tab.waitForLoadState();
  assert.equal(tab.url(), 'https://example.org/source');
  await inner.click('#send').catch(() => {});
  await page.waitForTimeout(300);
  assert.ok(!seen.some((u) => u.includes('evil.example')), seen.join('\n'));
  await page.getByRole('button', {name: 'ソース', exact: true}).click();
  assert.ok((await page.locator('.pv-pre').textContent()).includes('<style>'));
});

for (const width of [1280, 390]) test(`a create-html page renders styled at ${width}px`, async (t) => {
  const sample = process.env.HTML_PREVIEW_SAMPLE;
  if (!sample) return t.skip('HTML_PREVIEW_SAMPLE not set');
  const {page, inner} = await open(t, width, fs.readFileSync(sample, 'utf8'), 'sample.html');
  assert.notEqual(await inner.evaluate(() => getComputedStyle(document.body).fontFamily), '');
  if (process.env.PREVIEW_SCREENSHOTS) await page.screenshot({path: path.join(process.env.PREVIEW_SCREENSHOTS, `html-preview-${width}.png`)});
});
