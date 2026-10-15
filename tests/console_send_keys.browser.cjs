// Run with Playwright installed: node --test tests/console_send_keys.browser.cjs
// Every text box sends on Ctrl/Cmd+Enter only, and a burst of sends posts once.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

async function openConsole(t, held) {
  const browser = await chromium.launch({ headless: true,
    executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'ja-JP' });
  const posts = [];
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const pathname = new URL(req.url()).pathname;
    if (pathname === '/' || pathname.startsWith('/static/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({ body: fs.readFileSync(path.join(root, file)),
        contentType: file.endsWith('.js') ? 'text/javascript' :
          file.endsWith('.css') ? 'text/css' : 'text/html' });
    }
    if (req.method() === 'POST' && (pathname.endsWith('/items') || pathname.endsWith('/reply'))) {
      posts.push({ pathname, body: req.postDataJSON() });
      held.push(route);
      return;
    }
    const item = { id: 'c1', thread_id: '1', title: 'Thread', status: 'idle', running: false,
      bucket: 'idle', priority: 2 };
    const data = pathname.endsWith('/board') ? { items: [item], slots: {}, places: [] } :
      pathname.endsWith('/messages') ? { messages: [] } : {};
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(() => state.items.length === 1);
  return { page, posts, errors };
}

const release = (held, body) => Promise.all(held.splice(0).map(r =>
  r.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })));

test('capture box: Enter is a newline, Ctrl+Enter sends once', async (t) => {
  const held = [];
  const { page, posts, errors } = await openConsole(t, held);
  const box = page.locator('#capture-input');
  await box.click();
  await box.type('first line');
  await box.press('Enter');
  await box.type('second line');
  assert.equal(await box.inputValue(), 'first line\nsecond line');
  assert.equal(posts.length, 0);

  await box.press('Control+Enter');
  await box.press('Control+Enter').catch(() => {});
  await page.locator('#capture-send').click({ force: true });
  await page.waitForTimeout(300);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].body.title, 'first line');
  assert.equal(posts[0].body.note, 'second line');

  await release(held, { item: { id: 'c2', title: 'first line' } });
  await page.waitForFunction(() => document.getElementById('capture-input').value === '');
  // Once the first send finished, the next one goes through again.
  await box.type('next');
  await box.press('Meta+Enter');
  await page.waitForTimeout(300);
  assert.equal(posts.length, 2);
  await release(held, { item: { id: 'c3', title: 'next' } });
  assert.deepEqual(errors, []);
});

test('reply box: Enter is a newline, repeated Ctrl+Enter and clicks send once', async (t) => {
  const held = [];
  const { page, posts, errors } = await openConsole(t, held);
  await page.evaluate(() => openItem('c1'));
  const box = page.locator('#reply');
  await box.waitFor();
  await box.click();
  await box.type('hello');
  await box.press('Enter');
  assert.equal(posts.length, 0);
  await box.type('there');

  const send = page.locator('form.reply button[type=submit]');
  await box.press('Control+Enter');
  await send.click({ force: true });
  await send.click({ force: true });
  await page.waitForTimeout(300);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].body.text, 'hello\nthere');

  await release(held, {});
  await page.waitForFunction(() => !document.getElementById('reply').disabled);
  assert.equal(await page.locator('#reply').inputValue(), '');
  assert.deepEqual(errors, []);
});
