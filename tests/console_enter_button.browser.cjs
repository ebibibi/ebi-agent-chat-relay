// Enter presses the focused button, as in any web page; the list's own Enter (open the selected
// row) only applies when focus is not on something that Enter already activates.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
test('Enter activates a focused button and still opens the selected row',async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width:1280,height:800}});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const items=[{id:'t100',thread_id:'100',title:'A thread',status:'idle',bucket:'idle'}];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[]}:/\/(commands|children|defaults)$/.test(name)?{backend:'claude',model:'opus',effort:'high',backends:['claude'],models:[],efforts:['high'],skills:[]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#idle');
 await page.waitForSelector('#list .row');
 const hideDone=page.locator('#hide-done');
 const before=await hideDone.getAttribute('aria-pressed');
 await hideDone.focus();
 await page.keyboard.press('Enter');
 assert.notEqual(await hideDone.getAttribute('aria-pressed'),before,'Enter pressed the focused button');
 assert.equal(await page.evaluate(()=>state.open),null,'Enter on a button does not also open the selected row');
 await page.evaluate(()=>document.activeElement.blur());
 if(!await page.evaluate(()=>state.selected))await page.keyboard.press('j');
 assert.equal(await page.evaluate(()=>state.selected),'t100');
 await page.keyboard.press('Enter');
 assert.equal(await page.evaluate(()=>state.open),'t100','Enter elsewhere still opens the selected row');
});
