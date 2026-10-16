// Finishing the open conversation leaves it, as the back button would, and says
// what happened to the Ebi Workspace notes.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
for(const [width,height,workspace,expected] of [
 [390,844,{path:'Work/Site',task:'Ship it',changed:true},'Obsidian task checked'],
 [1280,800,null,null],
])test(`finish closes the item ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const items=[{id:'t100',thread_id:'100',title:'Ship it',status:'idle',bucket:'idle'}];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(name.endsWith('/done')){items[0].bucket='done';items[0].status='done';return route.fulfill({contentType:'application/json',body:JSON.stringify({item:items[0],workspace})});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',content:'Finished.'}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#idle/t100');
 await page.waitForSelector('#msgs .msg');
 if(width<900)await page.locator('.d-more').click();
 await page.locator('.d-finish').click();
 await page.waitForTimeout(500);
 await page.locator('.d-finish.armed').click();
 await page.waitForFunction(()=>state.open===null);
 assert.equal(await page.evaluate(()=>location.hash),'#idle');
 const toast=await page.locator('.toast.undo').textContent();
 assert.ok(toast.includes('Ship it'),toast);
 if(expected)assert.ok(toast.includes(expected),toast);else assert.ok(!toast.includes('Obsidian'),toast);
});
