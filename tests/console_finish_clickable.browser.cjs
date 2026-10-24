// The Finish button stays pressable: the Undo toast from the last finish never covers
// the next item's button, and a board poll does not swap the button out from under a click.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const items=[
 {id:'t100',thread_id:'100',title:'First',status:'idle',bucket:'idle'},
 {id:'n1',thread_id:null,title:'Next',status:'todo',bucket:'todo'},
];
async function open(t){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width:980,height:800}});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items:items.map(i=>({...i,updated_at:String(polls++)})),slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',content:'Hi.'}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 return page;
}
let polls=0;
const overlap=(a,b)=>a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;
test('the undo toast does not cover the next item\'s finish button',async t=>{
 const page=await open(t);
 await page.goto('http://console.test/?lang=en#idle/t100');
 // The usage strip (or a wrapped header) pushes the header down into the toast's default spot.
 await page.addStyleTag({content:'#detail{padding-top:40px}'});
 const finish=page.locator('#d-head .d-finish');
 await finish.click();await page.waitForTimeout(500);await finish.click();
 await page.waitForSelector('.toast.undo');
 await page.evaluate(()=>{location.hash='#todo/n1';});
 await page.waitForSelector('#d-head .d-finish');
 const rects=await page.evaluate(()=>[...document.querySelectorAll('.toast.undo, #d-head .d-finish')]
  .map(n=>n.getBoundingClientRect().toJSON()));
 assert.equal(rects.length,2);
 console.log(JSON.stringify(rects));
 assert.ok(!overlap(rects[0],rects[1]),JSON.stringify(rects));
});
test('a board refresh keeps the same finish button node',async t=>{
 const page=await open(t);
 await page.goto('http://console.test/?lang=en#idle/t100');
 await page.waitForSelector('#d-head .d-finish');
 const same=await page.evaluate(async()=>{
  const before=document.querySelector('#d-head .d-finish');
  await refresh();
  return before===document.querySelector('#d-head .d-finish')&&before.isConnected;
 });
 assert.equal(same,true);
});
