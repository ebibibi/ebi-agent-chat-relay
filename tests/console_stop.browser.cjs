// Run with Playwright installed: node --test tests/console_stop.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

for (const target of ['#list .stop', '#d-head .stop', '.live-stop']) {
  test(`Stop acknowledges ${target} before the response and survives redraws`, async (t) => {
    const browser = await chromium.launch({ headless: true,
      executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
    t.after(() => browser.close());
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'ja-JP' });
    let running = true;
    let calls = 0;
    let stopRoute;
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://console.test/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/' || pathname.startsWith('/static/')) {
        const file = pathname === '/' ? 'index.html' : path.basename(pathname);
        return route.fulfill({ body: fs.readFileSync(path.join(root, file)),
          contentType: file.endsWith('.js') ? 'text/javascript' :
            file.endsWith('.css') ? 'text/css' : 'text/html' });
      }
      if (pathname.endsWith('/stop')) { calls++; stopRoute = route; return; }
      const item = { id: 'c1', thread_id: '1', title: 'Running task', status: running ? 'running' : 'idle',
        running, bucket: running ? 'ai' : 'idle', priority: 2 };
      const data = pathname.endsWith('/board') ? { items: [item], slots: {}, places: [] } :
        pathname.endsWith('/live') ? { running, live: running ? { can_stop: true, activities: [] } : null } :
          pathname.endsWith('/messages') ? { messages: [] } : {};
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    });
    await page.goto('http://console.test/');
    await page.waitForFunction(() => state.items.length === 1);
    await page.evaluate(() => { setView('ai'); return openItem('c1'); });
    await page.locator('.live-stop').waitFor();
    const buttons = page.locator('#list .stop, #d-head .stop, .live-stop');
    const assertStopping = async () => {
      const states = await buttons.evaluateAll(bs => bs.map(b => ({ disabled: b.disabled,
        busy: b.getAttribute('aria-busy'), label: b.getAttribute('aria-label'), text: b.textContent })));
      assert.equal(states.length, 3);
      for (const b of states) {
        assert.equal(b.disabled, true);
        assert.equal(b.busy, 'true');
        assert.match(b.label, /停止中/);
        assert.match(b.text, /⏳|停止中/);
      }
    };
    await page.locator(target).click();
    await assertStopping();
    await page.evaluate(() => refresh());
    await page.waitForTimeout(2200); // The live panel polls and recreates its button.
    await assertStopping();
    await buttons.evaluateAll(bs => bs.forEach(b => b.click()));
    assert.equal(calls, 1);
    // A failed request makes all entry points retryable and reports the error.
    await stopRoute.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"Stop failed"}' });
    await page.waitForFunction(() => [...document.querySelectorAll('.stop, .live-stop')].every(b => !b.disabled));
    assert.match(await page.locator('.toast').last().textContent(), /Stop failed/);
    await page.locator(target).click();
    await assertStopping();
    await page.waitForFunction(() => true);
    while (calls < 2) await page.waitForTimeout(10);
    await stopRoute.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"stopping"}' });
    await page.waitForTimeout(100);
    await page.evaluate(() => refresh());
    await assertStopping(); // Accepted is not yet stopped.
    running = false;
    await page.evaluate(() => refresh());
    await page.waitForFunction(() => !document.querySelector('.live-stop'));
    running = true;
    await page.evaluate(() => refresh());
    assert.equal(await page.locator('#list .stop').isEnabled(), true);
    assert.equal(calls, 2);
    assert.deepEqual(errors, []);
  });
}
