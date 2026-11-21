// Markdown attachment previews: authenticated sibling images and fullscreen reading.
// NODE_PATH=<playwright node_modules> node --test tests/console_preview.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const server = fs.readFileSync(path.resolve(__dirname, '../claude_discord/console/server.py'), 'utf8');
const CSP = server.split('"Content-Security-Policy": (')[1].split('\n    ),')[0]
  .split('\n').filter(l=>l.trim().startsWith('"')).map(l => (l.match(/"([^"]*)"/) || [])[1]).filter(Boolean).join('');
const PREFIX = '/console/api/files/42/';
const DOC = PREFIX + 'a'.repeat(32) + '/report.md';
const IMAGE = PREFIX + 'b'.repeat(32) + '/%E5%9B%B3%201.png';
const PNG = fs.readFileSync(path.join(root, '../../../examples/demo/ja-it-admin/vault/02_Contexts/情シス/プレビュー確認/network.png'));
const TEXT = '# Report\n\n![Relative](<images/図 1.png>)\n\n![Reference][figure]\n\n[figure]: images/%E5%9B%B3%201.png "Diagram"\n\n![Web](https://images.example/figure.png)\n\n<script>window.injected=1</script>\n\n' + 'Paragraph.\n\n'.repeat(100);

async function open(t, width=1280, remote=false) {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width,height:844}});
  page.setDefaultTimeout(5000);
  const seen = [];
  const doc = remote ? 'https://cdn.discordapp.com/attachments/42/1/report.md?signature=doc' : DOC;
  const image = remote ? 'https://cdn.discordapp.com/attachments/42/2/%E5%9B%B3%201.png?signature=image' : IMAGE;
  await page.addInitScript(() => localStorage.setItem('token','preview-test-token'));
  await page.route('**/*', async route => {
    const req=route.request(), u=new URL(req.url()), p=u.pathname;
    seen.push({url:req.url(),auth:req.headers().authorization,referrer:req.headers().referer});
    const docUrl = new URL(doc, 'http://console.test').href, imageUrl = new URL(image, 'http://console.test').href;
    if (req.url()===docUrl || req.url()===imageUrl || u.host==='images.example') {
      return route.fulfill({body:req.url()===docUrl ? TEXT : PNG, contentType:req.url()===docUrl ? 'text/markdown' : 'image/png', headers:{'Access-Control-Allow-Origin':'*'}});
    }
    if (u.host!=='console.test') return route.abort();
    if (p.startsWith('/console/api/files/')) return route.fulfill({status:404,body:'not found'});
    if (p==='/' || p.startsWith('/static/')) {
      const file=p==='/'?'index.html':path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)), headers:{'Content-Security-Policy':CSP},
        contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data={};
    if(p.endsWith('/board')) data={items:[],slots:{},places:[]};
    return route.fulfill({body:JSON.stringify(data),contentType:'application/json'});
  });
  await page.goto('http://console.test/?lang=en#focus');
  await page.waitForFunction(() => window.ConsolePreview && window.ConsoleLive);
  await page.evaluate(() => { if (!document.querySelector('#msgs')) { const node=document.createElement('div'); node.id='msgs'; document.body.append(node); } });
  if (remote) {
    await page.evaluate(({doc,image}) => {
      const row=messageNode({id:'m',is_bot:true,content:'Result',attachments:[
        {filename:'report.md',url:doc},{filename:'図 1.png',url:image,content_type:'image/png'}]});
      document.querySelector('#msgs').append(row);
    },{doc,image});
    await page.locator('.file-look').click();
  } else {
    await page.evaluate(({doc,image}) => {
      const msg=document.createElement('div');msg.className='msg';
      const body=document.createElement('div');body.className='body';
      for(const href of [doc,image]){const a=document.createElement('a');a.href=href;a.textContent=href;body.append(a);}
      msg.append(body);document.querySelector('#msgs').append(msg);window.ConsoleLive.linkFiles();
    },{doc,image});
    await page.getByRole('button',{name:'👁 Preview report.md',exact:true}).click();
  }
  await page.locator('.pv-doc').waitFor();
  return {page,seen,image};
}

