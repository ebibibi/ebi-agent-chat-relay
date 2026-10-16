// The Finish button stays in one place whatever else the header holds: the
// top-right corner on a desktop, the first action in the mobile tools panel.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const items=[
 {id:'t100',thread_id:'100',title:'Short',status:'idle',bucket:'idle'},
 {id:'n1',thread_id:null,title:'A note with a much longer title that pushes the controls around',status:'todo',bucket:'todo',
  context:{used:120000,window:200000}},
 {id:'t200',thread_id:'200',title:'Finished one',status:'done',bucket:'done'},
];
async function open(t,width,height){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',content:'Hi.'}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 return page;
}
for(const width of [1280,980])test(`finish keeps the top-right corner at ${width}px`,async t=>{
 const page=await open(t,width,800);
 const spots=[];
 for(const item of items){
  await page.goto(`http://console.test/?lang=en#${item.bucket}/${item.id}`);
  await page.waitForSelector('#d-head .d-finish');
  spots.push(await page.evaluate(()=>{
   const head=document.querySelector('#d-head').getBoundingClientRect();
   const b=document.querySelector('#d-head .d-finish').getBoundingClientRect();
   return {right:Math.round(head.right-b.right),top:Math.round(b.top-head.top)};
  }));
 }
 for(const s of spots)assert.deepEqual(s,spots[0],JSON.stringify(spots));
 assert.ok(spots[0].right<=16&&spots[0].top<=12,JSON.stringify(spots[0]));
});
test('finish is the first mobile action',async t=>{
 const page=await open(t,390,844);
 for(const item of items){
  await page.goto(`http://console.test/?lang=en#${item.bucket}/${item.id}`);
  await page.waitForSelector('#msgs .msg, #msgs .empty');
  await page.locator('.d-more').click();
  assert.equal(await page.locator('.mobile-actions > :first-child').evaluate(n=>n.classList.contains('d-finish')),true,item.id);
  await page.locator('.mobile-tools-close').click();
 }
});
