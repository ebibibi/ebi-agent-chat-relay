// Run with Playwright installed: node --test tests/console_proxy_reauth.browser.cjs
// An identity-aware reverse proxy in front of the Console answers every request with a 302
// to its identity provider on another origin once its cookie expires. fetch() follows that
// redirect, the provider sends no CORS headers, and the request fails as "Failed to fetch".
// This test runs a real proxy and provider on two local origins and checks that the Console
// recovers: it waits while the human types, then reloads with the draft and the open view
// kept; "Reconnect" signs in again in a small window without reloading; and a real network
// failure reads as one.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const listen = (handler) => new Promise((resolve) => {
  const server = http.createServer(handler);
  server.listen(0, '127.0.0.1', () => resolve(server));
});

async function setup(t) {
  // gen bumps to expire every cookie handed out so far, like an access token running out.
  const env = { gen: 1, apiCalls: 0, signIns: 0 };
  const idp = await listen((req, res) => {
    env.signIns += 1;  // the provider's own session is alive: sign straight back in
    const back = new URL(req.url, 'http://x').searchParams.get('back');
    res.writeHead(302, { Location: `${env.proxyOrigin}/.proxy/callback?back=${encodeURIComponent(back)}` }).end();
  });
  const proxy = await listen((req, res) => {
    const url = new URL(req.url, env.proxyOrigin);
    if (url.pathname === '/.proxy/callback') {
      res.writeHead(302, { Location: url.searchParams.get('back'), 'Set-Cookie': `px=${env.gen}; Path=/; HttpOnly` }).end();
      return;
    }
    if (!(req.headers.cookie || '').split('; ').includes(`px=${env.gen}`)) {
      const back = encodeURIComponent(env.proxyOrigin + req.url);
      res.writeHead(302, { Location: `${env.idpOrigin}/authorize?back=${back}` }).end();
      return;
    }
    if (url.pathname === '/' || url.pathname.startsWith('/static/')) {
      const file = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
      const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store' }).end(fs.readFileSync(path.join(root, file)));
      return;
    }
    env.apiCalls += 1;
    const item = { id: 'c1', thread_id: '1', title: 'Thread', status: 'idle', running: false, bucket: 'idle', priority: 2 };
    const data = url.pathname.endsWith('/board') ? { items: [item], slots: {}, places: [] }
      : url.pathname.endsWith('/messages') ? { messages: [] } : {};
    res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(data));
  });
  env.proxyOrigin = `http://127.0.0.1:${proxy.address().port}`;
  env.idpOrigin = `http://localhost:${idp.address().port}`;  // another origin, as a real provider is
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
  t.after(async () => { await browser.close(); proxy.close(); idp.close(); });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: 'ja-JP' });
  await context.addInitScript(() => { window.CCDB_NET_IDLE_MS = 1500; });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.goto(`${env.proxyOrigin}/?lang=ja#focus/c1`);
  await page.locator('#reply').waitFor();
  return { env, page, context };
}
const poll = (page) => page.evaluate(() => window.dispatchEvent(new Event('console:reconnected')));

test('an expired proxy sign-in waits for typing to stop, then reloads keeping the draft and view', async (t) => {
  const { env, page } = await setup(t);
  await page.evaluate(() => { window.__sameDocument = true; });
  await page.locator('#reply').fill('書きかけの返信');
  env.gen += 1;  // the proxy's cookie expires
  // Still typing: the banner explains, nothing reloads.
  const typing = setInterval(() => page.keyboard.press('Shift').catch(() => {}), 300);
  await page.locator('#reply').focus();
  await poll(page);
  const banner = page.locator('.net-banner');
  await banner.waitFor();
  assert.match(await banner.textContent(), /サインインの有効期限が切れました。.*入力が終わったら/);
  assert.doesNotMatch(await page.locator('#sync').textContent(), /Failed to fetch/);
  await page.waitForTimeout(2500);
  assert.equal(await page.evaluate(() => window.__sameDocument), true, 'reloaded while typing');
  clearInterval(typing);
  // Typing stopped: the page reloads through the provider and comes back as it was.
  await page.waitForFunction(() => !window.__sameDocument && document.querySelector('#reply'));
  await page.locator('#reply').waitFor();
  await page.waitForFunction(() => document.querySelector('#reply').value === '書きかけの返信');
  assert.match(page.url(), /#focus\/c1$/);
  assert.ok(env.signIns >= 2, 'the reload went through the provider again');
  assert.equal(await page.locator('.net-banner').count(), 0);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('ccdb.net.saved')), null);
});

test('Reconnect signs in again in a small window and keeps the page as it is', async (t) => {
  const { env, page, context } = await setup(t);
  await page.evaluate(() => { window.__sameDocument = true; });
  // Unsaved work no text box holds: no automatic reload, only the Reconnect button.
  await page.evaluate(() => window.ConsoleNet.busy(() => true));
  await page.locator('#reply').fill('残しておきたい文');
  env.gen += 1;
  await poll(page);
  const banner = page.locator('.net-banner');
  await banner.waitFor();
  await page.waitForTimeout(2500);
  assert.match(await banner.textContent(), /保存していない変更があります/);
  const popup = context.waitForEvent('page');
  await banner.locator('button').click();
  const win = await popup;
  await win.waitForEvent('close', { timeout: 8000 });
  await banner.waitFor({ state: 'detached' });
  assert.equal(await page.evaluate(() => window.__sameDocument), true, 'the page was not reloaded');
  assert.equal(await page.locator('#reply').inputValue(), '残しておきたい文');
  const calls = env.apiCalls;
  await poll(page);
  await page.waitForFunction(() => /更新/.test(document.querySelector('#sync').textContent));
  assert.ok(env.apiCalls > calls, 'requests reach the Console again');
});

test('a real network failure says so instead of "Failed to fetch"', async (t) => {
  const { page } = await setup(t);
  await page.route('**/console/api/**', (route) => route.abort('internetdisconnected'));
  await page.route('**/static/reauth.html', (route) => route.abort('internetdisconnected'));
  await poll(page);
  await page.waitForFunction(() => /ネットワーク/.test(document.querySelector('#sync').textContent));
  assert.doesNotMatch(await page.locator('#sync').textContent(), /Failed to fetch/);
  assert.equal(await page.locator('.net-banner').count(), 0);
});