test('Markdown images render from authenticated sibling attachments and HTTPS',async t=>{
  const {page,seen,image}=await open(t);
  await page.waitForFunction(()=>[...document.querySelectorAll('.pv-doc img')].length===3 && [...document.querySelectorAll('.pv-doc img')].every(i=>i.naturalWidth===480));

  assert.ok(seen.some(r=>r.url.endsWith(image)&&r.auth==='Bearer preview-test-token'));
  assert.ok(seen.some(r=>r.url==='https://images.example/figure.png'&&!r.auth&&!r.referrer));
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  await page.getByRole('button',{name:'Source',exact:true}).click();
  assert.ok((await page.locator('.pv-pre').textContent()).includes('![Relative]'));
  await page.getByRole('button',{name:'Rendered',exact:true}).click();
  assert.equal(await page.locator('.pv-doc img').count(),3);
});

test('Discord sibling attachment signed URLs stay intact without Console credentials',async t=>{
  const {page,seen,image}=await open(t,1280,true);
  await page.waitForFunction(()=>document.querySelector('.pv-doc img')?.naturalWidth===480);
  assert.ok(seen.some(r=>r.url===image&&!r.auth));
});

for(const width of [1280,390]) test(`fullscreen reading fills viewport and restores at ${width}px`,async t=>{
  const {page}=await open(t,width);
  const panel=page.locator('.pv-panel');
  const before=await panel.boundingBox();
  await page.locator('.pv-body').evaluate(e=>e.scrollTop=150);
  await page.getByRole('button',{name:'Fullscreen',exact:true}).click();
  const full=await panel.boundingBox();
  assert.ok(Math.abs(full.width-width)<2 && Math.abs(full.height-844)<2,JSON.stringify(full));
  assert.equal(await page.getByRole('button',{name:'Restore size',exact:true}).getAttribute('aria-pressed'),'true');
  if(process.env.PREVIEW_SCREENSHOTS) await page.screenshot({path:path.join(process.env.PREVIEW_SCREENSHOTS,`preview-${width}.png`)});
  assert.ok(await page.locator('.pv-body').evaluate(e=>e.scrollTop)>0);
  await page.keyboard.press('Escape');
  assert.equal(await panel.count(),1);
  assert.deepEqual(await panel.boundingBox(),before);
  await page.keyboard.press('Escape');
  assert.equal(await panel.count(),0);
});

test('unsafe image locations never fetch privileged endpoints or local files',async t=>{
  const {page,seen}=await open(t);
  await page.evaluate(async()=>window.ConsolePreview.open('unsafe.md',()=>Promise.resolve(new Blob([
    '![secret](/console/api/admin/delete)\n\n![local](file:///etc/passwd)\n\n![bad](javascript:alert(1))\n\n![bad](//evil.example/a.png)'
  ])),null,{url:'/console/api/files/42/'+'a'.repeat(32)+'/unsafe.md'}));
  const imgs=await page.locator('.pv-doc img').evaluateAll(xs=>xs.map(i=>i.getAttribute('src')));
  assert.ok(imgs.every(s=>!s),JSON.stringify(imgs));
  assert.equal(await page.locator('.pv-doc script, .pv-doc iframe').count(),0);
  assert.ok(!seen.some(r=>r.url.includes('evil.example')||r.url.includes('/admin/delete')));
});


test('missing images keep a readable document and explain the missing resource',async t=>{
  const {page}=await open(t);
  await page.evaluate(async()=>window.ConsolePreview.open('missing.md',()=>Promise.resolve(new Blob([
    '# Still readable\n\n![Missing diagram](missing.png)'
  ])),null,{url:'/console/api/files/42/'+'a'.repeat(32)+'/missing.md'}));
  await page.getByText('Missing diagram — Image unavailable',{exact:true}).waitFor();
  assert.equal(await page.locator('.pv-doc h1').textContent(),'Still readable');
});

test('closing a pending preview cannot close its replacement on late failure',async t=>{
  const {page}=await open(t);
  await page.evaluate(async()=>{
    let reject;
    const waiting=new Promise((_,r)=>reject=r);
    window.ConsolePreview.open('slow.txt',()=>waiting);
    await window.ConsolePreview.open('next.txt',()=>Promise.resolve(new Blob(['next document'])));
    reject(new Error('late failure'));
  });
  assert.equal(await page.locator('.pv-pre').textContent(),'next document');
});
