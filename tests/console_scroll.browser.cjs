// Run with Playwright installed: node --test tests/console_scroll.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const fixture = (id) => ({ id, title: `Thread ${id}`, thread_id: id, status: 'idle',
  bucket: 'idle', priority: 2, last_activity_at: '2026-10-14T10:00:00Z' });

for (const width of [1440, 390]) {
  test(`thread list preserves browsing position at width ${width}`, async (t) => {
    const browser = await chromium.launch({ headless: true,
      executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
    t.after(() => browser.close());
    const page = await browser.newPage({ viewport: { width, height: 844 } });
    let items = Array.from({ length: 100 }, (_, n) => fixture(String(n)));
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
      const data = pathname.endsWith('/board') ? { items, slots: {}, places: [] } :
        pathname.endsWith('/messages') ? { messages: [] } : {};
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    });
    await page.goto('http://console.test/');
    await page.waitForFunction(() => state.items.length === 100);
    await page.evaluate(() => setView('idle'));
    await t.test('selection stays distinct from unread and pinned states', async () => {
      const selected = page.locator('.row.sel').first();
      const unread = page.locator('.row.fresh:not(.sel)').first();
      assert.equal(await selected.locator('.selection-label').count(), 1);
      for (const extra of ['', 'fresh', 'pinned', 'fresh pinned']) {
        const styles = await selected.evaluate((node, extra) => {
          node.className = 'row sel ' + extra;
          const css = getComputedStyle(node);
          return { outline: css.outlineStyle, width: parseFloat(css.outlineWidth), background: css.backgroundColor };
        }, extra);
        assert.equal(styles.outline, 'solid');
        assert.ok(styles.width >= 2);
        if (await unread.count()) assert.notEqual(styles.background, await unread.evaluate(node => getComputedStyle(node).backgroundColor));
      }
      await page.evaluate(() => renderList());
    });
    if (process.env.CONSOLE_SCREENSHOT_DIR) await page.screenshot({path: `${process.env.CONSOLE_SCREENSHOT_DIR}/selection-${width}.png`});
    const position = () => page.evaluate(() => {
      const list = $('list');
      const top = list.getBoundingClientRect().top;
      const row = [...list.querySelectorAll('.row')].find(r => r.getBoundingClientRect().bottom > top);
      return { scroll: list.scrollTop, title: row?.querySelector('.title').textContent,
        offset: row?.getBoundingClientRect().top - top };
    });
    await t.test('repeated refreshes do not pull a wheel scroll back to the selection', async () => {
      await page.locator('#list').hover();
      for (let i = 0; i < 3; i++) {
        const target = (await position()).scroll + 480;
        await page.mouse.wheel(0, 480);
        // wheel() queues a compositor event; wait for it, not a wall-clock guess.
        await page.waitForFunction(target => $('list').scrollTop === target, target);
        const before = await position();
        assert.ok(before.scroll > 300);
        await page.evaluate(() => refresh());
        assert.deepEqual(await position(), before);
      }
      assert.equal(await page.evaluate(() => state.selected), '0');
    });
    await t.test('insertions and removals above the viewport retain the visible row', async () => {
      const before = await position();
      items = [fixture('new'), ...items];
      await page.evaluate(() => refresh());
      let after = await position();
      assert.equal(after.title, before.title);
      assert.ok(Math.abs(after.offset - before.offset) <= 1);
      items = items.filter(i => !['new', '1', '2'].includes(i.id));
      await page.evaluate(() => refresh());
      after = await position();
      assert.equal(after.title, before.title);
      assert.ok(Math.abs(after.offset - before.offset) <= 1);
    });
    await t.test('keyboard navigation still reveals its selection', async () => {
      await page.keyboard.press('j');
      const inView = await page.evaluate(() => {
        const list = $('list').getBoundingClientRect();
        const selected = $('list').querySelector('.sel').getBoundingClientRect();
        return selected.top >= list.top - 1 && selected.bottom <= list.bottom + 1;
      });
      assert.ok(inView, JSON.stringify(await page.evaluate(() => ({
        selected: state.selected, scroll: $('list').scrollTop,
        list: $('list').getBoundingClientRect().toJSON(),
        row: $('list').querySelector('.sel').getBoundingClientRect().toJSON(),
      }))));
    });
    if (width === 1440) {
      await t.test('an open conversation and draft do not make refresh reclaim the list', async () => {
        await page.evaluate(() => openItem('0'));
        await page.locator('#reply').fill('Keep this draft');
        await page.evaluate(() => { $('list').scrollTop = 1700; });
        const before = await position();
        await page.evaluate(() => refresh());
        assert.deepEqual(await position(), before);
        assert.equal(await page.locator('#reply').inputValue(), 'Keep this draft');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'reply');
      });
    }
    await t.test('refresh at the top leaves new items visible', async () => {
      await page.evaluate(() => { $('list').scrollTop = 0; });
      items = [fixture('latest'), ...items];
      await page.evaluate(() => refresh());
      assert.equal((await position()).scroll, 0);
      assert.equal((await position()).title, 'Thread latest');
    });
    await t.test('a pinned thread is listed once and keeps its position on refresh', async () => {
      items = items.map(i => i.id === '50' ? {...i,pinned_at:'2026-10-14T10:00:00Z'} : i);
      await page.evaluate(async () => { closeItem(); await refresh(); setView('tree'); });
      await page.evaluate(() => {
        const rows = document.querySelectorAll('[data-item-id="50"]');
        const list = $('list');
        if (rows.length !== 1) throw new Error('Pinned family was duplicated');
        list.scrollTop += rows[0].getBoundingClientRect().top - list.getBoundingClientRect().top;
      });
      const before = await position();
      await page.evaluate(() => refresh());
      assert.deepEqual(await position(), before);
    });
    assert.deepEqual(errors, []);
  });
}
