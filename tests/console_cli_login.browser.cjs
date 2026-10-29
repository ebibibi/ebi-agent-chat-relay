// A CLI that is not signed in gets a chip in the usage strip; its dialog shows the
// device code (Codex) or takes the pasted code (Claude) and posts it with the CSRF header.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const CLAUDE_URL='https://claude.com/cai/oauth/authorize?code=true&state=s';
for(const [width,height] of [[1280,800],[390,844]])test(`cli sign-in ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const flows={claude:null,codex:null};const posts=[];
 await page.route('http://console.test/**',async route=>{
  const req=route.request();const name=new URL(req.url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(name==='/console/api/cli-login')return route.fulfill({contentType:'application/json',body:JSON.stringify({backends:[
   {backend:'claude',logged_in:Boolean(flows.claude&&flows.claude.state==='succeeded'),method:null,flow:flows.claude},
   {backend:'codex',logged_in:true,method:'ChatGPT',flow:flows.codex}]})});
  if(name==='/console/api/cli-login/claude'){
   const body=JSON.parse(req.postData());posts.push({body,csrf:req.headers()['x-console-request']});
   if(body.action==='start')flows.claude={backend:'claude',state:'waiting',url:CLAUDE_URL,user_code:null,needs_code:true,message:null,started_at:0,expires_at:Math.floor(Date.now()/1000)+900};
   if(body.action==='code')flows.claude={...flows.claude,state:'succeeded',url:null,needs_code:false,message:'Login successful.'};
   return route.fulfill({contentType:'application/json',body:JSON.stringify({flow:flows.claude})});
  }
  const data=name.endsWith('/board')?{items:[],slots:{},places:[]}:name.endsWith('/usage')?{now:Math.floor(Date.now()/1000),backends:[]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en');
 // Only the CLI that is not signed in gets a chip, and the strip shows even with no usage rows.
 const chip=page.locator('#usage .cl-chip');
 await chip.waitFor();
 assert.equal(await chip.count(),1);
 assert.match(await chip.textContent(),/Claude/);
 await chip.locator('button').click();
 const link=page.locator('dialog.cl-dialog a.cl-link');
 await link.waitFor();
 assert.equal(await link.getAttribute('href'),CLAUDE_URL);
 assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
 await page.locator('dialog .cl-input').fill('abc#state');
 await page.locator('dialog .cl-btn.primary').click();
 await page.locator('dialog .cl-ok').waitFor();
 assert.deepEqual(posts.map(p=>p.body.action),['start','code']);
 assert.equal(posts[1].body.code,'abc#state');
 assert.ok(posts.every(p=>p.csrf==='1'));
 await page.locator('dialog .cl-actions button',{hasText:'Close'}).click();
 assert.equal(await page.locator('dialog.cl-dialog[open]').count(),0);
});
