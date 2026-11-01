// The Workspace nav button must appear after signing in, without a page reload.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_workspace_login.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Workspace nav shows up after a 401 at startup once the user signs in', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  let signedIn = false;
  // Capture the sign-in callback instead of driving the passkey dialog.
  await page.addInitScript(() => {
    let auth;
    Object.defineProperty(window, 'ConsoleAuth', {configurable:true, get:() => auth,
      set:x => { auth = {...x, showLogin:done => { window.__signedIn = done; }}; }});
  });
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    if (!signedIn) return route.fulfill({status:401,contentType:'application/json',body:'{"error":"unauthorized"}'});
    let data = {};
    if (p.endsWith('/board')) data = {items:[],slots:{},places:[]};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(() => typeof window.__signedIn === 'function');
  assert.equal(await page.locator('.nav-workspace').count(), 0);
  signedIn = true;
  await page.evaluate(() => window.__signedIn());
  await page.locator('.nav-workspace').waitFor({timeout: 5000});
  assert.match(await page.locator('.nav-workspace').innerText(), /Ebi Workspace/);
});
