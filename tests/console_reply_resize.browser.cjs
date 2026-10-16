// Run with Playwright installed: node --test tests/console_reply_resize.browser.cjs
// On a desktop the reply box is resized by dragging the top edge of the form, the height
// survives typing and re-rendering, and double-click goes back to the auto-growing default.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

async function openThread(t, viewport, opts = {}) {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport, locale: 'ja-JP', ...opts });
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
    const item = { id: 'c1', thread_id: '1', title: 'Thread', status: 'idle', running: false,
      bucket: 'idle', priority: 2 };
    const data = pathname.endsWith('/board') ? { items: [item], slots: {}, places: [] } :
      pathname.endsWith('/messages') ? { messages: [] } : {};
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://console.test/#focus/c1');
  await page.waitForSelector('#reply');
  return { page, errors };
}

const height = (page) => page.locator('#reply').evaluate(b => b.getBoundingClientRect().height);

test('desktop: dragging the top edge resizes the reply box and is remembered', async (t) => {
  const { page, errors } = await openThread(t, { width: 1440, height: 1000 });
  const before = await height(page);
  const edge = page.locator('.reply-resize');
  assert.ok(await edge.isVisible());
  const r = await edge.boundingBox();
  const x = r.x + r.width / 2;
  const y = r.y + r.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y - 450, { steps: 5 });
  await page.mouse.up();
  const tall = await height(page);
  assert.ok(tall > before + 400, `expected taller than ${before + 400}, got ${tall}`);
  // Past the old 40vh cap.
  assert.ok(tall > 400, `expected past 40vh, got ${tall}`);

  // Typing does not snap it back.
  await page.locator('#reply').fill('short');
  await page.locator('#reply').dispatchEvent('input');
  assert.ok(Math.abs(await height(page) - tall) < 3);

  // Dragging down shrinks it again.
  const r2 = await edge.boundingBox();
  await page.mouse.move(x, r2.y + r2.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, r2.y + r2.height / 2 + 150, { steps: 5 });
  await page.mouse.up();
  const shorter = await height(page);
  assert.ok(shorter < tall - 120, `expected shorter than ${tall - 120}, got ${shorter}`);

  // A reload keeps the dragged height.
  await page.reload();
  await page.waitForSelector('#reply');
  await page.waitForTimeout(100);
  assert.ok(Math.abs(await height(page) - shorter) < 3);

  // Double-click goes back to the default.
  await page.locator('.reply-resize').dblclick();
  assert.ok(Math.abs(await height(page) - before) < 3);
  assert.equal(await page.evaluate(() => localStorage.getItem('replyHeight')), null);
  assert.deepEqual(errors, []);
});

test('phone: no drag edge, the box keeps auto-growing', async (t) => {
  const { page, errors } = await openThread(t, { width: 390, height: 844 },
    { isMobile: true, hasTouch: true });
  assert.equal(await page.locator('.reply-resize').isVisible(), false);
  assert.deepEqual(errors, []);
});
