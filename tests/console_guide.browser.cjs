const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

async function open(t, board, locale = 'ja-JP', url = 'http://console.test/') {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale});
  await page.addInitScript(() => localStorage.setItem('view', 'focus'));
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = board;
    else if (p.endsWith('/workspace')) data = {available:false,entries:[]};
    else if (p.endsWith('/messages')) data = {messages:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(url);
  return page;
}

const item = (id, over = {}) => ({id, thread_id:id, title:`Item ${id}`, status:'waiting', bucket:'me',
  priority:2, focus:120, actionable:true, ...over});

test('the guide names the next move, why, batches and drift', {timeout: 20000}, async t => {
  const items = [item('a',{title:'Answer the customer'}), item('b'), item('c')];
  const guide = {
    next:[{id:'a',title:'Answer the customer',reasons:['overdue','asked'],unblocks:true,next_step:'Reply with the date',why:'Customer waits',place:null}],
    batches:[{root_id:'b',title:'Release',items:['b','c']}],
    calm:false, counts:{me:3,running:1,waiting:0,candidate:0},
    drift:[{id:'c',title:'Ship it',kind:'task_open',workspace_path:'W/S',task:'Ship it'}],
  };
  const page = await open(t, {items, slots:{}, places:[], guide});
  const card = page.locator('.guide-card');
  await card.waitFor();
  const text = await card.innerText();
  for (const s of ['今やる一手','Answer the customer','期限超過','AIが質問中','→ Reply with the date','返すとAIが続きを進めます',
    'まとめて判断','Release — 2件の判断','記録を自動で直せなかった会話','「Ship it」は未完了']) assert.ok(text.includes(s), s);
  await card.locator('.guide-next').click();
  await page.waitForFunction(() => state.open === 'a');
});

test('nothing for the human: the guide says so, and shows no drift without a Workspace', {timeout: 20000}, async t => {
  const items = [item('r',{status:'running',bucket:'ai',focus:null,actionable:null})];
  const guide = {next:[],batches:[],calm:true,counts:{me:0,running:1,waiting:2,candidate:0},drift:null};
  const page = await open(t, {items, slots:{}, places:[], guide}, 'en-US', 'http://console.test/?lang=en');
  const card = page.locator('.guide-card');
  await card.waitFor();
  const text = await card.innerText();
  assert.match(text, /Nothing needs you right now/);
  assert.match(text, /1 running · 2 waiting/);
  assert.match(text, /You can stop here/);
  assert.doesNotMatch(text, /Records the Console could not write back/);
});

test('a board without a guide (older server) renders Focus as before', {timeout: 20000}, async t => {
  const page = await open(t, {items:[item('a')], slots:{}, places:[]});
  await page.locator('.place-bar').waitFor();
  assert.equal(await page.locator('.guide-card').count(), 0);
});
