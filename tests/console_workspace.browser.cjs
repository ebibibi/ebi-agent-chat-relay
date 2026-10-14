const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
for (const available of [false, true]) {
  test(`Workspace GUI with configured=${available}`, {timeout: 20000}, async t => {
    const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    t.after(() => browser.close());
    const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
    const entry = {path:'Work/Website',name:'Website',kind:'project',context:'Work',file:'status.md',revision:'v1',
      tasks:[{line:3,text:'Ship the website',done:false}]};
    const writes = [];
    let items = [];
    await page.route('http://console.test/**', async route => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/' || p.startsWith('/static/')) {
        const file = p === '/' ? 'index.html' : path.basename(p);
        return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
          file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
      }
      let data = {};
      if (p.endsWith('/board')) data = {items,slots:{},places:[]};
      else if (p.endsWith('/workspace')) data = {available,entries:available?[entry]:[]};
      else if (p.endsWith('/document')) data = {text:'# Website\nProject overview'};
      else if (p.endsWith('/workspace/tasks')) {
        const body = route.request().postDataJSON(); writes.push(body);
        if (route.request().method() === 'POST') entry.tasks.push({line:4,text:body.text,done:false});
        else Object.assign(entry.tasks.find(t=>t.line===body.line),body);
        entry.revision += 'x'; data=entry;
      } else if (p.endsWith('/workspace/run')) {
        const body = route.request().postDataJSON(); writes.push(body);
        items = [{id:'t1',thread_id:'1',title:'Ship the website',status:'running',bucket:'ai',running:true,
          workspace_path:entry.path,priority:2}]; data={item:items[0]};
      } else if (p.endsWith('/messages')) data={messages:[]};
      return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    });
    const loaded = page.waitForResponse(r=>r.url().endsWith('/workspace'));
    await page.goto('http://console.test/');
    await loaded;
    if (!available) {
      await page.locator('.workspace-offer').waitFor();
      await page.locator('.workspace-offer button').first().click();
      await page.locator('.ws-onboarding').waitFor();
      const link = page.locator('.ws-purchase');
      assert.equal(await link.getAttribute('href'),'https://note.com/ebibibi/n/n300772aeac93');
      assert.match(await page.locator('.ws-onboarding').innerText(),/買い切り/);
      assert.equal(writes.length,0);
      await page.getByRole('button',{name:'閉じる',exact:true}).click();
      await page.locator('.workspace-offer button').last().click();
      assert.equal(await page.locator('.workspace-offer').count(),0);
      await page.reload();
      await page.locator('.nav-workspace').waitFor();
      assert.equal(await page.locator('.workspace-offer').count(),0);
      await page.locator('.nav-workspace').click();
      assert.equal(await page.locator('.ws-onboarding').isVisible(),true);
      await page.goto('http://console.test/?lang=en');
      await page.locator('.nav-workspace').click();
      assert.equal(await page.locator('.ws-purchase').getAttribute('href'),'https://note.com/ebibibi/n/nc327ab1de017');
      return;
    }
    await page.locator('.nav-workspace').click();
    await page.locator('.ws-entry').click();
    await page.locator('.ws-add input').fill('Check links');
    await page.locator('.ws-add button').click();
    await page.getByText('Check links',{exact:true}).waitFor();
    await page.locator('.ws-task').last().locator('input[type=checkbox]').check();
    await page.waitForFunction(()=>document.querySelectorAll('.ws-task.done').length===1);
    await page.locator('.ws-task').first().getByRole('button',{name:'編集',exact:true}).click();
    await page.locator('.ws-edit input').fill('Ship the updated website');
    await page.locator('.ws-edit button[type=submit]').click();
    await page.getByText('Ship the updated website',{exact:true}).waitFor();
    await page.locator('.ws-task').first().getByRole('button',{name:'AIに実行',exact:true}).click();
    await page.waitForFunction(()=>state.open==='t1');
    assert.equal(writes.at(-1).path,'Work/Website');
    assert.equal(await page.locator('#d-head .workspace-picker').inputValue(),'Work/Website');
    await page.locator('.nav-workspace').click();
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=390));
  });
}
