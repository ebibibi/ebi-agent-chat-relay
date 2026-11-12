const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
async function setup(t,width){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height:900}});page.setDefaultTimeout(4000);
 const items=['A','B','C'].map((title,i)=>({id:'t'+(100+i),thread_id:String(100+i),title,priority:2,status:'idle',bucket:'idle',pinned_at:`2026-11-12T00:00:0${3-i}Z`,pin_position:null}));
 const requests=[];let fail=false;
 await page.route('http://console.test/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname;
  if(p==='/'||p.startsWith('/static/')){const f=p==='/'?'index.html':path.basename(p);return route.fulfill({body:fs.readFileSync(path.join(root,f)),contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html'});}
  if(p.endsWith('/pin-order')){
   requests.push(req.postDataJSON());if(fail)return route.fulfill({status:400,json:{error:'Pin no longer available'}});
   const sorted=items.filter(i=>i.pinned_at).sort((a,b)=>(a.pin_position??-1)-(b.pin_position??-1)||b.pinned_at.localeCompare(a.pinned_at));
   const id=p.split('/').at(-2),body=req.postDataJSON(),move=sorted.splice(sorted.findIndex(i=>i.id===id),1)[0];
   const index=sorted.findIndex(i=>i.id===body.target_id)+(body.placement==='after'?1:0);sorted.splice(index,0,move);
   sorted.forEach((i,n)=>i.pin_position=n);return route.fulfill({json:{ok:true}});
  }
  return route.fulfill({json:p.endsWith('/board')?{items,slots:{},places:[]}:p.endsWith('/messages')?{messages:[]}: {}});
 });
 await page.goto('http://console.test/?lang=en#idle');await page.locator('.row.pinned').first().waitFor();
 return {page,items,requests,setFail:()=>fail=true};
}
const order=page=>page.locator('.row.pinned').evaluateAll(rows=>rows.map(r=>r.dataset.itemId));
for(const width of [1280,390])test(`pins can move with buttons and persist after reload at ${width}px`,async t=>{
 const {page,requests}=await setup(t,width);
 await page.locator('[data-item-id="t102"] .pin-up').click();
 await page.waitForFunction(()=>document.querySelectorAll('.row.pinned')[1]?.dataset.itemId==='t102');
 assert.deepEqual(await order(page),['t100','t102','t101']);assert.equal(requests.length,1);
 assert.equal(await page.locator('#detail').isVisible(),false);
 await page.reload();await page.locator('.pin-up').first().waitFor();
 assert.deepEqual(await order(page),['t100','t102','t101']);
 assert.equal(await page.locator('[data-item-id="t100"] .pin-up').isDisabled(),true);
 assert.equal(await page.locator('[data-item-id="t101"] .pin-down').isDisabled(),true);
 await page.locator('[data-item-id="t100"] .pin-down').focus();await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('.row.pinned')?.dataset.itemId==='t102');
 assert.equal(await page.locator('#detail').isVisible(),false);
 assert.equal(await page.locator(':focus').getAttribute('data-pin-id'),'t100');
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});
test('filtered move preserves hidden pins and server failure preserves order',async t=>{
 const {page,items,setFail,requests}=await setup(t,1280);
 items[0].title="Visible A";items[1].title="Hidden";items[2].title="Visible C";
 await page.evaluate(()=>{state.items[0].title='Visible A';state.items[1].title='Hidden';state.items[2].title='Visible C';state.filter='Visible';render();});
 assert.deepEqual(await order(page),['t100','t102']);
 await page.locator('[data-item-id="t102"] .pin-up').click();
 await page.waitForFunction(()=>document.querySelector('.row.pinned')?.dataset.itemId==='t102');
 assert.deepEqual(requests[0],{target_id:'t100',placement:'before'});
 await page.evaluate(()=>{state.filter='';render();});
 assert.deepEqual(await order(page),['t102','t100','t101']);
 setFail();await page.locator('[data-item-id="t102"] .pin-down').click();
 await page.waitForFunction(()=>document.body.textContent.includes('Pin no longer available'));
 assert.deepEqual(await order(page),['t102','t100','t101']);
});

test('pinned family moves as a unit and new pins lead saved order',async t=>{
 const {page,items}=await setup(t,1280);
 items[1].parent_id='t100';items[1].pinned_at=null;
 await page.reload();await page.locator('[data-item-id="t102"] .pin-up').waitFor();
 await page.locator('[data-item-id="t102"] .pin-up').click();
 await page.waitForFunction(()=>document.querySelector('.row.pinned')?.dataset.itemId==='t102');
 assert.deepEqual(await order(page),['t102','t100']);
 items.push({id:'t103',thread_id:'103',title:'New pin',priority:2,status:'idle',bucket:'idle',pinned_at:'2026-11-12T01:00:00Z',pin_position:null});
 await page.reload();await page.locator('[data-item-id="t103"]').waitFor();
 assert.deepEqual(await order(page),['t103','t102','t100']);
});
