// Run with Playwright installed: node --test tests/console_bulk.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const fixture = (id, extra = {}) => ({id, title: `Thread ${id}`, thread_id: id,
  status: 'idle', bucket: 'idle', priority: 2, ...extra});
async function setup(t, items) {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  page.setDefaultTimeout(4000);
  const app = {items, requests:[], fail: new Set(), gate: null};
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text;}}}));
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('http://console.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/' || pathname.startsWith('/static/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),
        contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    const done = pathname.match(/\/items\/([^/]+)\/done$/);
    if (done) {
      const id = decodeURIComponent(done[1]); app.requests.push(id);
      if (app.gate) await app.gate;
      if (app.fail.has(id)) return route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({error:'Please retry'})});
      app.items = app.items.map(i => i.id === id ? {...i,bucket:'done',status:'done'} : i);
    }
    const data = pathname.endsWith('/board') ? {items:app.items,slots:{},places:[]} :
      pathname.endsWith('/messages') ? {messages:[]} : {};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/?lang=en');
  await page.waitForFunction(() => state.items.length > 0);
  await page.evaluate(() => setView('idle'));
  app.page = page;
  app.row = id => page.locator(`.row[data-item-id="${id}"]`).last();
  app.selected = () => page.locator('.bulk-check:checked').evaluateAll(nodes => [...new Set(nodes.map(n => n.closest('.row').dataset.itemId))]);
  return app;
}


for (const width of [1440,390]) test(`independent manager and child AI pickers at width ${width}`, async t => {
 const app=await setup(t,[fixture('t100')]);const page=app.page;
 await page.setViewportSize({width,height:844});
 const configs={manager:{backend:'claude',model:'opus'},child:{backend:'claude',model:'sonnet'},global:{backend:'claude',model:'sonnet'}};
 const requests=[];
 await page.route('**/console/api/**',async route=>{
  const url=new URL(route.request().url());
  const scope=url.pathname.endsWith('/defaults/children')?'global':url.pathname.endsWith('/children')?'child':/\/(command|commands)$/.test(url.pathname)?'manager':null;
  if(!scope)return route.fallback();
  const c=configs[scope];
  if(route.request().method()==='POST'){
   const body=route.request().postDataJSON();requests.push([scope,body]);
   c[body.name]=body.arg;if(body.name==='backend')c.model='';
  }
  const catalog={...c,backends:['claude','codex','pi'],models:[{value:c.backend==='pi'?'provider/gpt-6.1-sol':'opus'}],efforts:['high','medium']};
  await route.fulfill({contentType:'application/json',body:JSON.stringify(route.request().method()==='POST'?{catalog}:catalog)});
 });
 await page.evaluate(()=>openItem('t100'));
 if(width===390)await page.locator('.d-more').click();
 await page.locator('.team-child-ai').click();
 await page.locator('.backend-picker').getByRole('button',{name:'pi',exact:true}).click();
 await page.locator('.backend-picker').getByRole('button',{name:'provider/gpt-6.1-sol',exact:true}).click();
 await page.locator('.backend-picker').getByRole('button',{name:'high',exact:true}).click();
 await page.locator('.backend-picker button[aria-label=close]').click();
 assert.equal(configs.manager.backend,'claude');
 assert.equal(configs.child.backend,'pi');assert.equal(configs.child.model,'provider/gpt-6.1-sol');
 assert.match(await page.locator('.team-child-ai').innerText(),/gpt-6.1-sol/);
 await page.locator('.team-manager-ai').click();
 await page.locator('.backend-picker').getByRole('button',{name:'Codex',exact:true}).click();
 await page.locator('.backend-picker button[aria-label=close]').click();
 assert.equal(configs.manager.backend,'codex');assert.equal(configs.child.backend,'pi');
 if(width===390){await page.locator('.mobile-tools-close').click();await page.locator('.detail-nav-toggle').click();}
 await page.locator('.nav-child-ai').click();
 await page.locator('.backend-picker').getByRole('button',{name:'pi',exact:true}).click();
 await page.locator('.backend-picker button[aria-label=close]').click();
 assert.equal(configs.global.backend,'pi');
 assert.deepEqual(requests.map(r=>r[0]),['child','child','child','manager','global']);
});

test('captured notes do not display a phantom AI control', async t => {
 const {page}=await setup(t,[fixture('note',{thread_id:null,note:'Draft note'})]);
 await page.evaluate(()=>openItem('note'));
 assert.equal(await page.locator('.team-settings').count(),0);
 assert.equal(await page.locator('#detail').evaluate(node=>[...node.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('')),'');
});
